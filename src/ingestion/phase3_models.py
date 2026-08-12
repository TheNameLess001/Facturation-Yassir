from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Phase3Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DuplicateClassification(StrEnum):
    UNIQUE = "UNIQUE"
    IDENTICAL_DUPLICATE = "IDENTICAL_DUPLICATE"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    MISSING_ORDER_ID = "MISSING_ORDER_ID"


class IssueSeverity(StrEnum):
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class SourceOccurrence(Phase3Model):
    source_file_id: str
    source_filename: str
    source_week: int
    source_year: int
    source_modified_at: datetime
    source_row_number: int


class CanonicalAdminOrder(Phase3Model):
    order_id: str
    restaurant_id: str | None = None
    restaurant_name: str | None = None
    order_created_at: datetime | None = None
    order_date: date | None = None
    original_order_timestamp: str | None = None
    operational_status: str | None = None
    cancellation_reason: str | None = None
    item_total: Decimal | None = None
    subtotal: Decimal | None = None
    gross_amount: Decimal | None = None
    discount: Decimal | None = None
    promo_amount: Decimal | None = None
    delivery_fee: Decimal | None = None
    commission_amount: Decimal | None = None
    commission_rate: Decimal | None = None
    currency: str | None = None
    lineage: tuple[SourceOccurrence, ...]
    ingested_at: datetime
    raw_extra: dict[str, Any] = Field(default_factory=dict)

    def material_payload(self) -> tuple[object, ...]:
        return (
            self.restaurant_id,
            self.order_date,
            self.operational_status,
            self.cancellation_reason,
            self.item_total,
            self.subtotal,
            self.gross_amount,
            self.discount,
            self.promo_amount,
            self.delivery_fee,
            self.commission_amount,
            self.commission_rate,
            self.currency,
        )


class IngestionIssueRecord(Phase3Model):
    category: str
    severity: IssueSeverity
    message: str
    occurrence: SourceOccurrence | None = None
    order_id: str | None = None
    field: str | None = None
    raw_value: str | None = None


class DuplicateOccurrence(Phase3Model):
    order_id: str
    classification: DuplicateClassification
    retained: bool
    occurrence: SourceOccurrence


class DuplicateConflict(Phase3Model):
    order_id: str
    conflicting_fields: tuple[str, ...]
    values_by_occurrence: tuple[dict[str, Any], ...]
    occurrences: tuple[SourceOccurrence, ...]
    detected_at: datetime
    severity: IssueSeverity = IssueSeverity.BLOCKING
    destination: str = "REVIEW_QUEUE"


class SchemaProfile(Phase3Model):
    signature: str
    normalized_columns: tuple[str, ...]
    source_columns: tuple[str, ...]
    files: tuple[str, ...]
    row_count: int
    canonical_mapping: dict[str, str]
    missing_critical_fields: tuple[str, ...] = ()
    unexpected_columns: tuple[str, ...] = ()
    ambiguous_mappings: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class SourceIngestionResult(Phase3Model):
    file_id: str
    filename: str
    week: int
    year: int
    modified_at: datetime
    checksum: str | None = None
    rows_read: int = 0
    rows_valid: int = 0
    rows_with_issues: int = 0
    unique_order_ids: int = 0
    duplicate_occurrences: int = 0
    ingestion_started_at: datetime
    ingestion_completed_at: datetime
    status: str


class IngestionRunSummary(Phase3Model):
    run_id: str
    started_at: datetime
    completed_at: datetime
    sources_selected: int
    sources_read: int
    source_failures: int
    raw_rows: int
    canonical_orders: int
    identical_duplicate_rows: int
    conflicting_order_ids: int
    missing_order_id_rows: int
    invalid_dates: int
    invalid_financial_values: int
    schema_warnings: int
    schema_variants: int
    blocking_issues: int
    publish_status: str
    min_order_date: date | None = None
    max_order_date: date | None = None


class Phase3Result(Phase3Model):
    summary: IngestionRunSummary
    canonical_orders: tuple[CanonicalAdminOrder, ...] = ()
    duplicate_occurrences: tuple[DuplicateOccurrence, ...] = ()
    conflicts: tuple[DuplicateConflict, ...] = ()
    issues: tuple[IngestionIssueRecord, ...] = ()
    schema_profiles: tuple[SchemaProfile, ...] = ()
    source_results: tuple[SourceIngestionResult, ...] = ()
    artifacts: tuple[str, ...] = ()
