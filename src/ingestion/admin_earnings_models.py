from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import AuditLevel, DuplicateKind, IngestionStatus


class NormalizedAdminEarningsRow(BaseModel):
    """Canonical transaction-source row; it is not a settlement decision."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    order_id: str
    restaurant_id: str
    restaurant_name: str | None = None
    order_date: datetime
    gross_amount: Decimal
    operational_status: str
    cancellation_reason: str | None = None
    source_file_id: str
    source_filename: str
    source_row_number: int = Field(ge=2)
    source_values: tuple[tuple[str, str], ...] = ()

    def comparison_key(self) -> tuple[object, ...]:
        return (
            self.restaurant_id,
            self.restaurant_name,
            self.order_date,
            self.gross_amount,
            self.operational_status,
            self.cancellation_reason,
        )


class IngestionIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: AuditLevel
    code: str
    message: str
    source_file_id: str | None = None
    source_filename: str | None = None
    source_row_number: int | None = None
    field: str | None = None


class DuplicateDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    kind: DuplicateKind
    occurrences: int = Field(ge=2)
    source_locations: tuple[str, ...]


class AdminEarningsFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_file_id: str
    source_filename: str
    rows_read: int = Field(default=0, ge=0)
    rows_valid: int = Field(default=0, ge=0)
    unique_order_ids: int = Field(default=0, ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    detected_columns: dict[str, str] = Field(default_factory=dict)
    issues: tuple[IngestionIssue, ...] = ()


class AdminEarningsIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IngestionStatus
    records: tuple[NormalizedAdminEarningsRow, ...] = ()
    file_results: tuple[AdminEarningsFileResult, ...] = ()
    duplicates: tuple[DuplicateDiagnostic, ...] = ()
    issues: tuple[IngestionIssue, ...] = ()
    started_at: datetime
    completed_at: datetime

    @property
    def rows_read(self) -> int:
        return sum(item.rows_read for item in self.file_results)

    @property
    def conflicting_order_ids(self) -> int:
        return sum(item.kind == DuplicateKind.CONFLICTING for item in self.duplicates)
