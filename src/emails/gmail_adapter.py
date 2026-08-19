from __future__ import annotations

from typing import Protocol

from src.config import Settings
from src.emails.phase10_models import (
    CapabilityStatus,
    GmailAuthenticationStatus,
    GmailCapability,
    PartnerEmailPackage,
)
from src.google.gmail_service import GoogleGmailService


class GmailAdapter(Protocol):
    def create_draft(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str: ...

    def send_package(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str: ...

    def get_message_metadata(self, provider_message_id: str) -> dict[str, str]: ...


class FakeGmailAdapter:
    """Deterministic test boundary. It never connects to an external provider."""

    def __init__(self, *, fail_send_keys: frozenset[str] = frozenset()) -> None:
        self.fail_send_keys = fail_send_keys
        self.drafts: list[str] = []
        self.sent: list[str] = []

    def create_draft(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str:
        provider_id = f"fake-draft-{package.send_key[:16]}"
        self.drafts.append(package.send_key)
        return provider_id

    def send_package(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str:
        if package.send_key in self.fail_send_keys:
            raise RuntimeError("FAKE_PROVIDER_FAILURE")
        provider_id = f"fake-message-{package.send_key[:16]}"
        self.sent.append(package.send_key)
        return provider_id

    def get_message_metadata(self, provider_message_id: str) -> dict[str, str]:
        return {"id": provider_message_id, "provider": "FAKE"}


class ProductionGmailAdapter:
    """Uninstantiated production boundary; construction requires approved Gmail auth."""

    def __init__(self, service: GoogleGmailService) -> None:
        self.service = service

    def create_draft(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str:
        if package.recipient_to is None:
            raise ValueError("EMAIL_MISSING")
        return self.service.create_draft(
            package.recipient_to,
            package.subject,
            package.body,
            list(attachments),
        )

    def send_package(
        self, package: PartnerEmailPackage, attachments: tuple[bytes, ...]
    ) -> str:
        if package.recipient_to is None:
            raise ValueError("EMAIL_MISSING")
        return self.service.send_message(
            package.recipient_to,
            package.subject,
            package.body,
            list(attachments),
        )

    def get_message_metadata(self, provider_message_id: str) -> dict[str, str]:
        return self.service.get_message_metadata(provider_message_id)


def inspect_gmail_capability(settings: Settings) -> GmailCapability:
    delegated = bool(
        settings.gmail_auth_mode == "DOMAIN_DELEGATION"
        and settings.gmail_domain_delegated_user
        and settings.google_credentials_configured
    )
    configured = bool(
        (settings.gmail_oauth_configured or delegated)
        and settings.gmail_sender_email
        and settings.gmail_sender_email.strip()
    )
    if not configured:
        return GmailCapability(
            credentials_detected=False,
            authentication=GmailAuthenticationStatus.NOT_CONFIGURED,
            draft_capability=CapabilityStatus.NO,
            send_capability=CapabilityStatus.NO,
        )
    # Configuration-only validation is deliberate: this phase must not perform a
    # Gmail API call or infer Gmail authority from Drive service-account access.
    return GmailCapability(
        credentials_detected=True,
        authentication=GmailAuthenticationStatus.PASS,
        draft_capability=CapabilityStatus.NOT_TESTED_SAFELY,
        send_capability=CapabilityStatus.NOT_TESTED_SAFELY,
    )


def validate_gmail_capability(
    settings: Settings, *, api: object | None = None
) -> GmailCapability:
    """Perform a read-only Gmail profile check only when auth is configured."""
    configured = inspect_gmail_capability(settings)
    if configured.authentication == GmailAuthenticationStatus.NOT_CONFIGURED:
        return configured
    try:
        if api is None:
            from src.google.gmail_auth import build_gmail_api

            api = build_gmail_api(settings)
        profile = api.users().getProfile(userId="me").execute()  # type: ignore[attr-defined]
        provider_email = str(profile.get("emailAddress", "")).strip().casefold()
        sender = (settings.gmail_sender_email or "").strip().casefold()
        if not provider_email or provider_email != sender:
            return GmailCapability(
                credentials_detected=True,
                authentication=GmailAuthenticationStatus.FAIL,
                draft_capability=CapabilityStatus.NO,
                send_capability=CapabilityStatus.NO,
            )
        return GmailCapability(
            credentials_detected=True,
            authentication=GmailAuthenticationStatus.PASS,
            draft_capability=CapabilityStatus.NOT_TESTED_SAFELY,
            send_capability=CapabilityStatus.NOT_TESTED_SAFELY,
        )
    except Exception:  # noqa: BLE001 - isolated provider capability boundary
        return GmailCapability(
            credentials_detected=True,
            authentication=GmailAuthenticationStatus.FAIL,
            draft_capability=CapabilityStatus.NO,
            send_capability=CapabilityStatus.NO,
        )
