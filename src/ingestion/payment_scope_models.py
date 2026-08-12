from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.ingestion.admin_earnings_models import (
    IngestionIssue,
    NormalizedAdminEarningsRow,
)
from src.models.enums import EligibilityState, IngestionStatus


class PaymentScopeEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    restaurant_id: str
    restaurant_name: str | None = None
    source_row_number: int = Field(ge=2)
    source_values: tuple[tuple[str, str], ...] = ()


class PaymentScopeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    period_id: str
    drive_file_id: str
    filename: str
    drive_modified_at: datetime
    drive_checksum: str | None = None
    content_hash: str
    snapshot_at: datetime
    restaurant_ids: tuple[str, ...]

    @property
    def restaurant_count(self) -> int:
        return len(self.restaurant_ids)


class PaymentScopeIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IngestionStatus
    period_id: str
    entries: tuple[PaymentScopeEntry, ...] = ()
    snapshot: PaymentScopeSnapshot | None = None
    rows_read: int = Field(default=0, ge=0)
    duplicate_restaurant_ids: tuple[str, ...] = ()
    issues: tuple[IngestionIssue, ...] = ()


class EligibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: NormalizedAdminEarningsRow
    state: EligibilityState
    reason: str


class EligibilityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IngestionStatus
    period_id: str
    scope_snapshot: PaymentScopeSnapshot | None = None
    eligible_orders: tuple[EligibilityRecord, ...] = ()
    out_of_scope_orders: tuple[EligibilityRecord, ...] = ()
    issues: tuple[IngestionIssue, ...] = ()

    @property
    def eligible_restaurant_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.order.restaurant_id for item in self.eligible_orders})
        )

    @property
    def out_of_scope_restaurant_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.order.restaurant_id for item in self.out_of_scope_orders})
        )
