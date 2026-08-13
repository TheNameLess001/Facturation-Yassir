from __future__ import annotations

from src.auth import Permission, RBACService, User
from src.emails.phase10_models import (
    AuthorizationStatus,
    EmailAutomationMode,
    EmailWorkflowStatus,
)
from src.emails.workflow_repository import EmailWorkflowRepository
from src.models.domain import AuditEvent
from src.models.enums import AuditLevel


class Phase10PeriodLockService:
    def __init__(
        self,
        repository: EmailWorkflowRepository,
        rbac: RBACService | None = None,
    ) -> None:
        self.repository = repository
        self.rbac = rbac or RBACService()

    def lock(
        self,
        *,
        user: User,
        period_code: str,
        intended_send_count: int,
        manual_close: bool,
        reason: str,
        confirmation_text: str,
    ) -> None:
        self.rbac.require(user, Permission.LOCK_PERIOD)
        if confirmation_text != f"LOCK {period_code}":
            raise PermissionError(f"Type LOCK {period_code} exactly")
        if not reason.strip():
            raise ValueError("Lock reason is required")
        sent_count = sum(
            item.status == EmailWorkflowStatus.SENT
            for item in self.repository.list_latest_sends(period_code)
        )
        if not manual_close and intended_send_count <= 0:
            raise ValueError("No intended production sends; manual close approval is required")
        if not manual_close and sent_count != intended_send_count:
            raise ValueError("All intended production sends must complete before lock")
        self.repository.set_period_lock(period_code, True)
        authorization = self.repository.active_authorization(period_code)
        if authorization:
            self.repository.set_authorization_status(
                authorization.authorization_id, AuthorizationStatus.USED
            )
        self._audit(
            "PERIOD_LOCKED",
            user,
            period_code,
            {"reason": reason, "manual_close": manual_close, "sent": sent_count},
        )

    def reopen(
        self,
        *,
        user: User,
        period_code: str,
        reason: str,
        confirmation_text: str,
    ) -> None:
        try:
            self.rbac.require(user, Permission.LOCK_PERIOD)
        except PermissionError:
            self._audit(
                "PERIOD_REOPEN_ATTEMPT_DENIED",
                user,
                period_code,
                {"reason": "INSUFFICIENT_ROLE"},
                level=AuditLevel.WARNING,
            )
            raise
        if confirmation_text != f"REOPEN {period_code}":
            raise PermissionError(f"Type REOPEN {period_code} exactly")
        if not reason.strip():
            raise ValueError("Reopen reason is required")
        if not self.repository.period_locked(period_code):
            raise ValueError("Period is not locked")
        self.repository.set_period_lock(period_code, False)
        authorization = self.repository.latest_authorization(period_code)
        if authorization:
            self.repository.set_authorization_status(
                authorization.authorization_id, AuthorizationStatus.STALE
            )
            self._audit(
                "AUTHORIZATION_STALE",
                user,
                period_code,
                {
                    "authorization_id": str(authorization.authorization_id),
                    "reason": "PERIOD_REOPENED",
                },
                level=AuditLevel.WARNING,
            )
        self.repository.set_period_mode(period_code, EmailAutomationMode.OFF)
        self._audit(
            "PERIOD_REOPENED",
            user,
            period_code,
            {"reason": reason},
            level=AuditLevel.WARNING,
        )

    def _audit(
        self,
        event_type: str,
        user: User,
        period_code: str,
        details: dict[str, object],
        *,
        level: AuditLevel = AuditLevel.INFO,
    ) -> None:
        self.repository.append_audit(
            AuditEvent(
                event_type=event_type,
                level=level,
                actor_id=user.user_id,
                period_id=period_code,
                entity_type="SETTLEMENT_PERIOD",
                entity_id=period_code,
                details={"role": user.role.value, **details},
            )
        )
