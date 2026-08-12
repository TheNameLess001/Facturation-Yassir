"""LEGACY Payment-Scope restaurant models retained for compatibility."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.ingestion.admin_earnings_models import IngestionIssue
from src.ingestion.payment_scope_models import EligibilityRecord, PaymentScopeSnapshot
from src.models.domain import Restaurant
from src.models.enums import IngestionStatus


class RSTListResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IngestionStatus
    restaurants: tuple[Restaurant, ...] = ()
    rows_read: int = Field(default=0, ge=0)
    source_file_id: str | None = None
    source_filename: str | None = None
    content_hash: str | None = None
    issues: tuple[IngestionIssue, ...] = ()


class EnrichedEligibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligibility: EligibilityRecord
    restaurant: Restaurant | None = None
    enrichment_status: str


class RSTEnrichmentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: IngestionStatus
    period_id: str
    scope_snapshot: PaymentScopeSnapshot | None = None
    records: tuple[EnrichedEligibilityRecord, ...] = ()
    restaurants: tuple[Restaurant, ...] = ()
    missing_restaurant_ids: tuple[str, ...] = ()
    issues: tuple[IngestionIssue, ...] = ()
    completed_at: datetime
