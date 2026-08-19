from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from src.config import Settings
from src.emails.gmail_adapter import GmailAdapter
from src.emails.packages import EMAIL_PATTERN, normalize_email, stable_hash
from src.emails.phase10_models import PartnerEmailPackage, RecipientStatus
from src.models.domain import AuditEvent


class GmailExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class GmailAuthMethod(StrEnum):
    NONE = "NONE"
    SERVICE_ACCOUNT_WITHOUT_DELEGATION = "SERVICE_ACCOUNT_WITHOUT_DELEGATION"
    DOMAIN_WIDE_DELEGATION = "DOMAIN_WIDE_DELEGATION"
    OAUTH_USER = "OAUTH_USER"


class SandboxDraftStatus(StrEnum):
    PENDING = "PENDING"
    CREATED = "CREATED"
    FAILED = "FAILED"
    ALREADY_CREATED = "ALREADY_CREATED"


@dataclass(frozen=True)
class SandboxDraftRecord:
    draft_key: str
    period_code: str
    restaurant_id: str
    recipient: str
    source_package_hash: str
    sandbox_package_hash: str
    status: SandboxDraftStatus
    created_at: datetime
    provider_draft_id: str | None = None
    error_code: str | None = None


class SandboxDraftRepository(Protocol):
    def claim_sandbox_draft(
        self, record: SandboxDraftRecord
    ) -> SandboxDraftRecord: ...

    def record_sandbox_draft(self, record: SandboxDraftRecord) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True)
class GmailSandboxCapability:
    execution_mode: GmailExecutionMode
    auth_method: GmailAuthMethod
    sender_configured: bool
    authentication_configured: bool
    sandbox_recipient: str | None
    sandbox_recipient_valid: bool
    draft_execution_allowed: bool
    send_execution_allowed: bool

    @property
    def sandbox_available(self) -> bool:
        return bool(
            self.execution_mode == GmailExecutionMode.SANDBOX
            and self.authentication_configured
            and self.sender_configured
            and self.sandbox_recipient_valid
        )


def inspect_gmail_sandbox(settings: Settings) -> GmailSandboxCapability:
    if settings.gmail_auth_mode == "OAUTH" or settings.gmail_oauth_configured:
        method = GmailAuthMethod.OAUTH_USER
    elif settings.gmail_auth_mode == "DOMAIN_DELEGATION":
        method = GmailAuthMethod.DOMAIN_WIDE_DELEGATION
    elif settings.google_credentials_configured:
        method = GmailAuthMethod.SERVICE_ACCOUNT_WITHOUT_DELEGATION
    else:
        method = GmailAuthMethod.NONE
    recipient = normalize_email(settings.gmail_sandbox_recipient)
    recipient_valid = bool(recipient and EMAIL_PATTERN.fullmatch(recipient))
    mode = GmailExecutionMode(settings.gmail_execution_mode)
    sender = bool(settings.gmail_sender_email and settings.gmail_sender_email.strip())
    auth_configured = method in {
        GmailAuthMethod.OAUTH_USER,
        GmailAuthMethod.DOMAIN_WIDE_DELEGATION,
    } and (
        method != GmailAuthMethod.OAUTH_USER
        or settings.gmail_oauth_configured
        or bool(
            settings.gmail_oauth_user_json
            and settings.gmail_oauth_user_json.get_secret_value().strip()
        )
    )
    return GmailSandboxCapability(
        execution_mode=mode,
        auth_method=method,
        sender_configured=sender,
        authentication_configured=auth_configured,
        sandbox_recipient=recipient,
        sandbox_recipient_valid=recipient_valid,
        draft_execution_allowed=bool(
            mode == GmailExecutionMode.SANDBOX
            and auth_configured
            and sender
            and recipient_valid
        ),
        # Sandbox SEND requires a future, separate authorization. It is false by default.
        send_execution_allowed=bool(
            mode == GmailExecutionMode.SANDBOX
            and auth_configured
            and sender
            and recipient_valid
            and settings.gmail_sandbox_send_enabled
        ),
    )


class GmailSandboxPackageFactory:
    """Build a safe provider package; never preserves a production recipient."""

    def build(
        self,
        package: PartnerEmailPackage,
        capability: GmailSandboxCapability,
    ) -> PartnerEmailPackage:
        if not capability.sandbox_available or not capability.sandbox_recipient:
            raise ValueError("SANDBOX_RECIPIENT_NOT_CONFIGURED")
        recipient = capability.sandbox_recipient
        subject = f"[TEST CASHCO] {package.subject}"
        body = (
            "TEST / DRY RUN — SANDBOX, aucune communication partenaire.\n\n"
            f"Restaurant d'origine: {package.restaurant_name}\n"
            f"Période: {package.period_code}\n\n"
            "Documents: Facture Commission, Note de Débours, Partner Statement.\n\n"
            "Ce brouillon valide uniquement l'architecture CashCo."
        )
        content_hash = stable_hash(
            {
                "recipient_to": recipient,
                "recipient_cc": (),
                "subject": subject,
                "body": body,
            }
        )
        package_hash = stable_hash(
            {
                "sandbox": True,
                "source_package_hash": package.package_hash,
                "content_hash": content_hash,
                "document_snapshot_hash": package.document_snapshot_hash,
            }
        )
        send_key = stable_hash(
            {"sandbox": True, "package_hash": package_hash, "recipient": recipient}
        )
        return package.model_copy(
            update={
                "package_id": uuid5(NAMESPACE_URL, package_hash),
                "recipient_to": recipient,
                "recipient_cc": (),
                "subject": subject,
                "body": body,
                "email_status": RecipientStatus.EMAIL_VALID,
                "content_hash": content_hash,
                "package_hash": package_hash,
                "send_key": send_key,
                "authorization_id": None,
            }
        )


class GmailSandboxDraftService:
    """Idempotent draft-only provider execution for an approved sandbox mailbox."""

    def __init__(
        self,
        settings: Settings,
        repository: SandboxDraftRepository,
        gmail: GmailAdapter,
        *,
        actor_id: str = "cashco.sandbox",
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.gmail = gmail
        self.actor_id = actor_id

    def create_draft(
        self,
        package: PartnerEmailPackage,
        attachments: tuple[bytes, ...],
    ) -> SandboxDraftRecord:
        capability = inspect_gmail_sandbox(self.settings)
        if not capability.draft_execution_allowed:
            raise PermissionError("GMAIL_SANDBOX_DRAFT_DISABLED")
        if not attachments:
            raise ValueError("GMAIL_SANDBOX_ATTACHMENT_REQUIRED")
        sandbox_package = GmailSandboxPackageFactory().build(package, capability)
        assert sandbox_package.recipient_to is not None
        draft_key = stable_hash(
            {
                "sandbox_package_hash": sandbox_package.package_hash,
                "attachments": [stable_hash(item.hex()) for item in attachments],
            }
        )
        pending = SandboxDraftRecord(
            draft_key=draft_key,
            period_code=package.period_code,
            restaurant_id=package.restaurant_id,
            recipient=sandbox_package.recipient_to,
            source_package_hash=package.package_hash,
            sandbox_package_hash=sandbox_package.package_hash,
            status=SandboxDraftStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        claimed = self.repository.claim_sandbox_draft(pending)
        if claimed.status == SandboxDraftStatus.CREATED:
            return SandboxDraftRecord(
                **{
                    **claimed.__dict__,
                    "status": SandboxDraftStatus.ALREADY_CREATED,
                }
            )
        if claimed != pending:
            return claimed
        self.repository.append_audit(
            AuditEvent(
                event_type="GMAIL_SANDBOX_PACKAGE_BUILT",
                actor_id=self.actor_id,
                period_id=package.period_code,
                restaurant_id=package.restaurant_id,
                entity_type="SANDBOX_EMAIL_PACKAGE",
                entity_id=str(sandbox_package.package_id),
                details={"package_hash": sandbox_package.package_hash},
            )
        )
        try:
            provider_id = self.gmail.create_draft(sandbox_package, attachments)
            result = SandboxDraftRecord(
                **{
                    **pending.__dict__,
                    "status": SandboxDraftStatus.CREATED,
                    "provider_draft_id": provider_id,
                }
            )
        except (RuntimeError, ValueError, OSError) as exc:
            result = SandboxDraftRecord(
                **{
                    **pending.__dict__,
                    "status": SandboxDraftStatus.FAILED,
                    "error_code": type(exc).__name__.upper(),
                }
            )
        self.repository.record_sandbox_draft(result)
        if result.status == SandboxDraftStatus.CREATED:
            self.repository.append_audit(
                AuditEvent(
                    event_type="GMAIL_SANDBOX_DRAFT_CREATED",
                    actor_id=self.actor_id,
                    period_id=package.period_code,
                    restaurant_id=package.restaurant_id,
                    entity_type="SANDBOX_EMAIL_PACKAGE",
                    entity_id=str(sandbox_package.package_id),
                    details={
                        "package_hash": sandbox_package.package_hash,
                        "draft_key": draft_key,
                        "provider_draft_id": result.provider_draft_id,
                    },
                )
            )
        return result
