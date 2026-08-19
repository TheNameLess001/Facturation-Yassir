from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.auth import Permission, RBACService, User
from src.emails.packages import stable_hash
from src.emails.phase10_models import (
    AuthorizationStatus,
    EmailAutomationMode,
    PartnerEmailPackage,
    PeriodAuthorization,
)
from src.emails.workflow_repository import EmailWorkflowRepository
from src.models.domain import AuditEvent


def package_snapshot_hashes(
    packages: tuple[PartnerEmailPackage, ...],
) -> tuple[str, str, str]:
    ordered = sorted(packages, key=lambda item: (item.restaurant_id, item.package_hash))
    return (
        stable_hash([item.settlement_snapshot_hash for item in ordered]),
        stable_hash([item.document_snapshot_hash for item in ordered]),
        stable_hash([item.content_hash for item in ordered]),
    )


class PeriodAuthorizationService:
    def __init__(
        self,
        repository: EmailWorkflowRepository,
        rbac: RBACService | None = None,
    ) -> None:
        self.repository = repository
        self.rbac = rbac or RBACService()

    def set_safe_mode(
        self,
        *,
        user: User,
        period_code: str,
        mode: EmailAutomationMode,
    ) -> None:
        self.rbac.require(user, Permission.AUTHORIZE_AUTOMATION)
        if mode not in {EmailAutomationMode.OFF, EmailAutomationMode.PREVIEW}:
            raise ValueError("DRAFT and SEND modes require snapshot authorization")
        if self.repository.period_locked(period_code):
            raise PermissionError("PERIOD_LOCKED")
        self.repository.set_period_mode(period_code, mode)
        self.repository.append_audit(
            AuditEvent(
                event_type="AUTOMATION_MODE_CHANGED",
                actor_id=user.user_id,
                period_id=period_code,
                entity_type="SETTLEMENT_PERIOD",
                entity_id=period_code,
                details={"role": user.role.value, "mode": mode.value},
            )
        )

    def authorize(
        self,
        *,
        user: User,
        period_code: str,
        mode: EmailAutomationMode,
        packages: tuple[PartnerEmailPackage, ...],
        eligible_restaurant_count: int,
        blocked_restaurant_count: int,
        confirmation_text: str,
        now: datetime | None = None,
    ) -> PeriodAuthorization:
        self.rbac.require(user, Permission.AUTHORIZE_AUTOMATION)
        if self.repository.period_locked(period_code):
            raise PermissionError("PERIOD_LOCKED")
        if mode == EmailAutomationMode.OFF:
            raise ValueError("OFF does not create an authorization")
        expected = (
            f"SEND CASHCO {period_code}"
            if mode == EmailAutomationMode.SEND
            else f"AUTHORIZE {period_code}"
        )
        if confirmation_text != expected:
            raise PermissionError(f"Typed confirmation must match {expected} exactly")
        settlement_hash, document_hash, email_hash = package_snapshot_hashes(packages)
        authorization_hash = stable_hash(
            {
                "period_code": period_code,
                "mode": mode.value,
                "settlement": settlement_hash,
                "documents": document_hash,
                "email": email_hash,
            }
        )
        authorization = PeriodAuthorization(
            authorization_id=uuid4(),
            period_code=period_code,
            mode=mode,
            authorized_by=user.user_id,
            authorized_at=now or datetime.now(UTC),
            eligible_restaurant_count=eligible_restaurant_count,
            blocked_restaurant_count=blocked_restaurant_count,
            settlement_snapshot_hash=settlement_hash,
            document_snapshot_hash=document_hash,
            email_snapshot_hash=email_hash,
            authorization_hash=authorization_hash,
            confirmation_text=confirmation_text,
        )
        self.repository.save_authorization(authorization)
        self.repository.set_period_mode(period_code, mode)
        self._audit("AUTHORIZATION_CREATED", user, authorization)
        return authorization

    def is_current(
        self,
        authorization: PeriodAuthorization,
        packages: tuple[PartnerEmailPackage, ...],
    ) -> bool:
        settlement, documents, email = package_snapshot_hashes(packages)
        current = (
            authorization.status == AuthorizationStatus.ACTIVE
            and authorization.settlement_snapshot_hash == settlement
            and authorization.document_snapshot_hash == documents
            and authorization.email_snapshot_hash == email
        )
        if not current:
            self.invalidate(authorization, actor_id="SYSTEM", reason="SNAPSHOT_CHANGED")
        return current

    def invalidate(
        self,
        authorization: PeriodAuthorization,
        *,
        actor_id: str,
        reason: str,
    ) -> None:
        self.repository.set_authorization_status(
            authorization.authorization_id, AuthorizationStatus.STALE
        )
        self.repository.append_audit(
            AuditEvent(
                event_type="AUTHORIZATION_STALE",
                actor_id=actor_id,
                period_id=authorization.period_code,
                entity_type="PERIOD_AUTHORIZATION",
                entity_id=str(authorization.authorization_id),
                details={"authorization_id": str(authorization.authorization_id), "reason": reason},
            )
        )

    def _audit(
        self, event_type: str, user: User, authorization: PeriodAuthorization
    ) -> None:
        self.repository.append_audit(
            AuditEvent(
                event_type=event_type,
                actor_id=user.user_id,
                period_id=authorization.period_code,
                entity_type="PERIOD_AUTHORIZATION",
                entity_id=str(authorization.authorization_id),
                details={
                    "role": user.role.value,
                    "mode": authorization.mode.value,
                    "authorization_id": str(authorization.authorization_id),
                    "authorization_hash": authorization.authorization_hash,
                },
            )
        )
