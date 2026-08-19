from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from src.auth import Permission, RBACService, User
from src.documents.legal_readiness import CashCoDocumentType
from src.documents.phase8 import (
    Phase8DocumentEngine,
    ProductionDocumentCandidate,
    ProductionDocumentStatus,
    RenderedDocument,
)
from src.documents.publishing import (
    DocumentPublication,
    DocumentPublicationBatchResult,
    DocumentPublicationRepository,
    DocumentPublicationStatus,
)


class PDFValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class PDFValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: PDFValidationStatus
    page_count: int
    size_bytes: int
    document_hash: str
    issues: tuple[str, ...] = ()


class DocumentGenerationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period_code: str
    eligible_restaurant_ids: tuple[str, ...]
    financial_snapshot_hashes: dict[str, str]
    legal_snapshot_hashes: dict[str, str]
    settlement_snapshot_hashes: dict[str, str]
    source_fingerprint: str
    created_at: datetime

    @classmethod
    def freeze(
        cls,
        period_code: str,
        candidates: tuple[ProductionDocumentCandidate, ...],
        source_fingerprint: str,
    ) -> DocumentGenerationSnapshot:
        ready = tuple(
            item
            for item in candidates
            if item.status == ProductionDocumentStatus.PRODUCTION_READY
        )
        restaurant_ids = tuple(sorted({item.restaurant_id for item in ready}))
        by_restaurant = {item.restaurant_id: item for item in ready}
        return cls(
            period_code=period_code,
            eligible_restaurant_ids=restaurant_ids,
            financial_snapshot_hashes={
                key: by_restaurant[key].financial_snapshot_hash
                for key in restaurant_ids
            },
            legal_snapshot_hashes={
                key: by_restaurant[key].legal_snapshot_hash for key in restaurant_ids
            },
            settlement_snapshot_hashes={
                key: by_restaurant[key].settlement_snapshot_hash
                for key in restaurant_ids
            },
            source_fingerprint=source_fingerprint,
            created_at=datetime.now(UTC),
        )

    def assert_current(self, source_fingerprint: str) -> None:
        if source_fingerprint != self.source_fingerprint:
            raise PermissionError("SOURCE_FINGERPRINT_CHANGED")


class R2ArchiveProvider(Protocol):
    bucket: str

    def put_pdf(
        self, object_key: str, content: bytes, metadata: dict[str, str]
    ) -> str: ...
    def head(self, object_key: str) -> dict[str, object]: ...
    def download(self, object_key: str) -> bytes: ...
    def signed_get_url(self, object_key: str, expiry_seconds: int) -> str: ...


class DocumentAuditSink(Protocol):
    def record(self, event_type: str, details: dict[str, object]) -> None: ...


class NullDocumentAudit:
    def record(self, event_type: str, details: dict[str, object]) -> None:
        return None


class SQLiteDocumentAudit:
    def __init__(self, repository: DocumentPublicationRepository) -> None:
        self.repository = repository

    def record(self, event_type: str, details: dict[str, object]) -> None:
        self.repository.append_document_audit(event_type, details)


def validate_pdf(
    rendered: RenderedDocument, candidate: ProductionDocumentCandidate
) -> PDFValidationResult:
    payload = rendered.content
    searchable = payload.decode("latin-1", errors="ignore")
    financial_fields = {
        CashCoDocumentType.INVOICE: (
            "sales_ttc",
            "invoice_ht",
            "invoice_tva",
            "invoice_ttc",
        ),
        CashCoDocumentType.NOTE_DE_DEBOURS: (
            "sales_ttc",
            "invoice_ttc",
            "final_net_payable",
        ),
        CashCoDocumentType.PARTNER_STATEMENT: (
            "sales_ttc",
            "commission_amount",
            "invoice_tva",
            "invoice_ttc",
            "final_net_payable",
        ),
    }[candidate.document_type]
    expected = (
        candidate.restaurant_id,
        candidate.period_code,
        candidate.document_reference,
        *(str(candidate.content.get(field) or "") for field in financial_fields),
    )
    issues: list[str] = []
    if not payload.startswith(b"%PDF-"):
        issues.append("INVALID_PDF_SIGNATURE")
    if not payload.strip().endswith(b"%%EOF"):
        issues.append("INVALID_PDF_EOF")
    if len(payload) < 500:
        issues.append("PDF_EMPTY_OR_TOO_SMALL")
    page_count = searchable.count("/Type /Page ")
    if page_count < 1:
        issues.append("PDF_PAGE_MISSING")
    for value in expected:
        if value and value not in searchable:
            issues.append("EXPECTED_CONTENT_MISSING")
            break
    forbidden = (
        "Traceback (most recent call last)",
        "PRIVATE KEY",
        "R2_SECRET_ACCESS_KEY",
    )
    if any(value in searchable for value in forbidden):
        issues.append("FORBIDDEN_INTERNAL_CONTENT")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != rendered.document_hash or digest != candidate.document_hash:
        issues.append("DOCUMENT_HASH_MISMATCH")
    return PDFValidationResult(
        status=PDFValidationStatus.PASS if not issues else PDFValidationStatus.FAIL,
        page_count=page_count,
        size_bytes=len(payload),
        document_hash=digest,
        issues=tuple(dict.fromkeys(issues)),
    )


@dataclass(frozen=True)
class DocumentArchiveService:
    provider: R2ArchiveProvider
    repository: DocumentPublicationRepository
    audit: DocumentAuditSink = field(default_factory=NullDocumentAudit)
    actor: str = "cashco.documents"
    max_transient_retries: int = 2

    def publish(
        self, candidates: tuple[ProductionDocumentCandidate, ...]
    ) -> DocumentPublicationBatchResult:
        results = tuple(self._publish_one(candidate) for candidate in candidates)
        return self._batch(results)

    def publish_in_batches(
        self,
        candidates: tuple[ProductionDocumentCandidate, ...],
        *,
        batch_restaurants: int = 15,
    ) -> DocumentPublicationBatchResult:
        if batch_restaurants < 1 or batch_restaurants > 20:
            raise ValueError("BATCH_SIZE_MUST_BE_BETWEEN_1_AND_20")
        grouped: dict[str, list[ProductionDocumentCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.restaurant_id, []).append(candidate)
        results: list[DocumentPublication] = []
        restaurant_ids = sorted(grouped)
        self.audit.record(
            "BULK_DOCUMENT_PUBLICATION_STARTED", {"restaurants": len(restaurant_ids)}
        )
        for start in range(0, len(restaurant_ids), batch_restaurants):
            for restaurant_id in restaurant_ids[start : start + batch_restaurants]:
                results.extend(self.publish(tuple(grouped[restaurant_id])).publications)
        self.audit.record(
            "BULK_DOCUMENT_PUBLICATION_COMPLETED",
            {"restaurants": len(restaurant_ids), "documents": len(results)},
        )
        return self._batch(tuple(results))

    def retry_failed_publications(
        self, candidates: tuple[ProductionDocumentCandidate, ...]
    ) -> DocumentPublicationBatchResult:
        failed = (
            {
                (item.restaurant_id, item.document_type, item.document_hash)
                for item in self.repository.list_latest_for_period(
                    candidates[0].period_code
                )
            if item.status
            in {
                DocumentPublicationStatus.FAILED,
                DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED,
            }
            }
            if candidates
            else set()
        )
        selected = tuple(
            item
            for item in candidates
            if (item.restaurant_id, item.document_type.value, item.document_hash)
            in failed
        )
        return self.publish(selected)

    def _publish_one(
        self, candidate: ProductionDocumentCandidate
    ) -> DocumentPublication:
        self.audit.record(
            "DOCUMENT_GENERATION_STARTED",
            {
                "restaurant_id": candidate.restaurant_id,
                "period_code": candidate.period_code,
                "document_type": candidate.document_type.value,
            },
        )
        identity = (
            candidate.period_code,
            candidate.restaurant_id,
            candidate.document_type.value,
        )
        current = self.repository.current(*identity)
        if current and (
            current.content_hash == candidate.content_hash
            or current.document_hash == candidate.document_hash
        ):
            self.audit.record(
                "DOCUMENT_ALREADY_PUBLISHED",
                {"publication_id": str(current.publication_id)},
            )
            return current.model_copy(
                update={"status": DocumentPublicationStatus.ALREADY_PUBLISHED}
            )
        version = 1 if current is None else current.document_version + 1
        if candidate.document_version != version:
            candidate = candidate.model_copy(update={"document_version": version})
            candidate = candidate.model_copy(
                update={
                    "document_reference": candidate.document_reference.rsplit(":v", 1)[
                        0
                    ]
                    + f":v{version}",
                    "document_hash": "PENDING_RENDER",
                }
            )
            rendered = Phase8DocumentEngine.render_production_document(candidate)
            candidate = candidate.model_copy(
                update={"document_hash": rendered.document_hash}
            )
        else:
            rendered = Phase8DocumentEngine.render_production_document(candidate)
        key = self.publication_key(candidate)
        object_key = self.object_key(candidate)
        base = DocumentPublication(
            publication_id=uuid5(NAMESPACE_URL, key),
            publication_key=key,
            restaurant_id=candidate.restaurant_id,
            period_code=candidate.period_code,
            document_type=candidate.document_type.value,
            document_version=candidate.document_version,
            document_hash=candidate.document_hash,
            provider="R2",
            storage_bucket=self.provider.bucket,
            object_key=object_key,
            financial_snapshot_hash=candidate.financial_snapshot_hash,
            legal_snapshot_hash=candidate.legal_snapshot_hash,
            settlement_snapshot_hash=candidate.settlement_snapshot_hash,
            financial_policy_version=candidate.financial_policy_version,
            content_hash=candidate.content_hash,
            created_at=datetime.now(UTC),
            published_by=self.actor,
            status=DocumentPublicationStatus.NOT_PUBLISHED,
        )
        if candidate.status != ProductionDocumentStatus.PRODUCTION_READY:
            return self._fail(
                base,
                "DOCUMENT_NOT_PRODUCTION_READY",
                event_type="DOCUMENT_GENERATION_FAILED",
            )
        validation = validate_pdf(rendered, candidate)
        if validation.status != PDFValidationStatus.PASS:
            return self._fail(
                base,
                "PDF_VALIDATION_FAILED",
                event_type="DOCUMENT_GENERATION_FAILED",
            )
        self.audit.record(
            "DOCUMENT_GENERATED", {"publication_id": str(base.publication_id)}
        )
        self.repository.append(
            base.model_copy(update={"status": DocumentPublicationStatus.PUBLISHING})
        )
        self.audit.record(
            "DOCUMENT_UPLOAD_STARTED", {"publication_id": str(base.publication_id)}
        )
        metadata = self.safe_metadata(candidate)
        try:
            etag = self._upload_with_retry(object_key, rendered.content, metadata)
            head = self.provider.head(object_key)
            readback = self.provider.download(object_key)
            self._verify_storage(head, readback, rendered, metadata)
        except (
            ConnectionError,
            TimeoutError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            return self._fail(base, self._safe_error(exc))
        if current:
            self.repository.supersede(current)
        published = base.model_copy(
            update={
                "etag": etag,
                "size_bytes": len(rendered.content),
                "published_at": datetime.now(UTC),
                "status": DocumentPublicationStatus.PUBLISHED,
            }
        )
        self.repository.append(published)
        self.audit.record(
            "DOCUMENT_UPLOADED", {"publication_id": str(base.publication_id)}
        )
        self.audit.record(
            "DOCUMENT_STORAGE_VERIFIED", {"publication_id": str(base.publication_id)}
        )
        if version > 1:
            self.audit.record(
                "DOCUMENT_VERSION_CREATED",
                {"publication_id": str(base.publication_id), "version": version},
            )
        return published

    def _upload_with_retry(
        self, object_key: str, content: bytes, metadata: dict[str, str]
    ) -> str:
        for attempt in range(self.max_transient_retries + 1):
            try:
                return self.provider.put_pdf(object_key, content, metadata)
            except (ConnectionError, TimeoutError, OSError):
                if attempt >= self.max_transient_retries:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("UPLOAD_RETRY_EXHAUSTED")

    @staticmethod
    def _verify_storage(
        head: dict[str, object],
        readback: bytes,
        rendered: RenderedDocument,
        metadata: dict[str, str],
    ) -> None:
        stored_metadata = head.get("metadata", {})
        valid = (
            head.get("content_type") == "application/pdf"
            and head.get("size_bytes") == len(rendered.content)
            and readback == rendered.content
            and hashlib.sha256(readback).hexdigest() == rendered.document_hash
            and isinstance(stored_metadata, dict)
            and all(
                stored_metadata.get(key) == value for key, value in metadata.items()
            )
        )
        if not valid:
            raise ValueError("STORAGE_VERIFICATION_FAILED")

    @staticmethod
    def object_key(candidate: ProductionDocumentCandidate) -> str:
        year, month, half = candidate.period_code.split("-")
        folder, filename = {
            CashCoDocumentType.INVOICE: ("invoice", "invoice"),
            CashCoDocumentType.NOTE_DE_DEBOURS: ("debours", "note_debours"),
            CashCoDocumentType.PARTNER_STATEMENT: ("statement", "statement"),
        }[candidate.document_type]
        return f"{year}/{month}/{half}/{candidate.restaurant_id}/{folder}/{filename}_v{candidate.document_version}.pdf"

    @staticmethod
    def safe_metadata(candidate: ProductionDocumentCandidate) -> dict[str, str]:
        return {
            "restaurant-id": candidate.restaurant_id,
            "period-code": candidate.period_code,
            "document-type": candidate.document_type.value,
            "document-version": str(candidate.document_version),
            "document-hash": candidate.document_hash,
            "financial-policy-version": candidate.financial_policy_version,
        }

    @staticmethod
    def publication_key(candidate: ProductionDocumentCandidate) -> str:
        return hashlib.sha256(
            f"{candidate.period_code}|{candidate.restaurant_id}|{candidate.document_type.value}|{candidate.document_version}|{candidate.document_hash}".encode()
        ).hexdigest()

    def _fail(
        self,
        base: DocumentPublication,
        error: str,
        *,
        event_type: str = "DOCUMENT_UPLOAD_FAILED",
    ) -> DocumentPublication:
        status = (
            DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED
            if error == "STORAGE_VERIFICATION_FAILED"
            else DocumentPublicationStatus.FAILED
        )
        failed = base.model_copy(update={"status": status, "error_code": error})
        self.repository.append(failed)
        self.audit.record(
            event_type,
            {"publication_id": str(base.publication_id), "error_code": error},
        )
        return failed

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return type(exc).__name__.upper() if str(exc) == "" else str(exc)[:80]

    @staticmethod
    def _batch(
        results: tuple[DocumentPublication, ...],
    ) -> DocumentPublicationBatchResult:
        return DocumentPublicationBatchResult(
            requested=len(results),
            published=sum(
                item.status == DocumentPublicationStatus.PUBLISHED for item in results
            ),
            already_published=sum(
                item.status == DocumentPublicationStatus.ALREADY_PUBLISHED
                for item in results
            ),
            failed=sum(
                item.status
                in {
                    DocumentPublicationStatus.FAILED,
                    DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED,
                }
                for item in results
            ),
            not_published=sum(
                item.status == DocumentPublicationStatus.NOT_PUBLISHED
                for item in results
            ),
            publications=results,
            audit_events=tuple(item.status.value for item in results),
        )


class SecureDocumentAccessService:
    def __init__(
        self,
        provider: R2ArchiveProvider,
        repository: DocumentPublicationRepository,
        *,
        expiry_seconds: int = 300,
        rbac: RBACService | None = None,
        audit: DocumentAuditSink | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.expiry_seconds = expiry_seconds
        self.rbac = rbac or RBACService()
        self.audit = audit or NullDocumentAudit()

    def view_url(self, user: User, publication: DocumentPublication) -> str:
        self._authorize(user, publication)
        assert publication.object_key
        url = self.provider.signed_get_url(publication.object_key, self.expiry_seconds)
        self.audit.record(
            "DOCUMENT_VIEW_REQUESTED",
            {"publication_id": str(publication.publication_id)},
        )
        return url

    def download(self, user: User, publication: DocumentPublication) -> bytes:
        self._authorize(user, publication)
        assert publication.object_key
        return self.provider.download(publication.object_key)

    def _authorize(self, user: User, publication: DocumentPublication) -> None:
        self.rbac.require(user, Permission.VIEW)
        if (
            publication.status
            not in {
                DocumentPublicationStatus.PUBLISHED,
                DocumentPublicationStatus.ALREADY_PUBLISHED,
                DocumentPublicationStatus.SUPERSEDED,
            }
            or publication.provider != "R2"
            or not publication.object_key
        ):
            raise PermissionError("DOCUMENT_NOT_VIEWABLE")
