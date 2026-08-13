from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmailAutomationMode(StrEnum):
    OFF = "OFF"
    PREVIEW = "PREVIEW"
    DRAFT = "DRAFT"
    SEND = "SEND"


class RecipientStatus(StrEnum):
    EMAIL_VALID = "EMAIL_VALID"
    EMAIL_MISSING = "EMAIL_MISSING"
    EMAIL_INVALID = "EMAIL_INVALID"


class EmailWorkflowStatus(StrEnum):
    NOT_READY = "NOT_READY"
    BLOCKED = "BLOCKED"
    READY = "READY"
    PREVIEWED = "PREVIEWED"
    DRAFT_READY = "DRAFT_READY"
    AUTHORIZED = "AUTHORIZED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    ALREADY_SENT = "ALREADY_SENT"


class ReadinessState(StrEnum):
    READY_FOR_PREVIEW = "READY_FOR_PREVIEW"
    READY_FOR_DRAFT = "READY_FOR_DRAFT"
    READY_FOR_SEND = "READY_FOR_SEND"
    BLOCKED = "BLOCKED"


class ReadinessBlocker(StrEnum):
    IDENTITY_BLOCKED = "IDENTITY_BLOCKED"
    SETTLEMENT_NOT_READY = "SETTLEMENT_NOT_READY"
    MANUAL_REVIEW_PENDING = "MANUAL_REVIEW_PENDING"
    COMMISSION_BLOCKED = "COMMISSION_BLOCKED"
    INVALID_FINANCIAL_DATA = "INVALID_FINANCIAL_DATA"
    LEGACY_FORMULA_NOT_VALIDATED = "LEGACY_FORMULA_NOT_VALIDATED"
    MISSING_LEGAL_DATA = "MISSING_LEGAL_DATA"
    DOCUMENT_NOT_READY = "DOCUMENT_NOT_READY"
    EMAIL_MISSING = "EMAIL_MISSING"
    EMAIL_INVALID = "EMAIL_INVALID"
    ADMIN_NOT_AUTHORIZED = "ADMIN_NOT_AUTHORIZED"
    AUTHORIZATION_STALE = "AUTHORIZATION_STALE"
    ALREADY_SENT = "ALREADY_SENT"
    PERIOD_LOCKED = "PERIOD_LOCKED"
    PRODUCTION_SEND_DISABLED = "PRODUCTION_SEND_DISABLED"


class AuthorizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    REVOKED = "REVOKED"
    USED = "USED"


class PeriodWorkflowStatus(StrEnum):
    OPEN = "OPEN"
    REVIEW = "REVIEW"
    FINANCIALLY_READY = "FINANCIALLY_READY"
    DOCUMENTS_READY = "DOCUMENTS_READY"
    EMAIL_READY = "EMAIL_READY"
    AUTHORIZED = "AUTHORIZED"
    SENDING = "SENDING"
    SENT = "SENT"
    LOCKED = "LOCKED"


class GmailAuthenticationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class CapabilityStatus(StrEnum):
    YES = "YES"
    NO = "NO"
    NOT_TESTED_SAFELY = "NOT_TESTED_SAFELY"


class DocumentAttachmentRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: str
    document_id: str
    version: int = Field(ge=1)
    content_hash: str
    status: str


class RecipientResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recipient_to: str | None
    recipient_cc: tuple[str, ...] = ()
    status: RecipientStatus
    source_field: str | None = None


class PartnerEmailPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: UUID
    period_code: str
    restaurant_id: str
    restaurant_name: str
    recipient_to: str | None
    recipient_cc: tuple[str, ...] = ()
    subject: str
    body: str
    document_refs: tuple[DocumentAttachmentRef, ...] = ()
    financial_status: str
    document_status: str
    email_status: RecipientStatus
    workflow_status: EmailWorkflowStatus
    package_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    settlement_snapshot_hash: str
    document_snapshot_hash: str
    content_hash: str
    package_hash: str
    send_key: str
    authorization_id: UUID | None = None


class ProductionReadinessResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ReadinessState
    blockers: tuple[ReadinessBlocker, ...]
    ready_for_preview: bool
    ready_for_draft: bool
    ready_for_send: bool


class PeriodAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: UUID
    period_code: str
    mode: EmailAutomationMode
    authorized_by: str
    authorized_at: datetime
    eligible_restaurant_count: int = Field(ge=0)
    blocked_restaurant_count: int = Field(ge=0)
    settlement_snapshot_hash: str
    document_snapshot_hash: str
    email_snapshot_hash: str
    authorization_hash: str
    confirmation_text: str
    status: AuthorizationStatus = AuthorizationStatus.ACTIVE


class SendAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    send_key: str
    period_code: str
    restaurant_id: str
    recipient: str
    package_hash: str
    authorization_id: UUID
    status: EmailWorkflowStatus
    attempted_at: datetime
    provider_message_id: str | None = None
    sent_at: datetime | None = None
    error_code: str | None = None
    actor_id: str


class BatchSendResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempted: int = 0
    sent: int = 0
    failed: int = 0
    not_attempted: int = 0
    already_sent: int = 0
    results: tuple[SendAttempt, ...] = ()


class GmailCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    credentials_detected: bool
    authentication: GmailAuthenticationStatus
    draft_capability: CapabilityStatus
    send_capability: CapabilityStatus
