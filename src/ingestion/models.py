from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.google.models import DriveFile
from src.models.enums import (
    ChangeState,
    ConnectionState,
    HealthState,
    SourceStatus,
    SourceType,
)


class DiscoveredSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    file: DriveFile
    period_id: str | None = None
    source_status: SourceStatus = SourceStatus.FOUND
    change_state: ChangeState = ChangeState.NEW
    health: HealthState = HealthState.HEALTHY
    message: str | None = None


class SourceIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    health: HealthState
    message: str
    period_id: str | None = None


class SourceDiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    connection_state: ConnectionState
    root_name: str | None = None
    admin_earnings_files: tuple[DiscoveredSource, ...] = ()
    payment_scope_files: tuple[DiscoveredSource, ...] = ()
    rst_list_file: DiscoveredSource | None = None
    warnings: tuple[SourceIssue, ...] = ()
    blocking_errors: tuple[SourceIssue, ...] = ()
    last_checked_at: datetime

    @property
    def files(self) -> tuple[DiscoveredSource, ...]:
        rst = (self.rst_list_file,) if self.rst_list_file else ()
        return self.admin_earnings_files + self.payment_scope_files + rst

    @property
    def overall_health(self) -> HealthState:
        if self.blocking_errors:
            return HealthState.BLOCKING
        if self.warnings:
            return HealthState.WARNING
        if self.connection_state != ConnectionState.CONNECTED:
            return HealthState.UNKNOWN
        return HealthState.HEALTHY


class SourceHealthSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    health: HealthState
    file_count: int
    reason: str
