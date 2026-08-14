from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from src.google.models import DriveAccessResult, DriveFile
from src.models.enums import ChangeState, ConnectionState, HealthState


class IgnoredFileReason(StrEnum):
    INVALID_FILENAME = "INVALID_FILENAME"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    INVALID_WEEK = "INVALID_WEEK"
    MALFORMED_ADMIN_FILENAME = "MALFORMED_ADMIN_FILENAME"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"


class ReadinessState(StrEnum):
    READY_FOR_INGESTION = "READY_FOR_INGESTION"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTH_ERROR = "AUTH_ERROR"


class AdminEarningsSourceFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    week_number: int
    year: int
    extension: str | None
    mime_type: str
    modified_at: datetime
    size: int | None = None
    checksum: str | None = None
    change_state: ChangeState
    health: HealthState = HealthState.HEALTHY


class IgnoredAdminFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    reason: IgnoredFileReason


class SourceHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    google_connection: HealthState
    admin_earnings: HealthState
    invoice_scope: HealthState
    partner_legal_master: HealthState = HealthState.UNKNOWN
    rst_list: HealthState
    workspace: HealthState
    overall: ReadinessState


class Phase2DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    connection_state: ConnectionState
    valid_admin_files: tuple[AdminEarningsSourceFile, ...] = ()
    ignored_admin_files: tuple[IgnoredAdminFile, ...] = ()
    missing_admin_files: int = 0
    rst_list: DriveFile | None = None
    invoice_scope: DriveFile | None = None
    partner_legal_master: DriveFile | None = None
    access: tuple[DriveAccessResult, ...] = ()
    health: SourceHealth
    last_checked_at: datetime
    message: str | None = None

    @property
    def total_admin_files(self) -> int:
        return len(self.valid_admin_files) + len(self.ignored_admin_files)

    def access_for(self, location: str) -> DriveAccessResult | None:
        return next((item for item in self.access if item.location == location), None)
