from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from src.config import Settings
from src.emails.packages import EMAIL_PATTERN, normalize_email, stable_hash
from src.emails.phase10_models import PartnerEmailPackage, RecipientStatus


class GmailExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class GmailAuthMethod(StrEnum):
    NONE = "NONE"
    SERVICE_ACCOUNT_WITHOUT_DELEGATION = "SERVICE_ACCOUNT_WITHOUT_DELEGATION"
    DOMAIN_WIDE_DELEGATION = "DOMAIN_WIDE_DELEGATION"
    OAUTH_USER = "OAUTH_USER"


@dataclass(frozen=True)
class GmailSandboxCapability:
    execution_mode: GmailExecutionMode
    auth_method: GmailAuthMethod
    sender_configured: bool
    sandbox_recipient: str | None
    sandbox_recipient_valid: bool
    draft_execution_allowed: bool
    send_execution_allowed: bool

    @property
    def sandbox_available(self) -> bool:
        return bool(
            self.execution_mode == GmailExecutionMode.SANDBOX
            and self.sender_configured
            and self.sandbox_recipient_valid
        )


def inspect_gmail_sandbox(settings: Settings) -> GmailSandboxCapability:
    if settings.gmail_auth_mode == "OAUTH":
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
    return GmailSandboxCapability(
        execution_mode=mode,
        auth_method=method,
        sender_configured=sender,
        sandbox_recipient=recipient,
        sandbox_recipient_valid=recipient_valid,
        draft_execution_allowed=bool(
            mode == GmailExecutionMode.SANDBOX
            and sender
            and recipient_valid
            and settings.gmail_sandbox_allow_drafts
            and settings.email_allow_drafts
        ),
        # Sandbox SEND requires a future, separate authorization. It is false by default.
        send_execution_allowed=bool(
            mode == GmailExecutionMode.SANDBOX
            and sender
            and recipient_valid
            and settings.gmail_sandbox_allow_send
            and settings.email_allow_send
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
            "TEST / DRY RUN — aucune communication partenaire.\n\n"
            f"Restaurant d'origine: {package.restaurant_name}\n"
            f"Restaurant ID: {package.restaurant_id}\n"
            f"Période: {package.period_code}\n\n"
            "Ce message valide uniquement l'architecture CashCo."
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
