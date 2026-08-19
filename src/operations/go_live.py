from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class GoLiveStatus(StrEnum):
    READY_FOR_CANARY_AUTHORIZATION = "READY_FOR_CANARY_AUTHORIZATION"
    # Compatibility name for callers from Activation 5.
    READY_FOR_GO_LIVE_AUTHORIZATION = "READY_FOR_CANARY_AUTHORIZATION"
    BLOCKED = "BLOCKED"


class GoLiveBlocker(StrEnum):
    FINANCIAL_POLICY_NOT_READY = "FINANCIAL_POLICY_NOT_READY"
    LEGAL_MASTER_NOT_CONNECTED = "LEGAL_MASTER_NOT_CONNECTED"
    SETTLEMENT_ENGINE_NOT_READY = "SETTLEMENT_ENGINE_NOT_READY"
    DOCUMENT_RENDERING_NOT_READY = "DOCUMENT_RENDERING_NOT_READY"
    DOCUMENT_STORAGE_NOT_READY = "DOCUMENT_STORAGE_NOT_READY"
    EMAIL_PACKAGES_NOT_READY = "EMAIL_PACKAGES_NOT_READY"
    GMAIL_AUTH_NOT_READY = "GMAIL_AUTH_NOT_READY"
    GMAIL_SENDER_NOT_READY = "GMAIL_SENDER_NOT_READY"
    SANDBOX_VALIDATION_NOT_READY = "SANDBOX_VALIDATION_NOT_READY"
    ADMIN_WORKFLOW_NOT_READY = "ADMIN_WORKFLOW_NOT_READY"
    IDEMPOTENCY_NOT_READY = "IDEMPOTENCY_NOT_READY"
    AUDIT_NOT_READY = "AUDIT_NOT_READY"
    PRODUCTION_FLAG_UNSAFELY_ENABLED = "PRODUCTION_FLAG_UNSAFELY_ENABLED"


class GoLiveReadinessInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    financial_policy_ready: bool
    partner_legal_master_connected: bool
    settlement_engine_ready: bool
    document_rendering_ready: bool
    document_storage_ready: bool
    email_package_ready: bool
    gmail_auth_ready: bool
    gmail_sender_ready: bool
    sandbox_validation_ready: bool
    admin_workflow_ready: bool
    idempotency_ready: bool
    audit_ready: bool
    production_send_enabled: bool


class GoLiveReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GoLiveStatus
    blockers: tuple[GoLiveBlocker, ...]
    evaluated_at: datetime
    production_send_flag: str
    admin_production_authorized: bool = False
    audit_event: str = "GO_LIVE_READINESS_EVALUATED"


class GoLiveReadinessPolicy:
    CHECKS = (
        ("financial_policy_ready", GoLiveBlocker.FINANCIAL_POLICY_NOT_READY),
        (
            "partner_legal_master_connected",
            GoLiveBlocker.LEGAL_MASTER_NOT_CONNECTED,
        ),
        ("settlement_engine_ready", GoLiveBlocker.SETTLEMENT_ENGINE_NOT_READY),
        (
            "document_rendering_ready",
            GoLiveBlocker.DOCUMENT_RENDERING_NOT_READY,
        ),
        ("document_storage_ready", GoLiveBlocker.DOCUMENT_STORAGE_NOT_READY),
        ("email_package_ready", GoLiveBlocker.EMAIL_PACKAGES_NOT_READY),
        ("gmail_auth_ready", GoLiveBlocker.GMAIL_AUTH_NOT_READY),
        ("gmail_sender_ready", GoLiveBlocker.GMAIL_SENDER_NOT_READY),
        (
            "sandbox_validation_ready",
            GoLiveBlocker.SANDBOX_VALIDATION_NOT_READY,
        ),
        ("admin_workflow_ready", GoLiveBlocker.ADMIN_WORKFLOW_NOT_READY),
        ("idempotency_ready", GoLiveBlocker.IDEMPOTENCY_NOT_READY),
        ("audit_ready", GoLiveBlocker.AUDIT_NOT_READY),
    )

    def evaluate(self, value: GoLiveReadinessInput) -> GoLiveReadiness:
        blockers = tuple(
            blocker for field, blocker in self.CHECKS if not getattr(value, field)
        )
        if value.production_send_enabled:
            blockers = (*blockers, GoLiveBlocker.PRODUCTION_FLAG_UNSAFELY_ENABLED)
        return GoLiveReadiness(
            status=(
                GoLiveStatus.READY_FOR_CANARY_AUTHORIZATION
                if not blockers
                else GoLiveStatus.BLOCKED
            ),
            blockers=blockers,
            evaluated_at=datetime.now(UTC),
            production_send_flag="ON" if value.production_send_enabled else "OFF",
        )
