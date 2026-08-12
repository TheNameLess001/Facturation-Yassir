from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    AuditLevel,
    AutomationMode,
    DocumentStatus,
    EmailStatus,
    FinancialDecision,
    SourceType,
    WorkflowState,
)


class CashCoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SettlementPeriod(CashCoModel):
    period_id: str
    start_at: datetime
    end_at: datetime
    automation_mode: AutomationMode = AutomationMode.OFF
    locked: bool = False

    @model_validator(mode="after")
    def validate_dates(self) -> SettlementPeriod:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class Restaurant(CashCoModel):
    restaurant_id: str
    restaurant_name: str
    chain: str | None = None
    legal_entity: str | None = None
    ice: str | None = None
    tax_id: str | None = None
    rc: str | None = None
    rib: str | None = None
    bank: str | None = None
    email: str | None = None
    finance_email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    area: str | None = None
    account_manager: str | None = None
    commission_rate: Decimal = Decimal(0)
    partner_status: str | None = None


class Order(CashCoModel):
    order_id: str
    restaurant_id: str
    restaurant_name: str
    chain: str | None = None
    order_date: datetime
    settlement_period: str
    gross_amount: Decimal
    original_status: str
    cancellation_reason: str | None = None
    automatic_settlement_decision: FinancialDecision
    final_settlement_decision: FinancialDecision
    settlement_reason: str
    manual_override: bool = False
    override_reason: str | None = None
    override_comment: str | None = None
    modified_by: str | None = None
    modified_at: datetime | None = None
    source_file_id: str
    source_filename: str
    processed_at: datetime


class RestaurantSettlement(CashCoModel):
    restaurant_id: str
    period_id: str
    gross_sales: Decimal
    commission: Decimal
    adjustments: Decimal = Decimal(0)
    net_payable: Decimal
    state: WorkflowState = WorkflowState.DRAFT
    readiness_score: int = Field(default=0, ge=0, le=100)


class OrderAdjustment(CashCoModel):
    adjustment_id: UUID = Field(default_factory=uuid4)
    order_id: str
    period_id: str
    previous_decision: FinancialDecision
    new_decision: FinancialDecision
    reason: str
    comment: str | None = None
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Document(CashCoModel):
    document_id: UUID = Field(default_factory=uuid4)
    restaurant_id: str
    period_id: str
    document_type: str
    document_number: str
    status: DocumentStatus = DocumentStatus.MISSING
    drive_file_id: str | None = None
    generated_at: datetime | None = None
    content_hash: str | None = None
    financial_hash: str | None = None
    supersedes_document_id: UUID | None = None


class EmailMessage(CashCoModel):
    communication_key: str
    restaurant_id: str
    period_id: str
    communication_type: str = "SETTLEMENT"
    recipient: str
    subject: str
    body: str = ""
    attachment_document_ids: tuple[UUID, ...] = ()
    financial_hash: str | None = None
    status: EmailStatus = EmailStatus.WAITING_ADMIN_AUTHORIZATION
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    sent_at: datetime | None = None


class Payment(CashCoModel):
    payment_id: UUID = Field(default_factory=uuid4)
    restaurant_id: str
    period_id: str
    amount: Decimal
    status: str = "PENDING"
    payment_date: date | None = None
    reference: str | None = None


class AdminAuthorization(CashCoModel):
    confirmation_id: UUID = Field(default_factory=uuid4)
    period_id: str
    automation_mode: AutomationMode
    admin_user: str
    admin_id: str
    authorized_at: datetime
    partners_authorized: int = Field(ge=0)
    settlement_total: Decimal
    authorization_snapshot: tuple[dict[str, Any], ...] = ()
    authorization_hash: str
    status: str = "ACTIVE"


class AuditEvent(CashCoModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    level: AuditLevel = AuditLevel.INFO
    actor_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    period_id: str | None = None
    restaurant_id: str | None = None
    entity_type: str
    entity_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class SourceFileManifest(CashCoModel):
    source_type: SourceType
    drive_file_id: str
    filename: str
    mime_type: str
    modified_at: datetime
    size: int | None = Field(default=None, ge=0)
    checksum: str | None = None
    period_id: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_checked_at: datetime
    status: str
    rows: int | None = Field(default=None, ge=0)
    unique_order_ids: int | None = Field(default=None, ge=0)
    duplicates: int | None = Field(default=None, ge=0)
    imported_at: datetime | None = None
    import_result: str | None = None
