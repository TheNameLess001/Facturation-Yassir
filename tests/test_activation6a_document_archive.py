from __future__ import annotations

import hashlib

import pytest

from src.auth import User
from src.documents.archive import (
    DocumentArchiveService,
    DocumentGenerationSnapshot,
    PDFValidationStatus,
    SecureDocumentAccessService,
    SQLiteDocumentAudit,
    validate_pdf,
)
from src.documents.legal_readiness import CashCoDocumentType, DocumentLegalStatus
from src.documents.phase8 import (
    Phase8DocumentEngine,
    ProductionDocumentCandidate,
    ProductionDocumentStatus,
)
from src.documents.publishing import (
    DocumentPublicationRepository,
    DocumentPublicationStatus,
)
from src.models.enums import Role


def candidate(
    kind: CashCoDocumentType = CashCoDocumentType.INVOICE,
    *,
    restaurant_id: str = "R-001",
    content_change: str = "",
) -> ProductionDocumentCandidate:
    content = {
        "partner": f"Restaurant Test{content_change}",
        "address": "1 Approved Address",
        "ice": "123456789012345",
        "if": None,
        "rc": None,
        "commission": "20.00",
        "sales_ttc": "120.00",
        "sales_ht": "100.00",
        "commission_amount": "20.00",
        "invoice_ht": "20.00",
        "invoice_tva": "4.00",
        "invoice_ttc": "24.00",
        "note_de_debours": "96.00",
        "final_net_payable": "96.00",
    }
    base = ProductionDocumentCandidate(
        restaurant_id=restaurant_id,
        period_code="2026-07-P2",
        document_type=kind,
        document_version=1,
        document_reference=f"{restaurant_id}:2026-07-P2:{kind.value}:v1",
        financial_policy_version="cashco_legacy_v1",
        settlement_snapshot_hash="settlement-hash",
        financial_snapshot_hash="financial-hash",
        legal_snapshot_hash=hashlib.sha256(content["partner"].encode()).hexdigest(),
        content_hash=hashlib.sha256(repr(sorted(content.items())).encode()).hexdigest(),
        document_hash="PENDING_RENDER",
        legal_status=DocumentLegalStatus.READY,
        status=ProductionDocumentStatus.PRODUCTION_READY,
        content=content,
    )
    rendered = Phase8DocumentEngine.render_production_document(base)
    return base.model_copy(update={"document_hash": rendered.document_hash})


class FakeR2:
    bucket = "cashco-documents"

    def __init__(self, *, fail_kind: str | None = None, corrupt_readback: bool = False):
        self.fail_kind = fail_kind
        self.corrupt_readback = corrupt_readback
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.put_calls = 0
        self.signed_expiry = None

    def put_pdf(self, object_key, content, metadata):
        if self.fail_kind and f"/{self.fail_kind}/" in object_key:
            raise RuntimeError("R2_UPLOAD_FAILED")
        self.put_calls += 1
        self.objects[object_key] = (content, metadata)
        return "etag"

    def head(self, object_key):
        content, metadata = self.objects[object_key]
        return {
            "content_type": "application/pdf",
            "size_bytes": len(content),
            "etag": "etag",
            "metadata": metadata,
        }

    def download(self, object_key):
        content = self.objects[object_key][0]
        return content + b"corrupt" if self.corrupt_readback else content

    def signed_get_url(self, object_key, expiry_seconds):
        self.signed_expiry = expiry_seconds
        return f"https://signed.invalid/{object_key}?redacted=true"


def test_pdf_generation_validity_identity_reference_financials_and_hash() -> None:
    item = candidate()
    rendered = Phase8DocumentEngine.render_production_document(item)
    validation = validate_pdf(rendered, item)
    text = rendered.content.decode("latin-1")
    assert rendered.mime_type == "application/pdf"
    assert rendered.filename.endswith(".pdf")
    assert validation.status == PDFValidationStatus.PASS
    assert validation.page_count == 1
    assert item.document_reference in text
    assert "20.00 MAD" in text and "24.00 MAD" in text
    assert hashlib.sha256(rendered.content).hexdigest() == item.document_hash


@pytest.mark.parametrize(
    ("kind", "fragment"),
    [
        (CashCoDocumentType.INVOICE, "/invoice/invoice_v1.pdf"),
        (CashCoDocumentType.NOTE_DE_DEBOURS, "/debours/note_debours_v1.pdf"),
        (CashCoDocumentType.PARTNER_STATEMENT, "/statement/statement_v1.pdf"),
    ],
)
def test_deterministic_r2_object_keys(kind, fragment) -> None:
    assert DocumentArchiveService.object_key(candidate(kind)).endswith(fragment)


def test_safe_r2_metadata_excludes_legal_contact_and_secrets() -> None:
    metadata = DocumentArchiveService.safe_metadata(candidate())
    assert set(metadata) == {
        "restaurant-id",
        "period-code",
        "document-type",
        "document-version",
        "document-hash",
        "financial-policy-version",
    }
    assert not {"rib", "email", "ice", "secret"}.intersection(metadata)


def test_publication_readback_idempotency_and_version_history(tmp_path) -> None:
    provider = FakeR2()
    repository = DocumentPublicationRepository(tmp_path / "archive.sqlite3")
    service = DocumentArchiveService(provider, repository)
    first = service.publish((candidate(),))
    second = service.publish((candidate(),))
    changed = service.publish((candidate(content_change=" v2"),))
    history = repository.history("2026-07-P2", "R-001", "INVOICE")
    assert first.published == 1
    assert second.already_published == 1
    assert changed.published == 1
    assert provider.put_calls == 2
    assert {item.document_version for item in history} == {1, 2}
    assert repository.current("2026-07-P2", "R-001", "INVOICE").document_version == 2
    assert any(item.status == DocumentPublicationStatus.SUPERSEDED for item in history)


def test_partial_failure_and_retry_only_failed(tmp_path) -> None:
    provider = FakeR2(fail_kind="debours")
    repository = DocumentPublicationRepository(tmp_path / "archive.sqlite3")
    service = DocumentArchiveService(provider, repository, max_transient_retries=0)
    items = tuple(candidate(kind) for kind in CashCoDocumentType)
    first = service.publish(items)
    assert first.published == 2 and first.failed == 1
    provider.fail_kind = None
    retry = service.retry_failed_publications(items)
    assert retry.requested == 1 and retry.published == 1


def test_storage_hash_mismatch_never_marks_published(tmp_path) -> None:
    result = DocumentArchiveService(
        FakeR2(corrupt_readback=True),
        DocumentPublicationRepository(tmp_path / "archive.sqlite3"),
    ).publish((candidate(),))
    assert result.failed == 1
    assert result.publications[0].error_code == "STORAGE_VERIFICATION_FAILED"


def test_generation_snapshot_is_frozen_and_detects_source_change() -> None:
    items = tuple(candidate(kind) for kind in CashCoDocumentType)
    snapshot = DocumentGenerationSnapshot.freeze("2026-07-P2", items, "source-a")
    assert snapshot.eligible_restaurant_ids == ("R-001",)
    snapshot.assert_current("source-a")
    with pytest.raises(PermissionError, match="SOURCE_FINGERPRINT_CHANGED"):
        snapshot.assert_current("source-b")


def test_runtime_timestamps_do_not_change_document_snapshot_identity() -> None:
    engine = Phase8DocumentEngine()
    first = {
        "amount": "120.00",
        "orders": [{"order_id": "O1", "decision_trace": {"created_at": "a"}}],
    }
    second = {
        "amount": "120.00",
        "orders": [{"order_id": "O1", "decision_trace": {"created_at": "b"}}],
    }
    assert engine._stable_hash(
        engine._without_runtime_timestamps(first)
    ) == engine._stable_hash(engine._without_runtime_timestamps(second))


def test_secure_view_and_download_require_rbac_and_use_300_seconds(tmp_path) -> None:
    provider = FakeR2()
    repository = DocumentPublicationRepository(tmp_path / "archive.sqlite3")
    publication = (
        DocumentArchiveService(provider, repository)
        .publish((candidate(),))
        .publications[0]
    )
    access = SecureDocumentAccessService(provider, repository, expiry_seconds=300)
    viewer = User("viewer", "Viewer", "viewer@example.test", Role.VIEWER)
    url = access.view_url(viewer, publication)
    assert "signed.invalid" in url and provider.signed_expiry == 300
    assert access.download(viewer, publication).startswith(b"%PDF-")
    already = publication.model_copy(
        update={"status": DocumentPublicationStatus.ALREADY_PUBLISHED}
    )
    assert access.view_url(viewer, already).startswith("https://signed.invalid/")
    invalid = publication.model_copy(
        update={"status": DocumentPublicationStatus.FAILED}
    )
    with pytest.raises(PermissionError, match="DOCUMENT_NOT_VIEWABLE"):
        access.view_url(viewer, invalid)


def test_canary_and_controlled_bulk_are_batched_without_gmail(tmp_path) -> None:
    provider = FakeR2()
    service = DocumentArchiveService(
        provider, DocumentPublicationRepository(tmp_path / "archive.sqlite3")
    )
    items = tuple(
        candidate(kind, restaurant_id=f"R-{restaurant}")
        for restaurant in range(1, 5)
        for kind in CashCoDocumentType
    )
    canary = service.publish(
        tuple(item for item in items if item.restaurant_id in {"R-1", "R-2", "R-3"})
    )
    assert canary.failed == 0 and canary.published == 9
    bulk = service.publish_in_batches(items, batch_restaurants=2)
    assert bulk.failed == 0
    assert bulk.published == 3
    assert bulk.already_published == 9


def test_document_audit_is_persistent_and_never_stores_signed_url(tmp_path) -> None:
    provider = FakeR2()
    repository = DocumentPublicationRepository(tmp_path / "archive.sqlite3")
    audit = SQLiteDocumentAudit(repository)
    publication = (
        DocumentArchiveService(provider, repository, audit=audit)
        .publish((candidate(),))
        .publications[0]
    )
    viewer = User("viewer", "Viewer", "viewer@example.test", Role.VIEWER)
    SecureDocumentAccessService(provider, repository, audit=audit).view_url(
        viewer, publication
    )
    events = repository.list_document_audit()
    assert "DOCUMENT_GENERATION_STARTED" in {item[0] for item in events}
    assert "DOCUMENT_STORAGE_VERIFIED" in {item[0] for item in events}
    assert "DOCUMENT_VIEW_REQUESTED" in {item[0] for item in events}
    assert "signed.invalid" not in repr(events)
