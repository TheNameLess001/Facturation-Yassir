from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from src.documents.phase8 import (
    Phase8DocumentEngine,
    ProductionDocumentCandidate,
    ProductionDocumentStatus,
)
from src.google.exceptions import GoogleIntegrationError
from src.google.models import DriveFile


class DocumentPublishMode(StrEnum):
    PREVIEW = "PREVIEW"
    SAMPLE = "SAMPLE"
    PRODUCTION = "PRODUCTION"


class DocumentStorageMode(StrEnum):
    DISABLED = "DISABLED"
    SHARED_DRIVE = "SHARED_DRIVE"
    OAUTH_USER = "OAUTH_USER"


class DriveDestinationType(StrEnum):
    MY_DRIVE = "MY_DRIVE"
    SHARED_DRIVE = "SHARED_DRIVE"
    OTHER = "OTHER"


class DrivePublishingCapability(StrEnum):
    CREATE_AVAILABLE = "CREATE_AVAILABLE"
    CREATE_NOT_AVAILABLE = "CREATE_NOT_AVAILABLE"
    INACCESSIBLE = "INACCESSIBLE"


class DocumentPublicationStatus(StrEnum):
    NOT_PUBLISHED = "NOT_PUBLISHED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    STORAGE_VERIFICATION_FAILED = "STORAGE_VERIFICATION_FAILED"
    ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


class DriveDestinationCapabilityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    folder_id: str | None
    folder_name: str | None
    storage_mode: DocumentStorageMode = DocumentStorageMode.DISABLED
    destination_type: DriveDestinationType
    capability: DrivePublishingCapability
    can_read: bool
    can_create: bool
    can_update: bool
    can_delete: bool | None
    can_list: bool = False
    can_retrieve_metadata: bool = False
    drive_id: str | None = None
    configuration_error: str | None = None


class DriveValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ALREADY_VALIDATED = "ALREADY_VALIDATED"


class DriveValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DriveValidationStatus
    filename: str
    provider_file_id: str | None = None
    created: bool = False
    read_back: bool = False
    metadata_verified: bool = False
    idempotent: bool = False
    error_code: str | None = None
    audit_events: tuple[str, ...] = ()


class DocumentPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: UUID
    publication_key: str
    restaurant_id: str
    period_code: str
    document_type: str
    document_version: int
    document_hash: str
    provider: str
    storage_bucket: str | None = None
    object_key: str | None = None
    etag: str | None = None
    size_bytes: int | None = None
    financial_snapshot_hash: str | None = None
    legal_snapshot_hash: str | None = None
    settlement_snapshot_hash: str | None = None
    financial_policy_version: str | None = None
    content_hash: str | None = None
    created_at: datetime | None = None
    provider_file_id: str | None = None
    provider_folder_id: str | None = None
    published_at: datetime | None = None
    published_by: str
    status: DocumentPublicationStatus
    error_code: str | None = None


class DocumentPublicationBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested: int
    published: int
    already_published: int
    failed: int
    not_published: int
    publications: tuple[DocumentPublication, ...]
    audit_events: tuple[str, ...]


class DocumentDriveProvider(Protocol):
    def get_folder_metadata(self, folder_id: str) -> DriveFile: ...

    def ensure_folder(self, parent_id: str, name: str) -> DriveFile: ...

    def create_file(
        self, folder_id: str, name: str, content: bytes, mime_type: str
    ) -> DriveFile: ...

    def list_files(self, folder_id: str) -> tuple[DriveFile, ...]: ...

    def download_file(self, file_id: str) -> bytes: ...


def inspect_drive_destination(
    provider: DocumentDriveProvider,
    folder_id: str | None,
    *,
    storage_mode: DocumentStorageMode | None = None,
    shared_drive_id: str | None = None,
) -> DriveDestinationCapabilityResult:
    selected_mode = storage_mode
    if selected_mode == DocumentStorageMode.DISABLED:
        return DriveDestinationCapabilityResult(
            folder_id=folder_id,
            folder_name=None,
            storage_mode=selected_mode,
            destination_type=DriveDestinationType.OTHER,
            capability=DrivePublishingCapability.CREATE_NOT_AVAILABLE,
            can_read=False,
            can_create=False,
            can_update=False,
            can_delete=None,
            configuration_error="DOCUMENT_STORAGE_DISABLED",
        )
    if not folder_id:
        return DriveDestinationCapabilityResult(
            folder_id=None,
            folder_name=None,
            storage_mode=selected_mode or DocumentStorageMode.DISABLED,
            destination_type=DriveDestinationType.OTHER,
            capability=DrivePublishingCapability.INACCESSIBLE,
            can_read=False,
            can_create=False,
            can_update=False,
            can_delete=None,
            configuration_error="DOCUMENTS_FOLDER_NOT_CONFIGURED",
        )
    try:
        folder = provider.get_folder_metadata(folder_id)
    except (GoogleIntegrationError, RuntimeError, ValueError, OSError):
        return DriveDestinationCapabilityResult(
            folder_id=folder_id,
            folder_name=None,
            storage_mode=selected_mode or DocumentStorageMode.DISABLED,
            destination_type=DriveDestinationType.OTHER,
            capability=DrivePublishingCapability.INACCESSIBLE,
            can_read=False,
            can_create=False,
            can_update=False,
            can_delete=None,
            configuration_error="DOCUMENT_DESTINATION_INACCESSIBLE",
        )
    destination_type = (
        DriveDestinationType.SHARED_DRIVE
        if folder.drive_id
        else DriveDestinationType.MY_DRIVE
        if "drive" in folder.spaces or not folder.spaces
        else DriveDestinationType.OTHER
    )
    can_create = bool(folder.capabilities.get("canAddChildren"))
    configuration_error = None
    if selected_mode == DocumentStorageMode.SHARED_DRIVE:
        if not folder.drive_id:
            configuration_error = "DESTINATION_IS_NOT_SHARED_DRIVE"
        elif shared_drive_id and folder.drive_id != shared_drive_id:
            configuration_error = "SHARED_DRIVE_ID_MISMATCH"
    if selected_mode == DocumentStorageMode.OAUTH_USER and folder.drive_id:
        # OAuth may access Shared Drive, but the selected mode describes credential
        # authority rather than destination ownership and is therefore accepted.
        configuration_error = None
    effective_create = can_create and configuration_error is None
    return DriveDestinationCapabilityResult(
        folder_id=folder.file_id,
        folder_name=folder.name,
        storage_mode=selected_mode
        or (
            DocumentStorageMode.SHARED_DRIVE
            if destination_type == DriveDestinationType.SHARED_DRIVE
            else DocumentStorageMode.OAUTH_USER
        ),
        destination_type=destination_type,
        capability=(
            DrivePublishingCapability.CREATE_AVAILABLE
            if effective_create
            else DrivePublishingCapability.CREATE_NOT_AVAILABLE
        ),
        can_read=True,
        can_create=effective_create,
        can_update=bool(folder.capabilities.get("canEdit")),
        can_delete=folder.capabilities.get("canDelete"),
        can_list=True,
        can_retrieve_metadata=True,
        drive_id=folder.drive_id,
        configuration_error=configuration_error,
    )


def validate_document_storage_write(
    provider: DocumentDriveProvider,
    destination: DriveDestinationCapabilityResult,
) -> DriveValidationResult:
    """Create/read exactly one synthetic file only in an explicitly enabled mode."""
    filename = "CASHCO_VALIDATION_TEST.txt"
    content = b"CashCo document storage capability validation. No partner data.\n"
    audit = ("DRIVE_CAPABILITY_CHECKED",)
    if (
        destination.storage_mode == DocumentStorageMode.DISABLED
        or not destination.folder_id
        or not destination.can_create
    ):
        return DriveValidationResult(
            status=DriveValidationStatus.NOT_CONFIGURED,
            filename=filename,
            error_code=destination.configuration_error or "CREATE_NOT_AVAILABLE",
            audit_events=audit,
        )
    try:
        matches = tuple(
            item
            for item in provider.list_files(destination.folder_id)
            if item.name == filename
        )
        if len(matches) > 1:
            return DriveValidationResult(
                status=DriveValidationStatus.FAIL,
                filename=filename,
                error_code="DUPLICATE_VALIDATION_FILES",
                audit_events=audit,
            )
        created = False
        if matches:
            item = matches[0]
        else:
            item = provider.create_file(
                destination.folder_id, filename, content, "text/plain"
            )
            created = True
        read_back = provider.download_file(item.file_id) == content
        metadata_ok = bool(item.file_id and item.name == filename)
        if not read_back or not metadata_ok:
            return DriveValidationResult(
                status=DriveValidationStatus.FAIL,
                filename=filename,
                provider_file_id=item.file_id,
                created=created,
                read_back=read_back,
                metadata_verified=metadata_ok,
                error_code="VALIDATION_READBACK_FAILED",
                audit_events=audit,
            )
        return DriveValidationResult(
            status=(
                DriveValidationStatus.PASS
                if created
                else DriveValidationStatus.ALREADY_VALIDATED
            ),
            filename=filename,
            provider_file_id=item.file_id,
            created=created,
            read_back=True,
            metadata_verified=True,
            idempotent=not created,
            audit_events=(
                *audit,
                "DRIVE_VALIDATION_FILE_CREATED"
                if created
                else "DRIVE_VALIDATION_FILE_REUSED",
            ),
        )
    except (GoogleIntegrationError, RuntimeError, ValueError, OSError) as exc:
        return DriveValidationResult(
            status=DriveValidationStatus.FAIL,
            filename=filename,
            error_code=DocumentPublishingService._safe_error(exc),
            audit_events=audit,
        )


def inspect_existing_document_storage_validation(
    provider: DocumentDriveProvider,
    destination: DriveDestinationCapabilityResult,
) -> DriveValidationResult:
    """Read-only check for the synthetic capability artifact."""
    filename = "CASHCO_VALIDATION_TEST.txt"
    content = b"CashCo document storage capability validation. No partner data.\n"
    if (
        destination.storage_mode == DocumentStorageMode.DISABLED
        or not destination.folder_id
    ):
        return DriveValidationResult(
            status=DriveValidationStatus.NOT_CONFIGURED,
            filename=filename,
            error_code=destination.configuration_error or "DOCUMENT_STORAGE_DISABLED",
            audit_events=("DRIVE_CAPABILITY_CHECKED",),
        )
    try:
        matches = tuple(
            item
            for item in provider.list_files(destination.folder_id)
            if item.name == filename
        )
        if len(matches) != 1:
            return DriveValidationResult(
                status=DriveValidationStatus.NOT_CONFIGURED
                if not matches
                else DriveValidationStatus.FAIL,
                filename=filename,
                error_code=(
                    "VALIDATION_FILE_NOT_FOUND"
                    if not matches
                    else "DUPLICATE_VALIDATION_FILES"
                ),
                audit_events=("DRIVE_CAPABILITY_CHECKED",),
            )
        item = matches[0]
        read_back = provider.download_file(item.file_id) == content
        return DriveValidationResult(
            status=(
                DriveValidationStatus.ALREADY_VALIDATED
                if read_back
                else DriveValidationStatus.FAIL
            ),
            filename=filename,
            provider_file_id=item.file_id,
            read_back=read_back,
            metadata_verified=bool(item.file_id),
            idempotent=read_back,
            error_code=None if read_back else "VALIDATION_READBACK_FAILED",
            audit_events=("DRIVE_CAPABILITY_CHECKED",),
        )
    except (GoogleIntegrationError, RuntimeError, ValueError, OSError) as exc:
        return DriveValidationResult(
            status=DriveValidationStatus.FAIL,
            filename=filename,
            error_code=DocumentPublishingService._safe_error(exc),
            audit_events=("DRIVE_CAPABILITY_CHECKED",),
        )


class DocumentPublicationRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_publications (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_document_publication_key "
                "ON document_publications(publication_key)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )

    def append(self, publication: DocumentPublication) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_publications(publication_key, payload) VALUES (?, ?)",
                (publication.publication_key, publication.model_dump_json()),
            )

    def latest(self, publication_key: str) -> DocumentPublication | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM document_publications WHERE publication_key = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (publication_key,),
            ).fetchone()
        return DocumentPublication.model_validate_json(row[0]) if row else None

    def list_for_period(self, period_code: str) -> tuple[DocumentPublication, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_publications ORDER BY sequence"
            ).fetchall()
        return tuple(
            item
            for item in (
                DocumentPublication.model_validate_json(row[0]) for row in rows
            )
            if item.period_code == period_code
        )

    def list_latest_for_period(
        self, period_code: str
    ) -> tuple[DocumentPublication, ...]:
        latest: dict[str, DocumentPublication] = {}
        for item in self.list_for_period(period_code):
            latest[item.publication_key] = item
        return tuple(latest[key] for key in sorted(latest))

    def history(
        self, period_code: str, restaurant_id: str, document_type: str
    ) -> tuple[DocumentPublication, ...]:
        latest: dict[str, DocumentPublication] = {}
        for item in self.list_for_period(period_code):
            if (
                item.restaurant_id == restaurant_id
                and item.document_type == document_type
            ):
                latest[item.publication_key] = item
        return tuple(
            item
            for item in latest.values()
            if item.status
            in {
                DocumentPublicationStatus.PUBLISHED,
                DocumentPublicationStatus.SUPERSEDED,
            }
        )

    def current(
        self, period_code: str, restaurant_id: str, document_type: str
    ) -> DocumentPublication | None:
        items = [
            item
            for item in self.history(period_code, restaurant_id, document_type)
            if item.status == DocumentPublicationStatus.PUBLISHED
        ]
        return max(items, key=lambda item: item.document_version, default=None)

    def supersede(self, publication: DocumentPublication) -> DocumentPublication:
        result = publication.model_copy(
            update={"status": DocumentPublicationStatus.SUPERSEDED}
        )
        self.append(result)
        return result

    def append_document_audit(
        self, event_type: str, details: dict[str, object]
    ) -> None:
        forbidden = {"bytes", "content", "signed_url", "url", "credentials", "token"}
        safe = {
            key: value
            for key, value in details.items()
            if key.casefold() not in forbidden
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_audit_events(event_type, payload, occurred_at) "
                "VALUES (?, ?, ?)",
                (
                    event_type,
                    json.dumps(safe, sort_keys=True, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def list_document_audit(self) -> tuple[tuple[str, dict[str, object]], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, payload FROM document_audit_events ORDER BY sequence"
            ).fetchall()
        return tuple((row[0], json.loads(row[1])) for row in rows)

    def provider_create_denied(self) -> bool:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM document_publications ORDER BY sequence DESC LIMIT 100"
            ).fetchall()
        return any(
            item.error_code == "DRIVEPERMISSIONERROR"
            for item in (
                DocumentPublication.model_validate_json(row[0]) for row in rows
            )
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class DocumentPublishingService:
    """Immutable, idempotent document publication boundary."""

    def __init__(
        self,
        provider: DocumentDriveProvider,
        repository: DocumentPublicationRepository,
        destination: DriveDestinationCapabilityResult,
        *,
        mode: DocumentPublishMode = DocumentPublishMode.PREVIEW,
        actor: str = "cashco.system",
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.destination = destination
        self.mode = mode
        self.actor = actor

    def publish(
        self, candidates: tuple[ProductionDocumentCandidate, ...]
    ) -> DocumentPublicationBatchResult:
        publications = tuple(self._publish_one(item) for item in candidates)
        return DocumentPublicationBatchResult(
            requested=len(publications),
            published=sum(
                item.status == DocumentPublicationStatus.PUBLISHED
                for item in publications
            ),
            already_published=sum(
                item.status == DocumentPublicationStatus.ALREADY_PUBLISHED
                for item in publications
            ),
            failed=sum(
                item.status
                in {
                    DocumentPublicationStatus.FAILED,
                    DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED,
                }
                for item in publications
            ),
            not_published=sum(
                item.status == DocumentPublicationStatus.NOT_PUBLISHED
                for item in publications
            ),
            publications=publications,
            audit_events=tuple(
                {
                    DocumentPublicationStatus.PUBLISHED: "DOCUMENT_PUBLISHED",
                    DocumentPublicationStatus.ALREADY_PUBLISHED: "DOCUMENT_ALREADY_PUBLISHED",
                    DocumentPublicationStatus.FAILED: "DOCUMENT_PUBLISH_FAILED",
                    DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED: "DOCUMENT_PUBLISH_FAILED",
                    DocumentPublicationStatus.NOT_PUBLISHED: "DOCUMENT_RENDERED",
                }[item.status]
                for item in publications
            ),
        )

    def _publish_one(
        self, candidate: ProductionDocumentCandidate
    ) -> DocumentPublication:
        key = self.publication_key(candidate)
        existing = self.repository.latest(key)
        if existing and existing.status == DocumentPublicationStatus.PUBLISHED:
            return existing.model_copy(
                update={"status": DocumentPublicationStatus.ALREADY_PUBLISHED}
            )
        base = DocumentPublication(
            publication_id=uuid5(NAMESPACE_URL, key),
            publication_key=key,
            restaurant_id=candidate.restaurant_id,
            period_code=candidate.period_code,
            document_type=candidate.document_type.value,
            document_version=candidate.document_version,
            document_hash=candidate.document_hash,
            provider="GOOGLE_DRIVE",
            published_by=self.actor,
            status=DocumentPublicationStatus.NOT_PUBLISHED,
        )
        if self.mode == DocumentPublishMode.PREVIEW:
            return base
        if self.mode == DocumentPublishMode.PRODUCTION:
            failed = base.model_copy(
                update={
                    "status": DocumentPublicationStatus.FAILED,
                    "error_code": "PRODUCTION_MODE_NOT_AUTHORIZED",
                }
            )
            self.repository.append(failed)
            return failed
        if not self.destination.can_create or not self.destination.folder_id:
            failed = base.model_copy(
                update={
                    "status": DocumentPublicationStatus.FAILED,
                    "error_code": "CREATE_NOT_AVAILABLE",
                }
            )
            self.repository.append(failed)
            return failed
        if candidate.status != ProductionDocumentStatus.PRODUCTION_READY:
            failed = base.model_copy(
                update={
                    "status": DocumentPublicationStatus.FAILED,
                    "error_code": "DOCUMENT_NOT_PRODUCTION_READY",
                }
            )
            self.repository.append(failed)
            return failed
        rendered = Phase8DocumentEngine.render_production_document(candidate)
        self.repository.append(
            base.model_copy(update={"status": DocumentPublicationStatus.PUBLISHING})
        )
        try:
            folder_id = self._ensure_sample_path(candidate)
            created = self.provider.create_file(
                folder_id,
                f"TEST_CASHCO_{rendered.filename}",
                rendered.content,
                rendered.mime_type,
            )
            published = base.model_copy(
                update={
                    "provider_file_id": created.file_id,
                    "provider_folder_id": folder_id,
                    "published_at": datetime.now(UTC),
                    "status": DocumentPublicationStatus.PUBLISHED,
                }
            )
            self.repository.append(published)
            return published
        except (GoogleIntegrationError, RuntimeError, ValueError, OSError) as exc:
            failed = base.model_copy(
                update={
                    "status": DocumentPublicationStatus.FAILED,
                    "error_code": self._safe_error(exc),
                }
            )
            self.repository.append(failed)
            return failed

    def _ensure_sample_path(self, candidate: ProductionDocumentCandidate) -> str:
        assert self.destination.folder_id is not None
        year, month, half = candidate.period_code.split("-")
        folder = self.provider.ensure_folder(
            self.destination.folder_id, "CashCo_VALIDATION_DRY_RUN"
        )
        for part in (year, month, half, self._safe_name(candidate.restaurant_id)):
            folder = self.provider.ensure_folder(folder.file_id, part)
        return folder.file_id

    @staticmethod
    def publication_key(candidate: ProductionDocumentCandidate) -> str:
        raw = (
            f"{candidate.period_code}|{candidate.restaurant_id}|"
            f"{candidate.document_type.value}|{candidate.document_version}|"
            f"{candidate.document_hash}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "restaurant"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        name = type(exc).__name__.upper()
        return re.sub(r"[^A-Z0-9_]+", "_", name)[:80] or "PROVIDER_ERROR"


class FakeDocumentDriveProvider:
    """In-memory provider used by tests; it has no external side effects."""

    def __init__(
        self, root: DriveFile, *, fail_names: frozenset[str] = frozenset()
    ) -> None:
        self.root = root
        self.fail_names = fail_names
        self.folders: dict[tuple[str, str], DriveFile] = {}
        self.created: list[str] = []
        self.files: dict[str, tuple[DriveFile, bytes]] = {}

    def get_folder_metadata(self, folder_id: str) -> DriveFile:
        return self.root

    def ensure_folder(self, parent_id: str, name: str) -> DriveFile:
        key = (parent_id, name)
        if key not in self.folders:
            folder_id = uuid5(NAMESPACE_URL, f"{parent_id}/{name}").hex
            self.folders[key] = self.root.model_copy(
                update={"file_id": folder_id, "name": name}
            )
        return self.folders[key]

    def create_file(
        self, folder_id: str, name: str, content: bytes, mime_type: str
    ) -> DriveFile:
        if name in self.fail_names:
            raise RuntimeError("FAKE_CREATE_FAILURE")
        self.created.append(name)
        item = self.root.model_copy(
            update={
                "file_id": uuid5(NAMESPACE_URL, f"{folder_id}/{name}").hex,
                "name": name,
                "mime_type": mime_type,
                "is_folder": False,
                "size": len(content),
            }
        )
        self.files[item.file_id] = (item, content)
        return item

    def list_files(self, folder_id: str) -> tuple[DriveFile, ...]:
        return tuple(item for item, _ in self.files.values())

    def download_file(self, file_id: str) -> bytes:
        return self.files[file_id][1]
