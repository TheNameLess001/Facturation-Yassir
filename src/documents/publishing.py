from __future__ import annotations

import hashlib
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
    ALREADY_PUBLISHED = "ALREADY_PUBLISHED"


class DriveDestinationCapabilityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    folder_id: str | None
    folder_name: str | None
    destination_type: DriveDestinationType
    capability: DrivePublishingCapability
    can_read: bool
    can_create: bool
    can_update: bool
    can_delete: bool | None
    drive_id: str | None = None


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


def inspect_drive_destination(
    provider: DocumentDriveProvider, folder_id: str | None
) -> DriveDestinationCapabilityResult:
    if not folder_id:
        return DriveDestinationCapabilityResult(
            folder_id=None,
            folder_name=None,
            destination_type=DriveDestinationType.OTHER,
            capability=DrivePublishingCapability.INACCESSIBLE,
            can_read=False,
            can_create=False,
            can_update=False,
            can_delete=None,
        )
    try:
        folder = provider.get_folder_metadata(folder_id)
    except (GoogleIntegrationError, RuntimeError, ValueError, OSError):
        return DriveDestinationCapabilityResult(
            folder_id=folder_id,
            folder_name=None,
            destination_type=DriveDestinationType.OTHER,
            capability=DrivePublishingCapability.INACCESSIBLE,
            can_read=False,
            can_create=False,
            can_update=False,
            can_delete=None,
        )
    destination_type = (
        DriveDestinationType.SHARED_DRIVE
        if folder.drive_id
        else DriveDestinationType.MY_DRIVE
        if "drive" in folder.spaces or not folder.spaces
        else DriveDestinationType.OTHER
    )
    can_create = bool(folder.capabilities.get("canAddChildren"))
    return DriveDestinationCapabilityResult(
        folder_id=folder.file_id,
        folder_name=folder.name,
        destination_type=destination_type,
        capability=(
            DrivePublishingCapability.CREATE_AVAILABLE
            if can_create
            else DrivePublishingCapability.CREATE_NOT_AVAILABLE
        ),
        can_read=True,
        can_create=can_create,
        can_update=bool(folder.capabilities.get("canEdit")),
        can_delete=folder.capabilities.get("canDelete"),
        drive_id=folder.drive_id,
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
            for item in (DocumentPublication.model_validate_json(row[0]) for row in rows)
            if item.period_code == period_code
        )

    def list_latest_for_period(
        self, period_code: str
    ) -> tuple[DocumentPublication, ...]:
        latest: dict[str, DocumentPublication] = {}
        for item in self.list_for_period(period_code):
            latest[item.publication_key] = item
        return tuple(latest[key] for key in sorted(latest))

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
            published=sum(item.status == DocumentPublicationStatus.PUBLISHED for item in publications),
            already_published=sum(item.status == DocumentPublicationStatus.ALREADY_PUBLISHED for item in publications),
            failed=sum(item.status == DocumentPublicationStatus.FAILED for item in publications),
            not_published=sum(item.status == DocumentPublicationStatus.NOT_PUBLISHED for item in publications),
            publications=publications,
            audit_events=tuple(
                {
                    DocumentPublicationStatus.PUBLISHED: "DOCUMENT_PUBLISHED",
                    DocumentPublicationStatus.ALREADY_PUBLISHED: "DOCUMENT_ALREADY_PUBLISHED",
                    DocumentPublicationStatus.FAILED: "DOCUMENT_PUBLISH_FAILED",
                    DocumentPublicationStatus.NOT_PUBLISHED: "DOCUMENT_RENDERED",
                }[item.status]
                for item in publications
            ),
        )

    def _publish_one(self, candidate: ProductionDocumentCandidate) -> DocumentPublication:
        key = self.publication_key(candidate)
        existing = self.repository.latest(key)
        if existing and existing.status == DocumentPublicationStatus.PUBLISHED:
            return existing.model_copy(update={"status": DocumentPublicationStatus.ALREADY_PUBLISHED})
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

    def __init__(self, root: DriveFile, *, fail_names: frozenset[str] = frozenset()) -> None:
        self.root = root
        self.fail_names = fail_names
        self.folders: dict[tuple[str, str], DriveFile] = {}
        self.created: list[str] = []

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
        return self.root.model_copy(
            update={
                "file_id": uuid5(NAMESPACE_URL, f"{folder_id}/{name}").hex,
                "name": name,
                "mime_type": mime_type,
                "is_folder": False,
                "size": len(content),
            }
        )
