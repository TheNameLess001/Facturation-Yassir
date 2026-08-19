from __future__ import annotations

from datetime import UTC, datetime

from src.auth import Permission, RBACService, User
from src.config import Settings
from src.emails.gmail_adapter import GmailAdapter
from src.emails.phase10_authorization import PeriodAuthorizationService
from src.emails.phase10_models import (
    BatchSendResult,
    EmailAutomationMode,
    EmailWorkflowStatus,
    PartnerEmailPackage,
    ProductionReadinessResult,
    ReadinessBlocker,
    SendAttempt,
)
from src.emails.workflow_repository import EmailWorkflowRepository
from src.models.domain import AuditEvent
from src.models.enums import AuditLevel


class ProductionSendDisabledError(PermissionError):
    pass


class Phase10EmailWorkflowService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: EmailWorkflowRepository,
        gmail: GmailAdapter,
        authorizations: PeriodAuthorizationService | None = None,
        rbac: RBACService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.gmail = gmail
        self.rbac = rbac or RBACService()
        self.authorizations = authorizations or PeriodAuthorizationService(
            repository, self.rbac
        )

    def preview(self, user: User, package: PartnerEmailPackage) -> PartnerEmailPackage:
        self.rbac.require(user, Permission.PREVIEW_EMAIL)
        self.repository.save_package(package)
        self._audit("EMAIL_PACKAGE_CREATED", user, package)
        self._audit("EMAIL_PREVIEWED", user, package)
        return package

    def create_draft(
        self,
        *,
        user: User,
        package: PartnerEmailPackage,
        readiness: ProductionReadinessResult,
        attachments: tuple[bytes, ...],
    ) -> str:
        self.rbac.require(user, Permission.PREVIEW_EMAIL)
        if not self.settings.email_allow_drafts:
            raise ProductionSendDisabledError("DRAFT_EXECUTION_DISABLED")
        if self.repository.mode_for_period(package.period_code) != EmailAutomationMode.DRAFT:
            raise PermissionError("DRAFT_MODE_NOT_ACTIVE")
        if not readiness.ready_for_draft:
            raise PermissionError("DRAFT_NOT_READY")
        provider_id = self.gmail.create_draft(package, attachments)
        self._audit("DRAFT_CREATED", user, package, {"provider_message_id": provider_id})
        return provider_id

    def send(
        self,
        *,
        user: User,
        package: PartnerEmailPackage,
        readiness: ProductionReadinessResult,
        authorized_packages: tuple[PartnerEmailPackage, ...],
        attachments: tuple[bytes, ...],
        retry: bool = False,
    ) -> SendAttempt:
        self.rbac.require(user, Permission.SEND_EMAIL)
        if not (
            self.settings.email_allow_send
            and self.settings.production_email_send_enabled
        ):
            self._audit("PRODUCTION_SEND_BLOCKED", user, package)
            raise ProductionSendDisabledError(
                ReadinessBlocker.PRODUCTION_SEND_DISABLED.value
            )
        if self.repository.mode_for_period(package.period_code) != EmailAutomationMode.SEND:
            raise PermissionError("SEND_MODE_NOT_ACTIVE")
        if self.repository.period_locked(package.period_code):
            raise PermissionError(ReadinessBlocker.PERIOD_LOCKED.value)
        authorization = self.repository.active_authorization(package.period_code)
        if authorization is None or authorization.mode != EmailAutomationMode.SEND:
            raise PermissionError(ReadinessBlocker.ADMIN_NOT_AUTHORIZED.value)
        if not self.authorizations.is_current(authorization, authorized_packages):
            raise PermissionError(ReadinessBlocker.AUTHORIZATION_STALE.value)
        if not readiness.ready_for_send:
            raise PermissionError(
                "SEND_BLOCKED: " + ",".join(item.value for item in readiness.blockers)
            )
        if package.recipient_to is None:
            raise PermissionError("EMAIL_MISSING")
        now = datetime.now(UTC)
        claim = SendAttempt(
            send_key=package.send_key,
            period_code=package.period_code,
            restaurant_id=package.restaurant_id,
            recipient=package.recipient_to,
            package_hash=package.package_hash,
            authorization_id=authorization.authorization_id,
            status=EmailWorkflowStatus.SENDING,
            attempted_at=now,
            actor_id=user.user_id,
        )
        claimed = self.repository.claim_send(claim, retry=retry)
        if claimed.status == EmailWorkflowStatus.SENT:
            self._audit("EMAIL_ALREADY_SENT_BLOCKED", user, package)
            return claimed.model_copy(update={"status": EmailWorkflowStatus.ALREADY_SENT})
        if claimed.status == EmailWorkflowStatus.SENDING and claimed != claim:
            return claimed
        if claimed.status == EmailWorkflowStatus.FAILED and not retry:
            return claimed
        authorization_details = {
            "authorization_id": str(authorization.authorization_id)
        }
        self._audit("SEND_STARTED", user, package, authorization_details)
        try:
            provider_id = self.gmail.send_package(package, attachments)
            result = claim.model_copy(
                update={
                    "status": EmailWorkflowStatus.SENT,
                    "provider_message_id": provider_id,
                    "sent_at": datetime.now(UTC),
                }
            )
            self.repository.record_send(result)
            self._audit(
                "EMAIL_SENT",
                user,
                package,
                {
                    "provider_message_id": provider_id,
                    **authorization_details,
                },
            )
            return result
        except Exception as exc:  # noqa: BLE001 - isolated provider boundary
            result = claim.model_copy(
                update={
                    "status": EmailWorkflowStatus.FAILED,
                    "error_code": type(exc).__name__,
                }
            )
            self.repository.record_send(result)
            self._audit(
                "EMAIL_FAILED",
                user,
                package,
                {"error_code": type(exc).__name__, **authorization_details},
                level=AuditLevel.WARNING,
            )
            return result

    def send_batch(
        self,
        *,
        user: User,
        packages: tuple[PartnerEmailPackage, ...],
        readiness_by_send_key: dict[str, ProductionReadinessResult],
        attachments_by_send_key: dict[str, tuple[bytes, ...]],
        retry: bool = False,
    ) -> BatchSendResult:
        results: list[SendAttempt] = []
        not_attempted = 0
        for package in packages:
            readiness = readiness_by_send_key.get(package.send_key)
            if readiness is None or not readiness.ready_for_send:
                not_attempted += 1
                continue
            result = self.send(
                user=user,
                package=package,
                readiness=readiness,
                authorized_packages=packages,
                attachments=attachments_by_send_key.get(package.send_key, ()),
                retry=retry,
            )
            results.append(result)
        return BatchSendResult(
            attempted=len(results),
            sent=sum(item.status == EmailWorkflowStatus.SENT for item in results),
            failed=sum(item.status == EmailWorkflowStatus.FAILED for item in results),
            already_sent=sum(
                item.status == EmailWorkflowStatus.ALREADY_SENT for item in results
            ),
            not_attempted=not_attempted,
            results=tuple(results),
        )

    def _audit(
        self,
        event_type: str,
        user: User,
        package: PartnerEmailPackage,
        details: dict[str, object] | None = None,
        *,
        level: AuditLevel = AuditLevel.INFO,
    ) -> None:
        self.repository.append_audit(
            AuditEvent(
                event_type=event_type,
                level=level,
                actor_id=user.user_id,
                period_id=package.period_code,
                restaurant_id=package.restaurant_id,
                entity_type="EMAIL_PACKAGE",
                entity_id=str(package.package_id),
                details={
                    "role": user.role.value,
                    "package_hash": package.package_hash,
                    "send_key": package.send_key,
                    "authorization_id": (
                        str(package.authorization_id)
                        if package.authorization_id
                        else None
                    ),
                    **(details or {}),
                },
            )
        )
