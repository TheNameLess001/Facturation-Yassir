from __future__ import annotations

from datetime import UTC, datetime

from src.documents.service import financial_hash
from src.emails.authorization import AutomationAuthorizationService
from src.emails.registry import EmailRegistry
from src.google.interfaces import GmailService
from src.models.domain import Document, EmailMessage, Restaurant, RestaurantSettlement
from src.models.enums import AutomationMode, DocumentStatus, EmailStatus


class EmailPreparationService:
    def prepare(
        self,
        restaurant: Restaurant,
        settlement: RestaurantSettlement,
        documents: tuple[Document, ...],
    ) -> EmailMessage:
        if not restaurant.email:
            raise ValueError("Partner email is missing")
        if not documents or any(
            item.status != DocumentStatus.GENERATED for item in documents
        ):
            raise ValueError("All documents must be current and generated")
        communication_key = (
            f"{restaurant.restaurant_id}:{settlement.period_id}:SETTLEMENT"
        )
        return EmailMessage(
            communication_key=communication_key,
            restaurant_id=restaurant.restaurant_id,
            period_id=settlement.period_id,
            recipient=restaurant.email,
            subject=f"CashCo settlement · {restaurant.restaurant_name} · {settlement.period_id}",
            body=(
                f"Restaurant: {restaurant.restaurant_name}\nPeriod: {settlement.period_id}\n"
                f"Gross sales: {settlement.gross_sales} MAD\nCommission: {settlement.commission} MAD\n"
                f"Adjustments: {settlement.adjustments} MAD\nNet payable: {settlement.net_payable} MAD"
            ),
            attachment_document_ids=tuple(item.document_id for item in documents),
            financial_hash=financial_hash(settlement),
            status=EmailStatus.WAITING_ADMIN_AUTHORIZATION,
        )


class EmailExecutionService:
    def __init__(
        self,
        gmail: GmailService,
        registry: EmailRegistry,
        authorizations: AutomationAuthorizationService,
        *,
        draft_execution_enabled: bool = False,
        production_send_enabled: bool = False,
        test_send_enabled: bool = False,
        allowed_test_recipient: str | None = None,
    ) -> None:
        self.gmail = gmail
        self.registry = registry
        self.authorizations = authorizations
        self.draft_execution_enabled = draft_execution_enabled
        self.production_send_enabled = production_send_enabled
        self.test_send_enabled = test_send_enabled
        self.allowed_test_recipient = allowed_test_recipient

    def execute(
        self,
        message: EmailMessage,
        settlement: RestaurantSettlement,
        attachments: list[bytes],
        *,
        resend_reason: str | None = None,
    ) -> EmailStatus:
        authorization = self.authorizations.active_for_period(message.period_id)
        if authorization is None:
            return EmailStatus.WAITING_ADMIN_AUTHORIZATION
        if not self.authorizations.validate_settlement(settlement):
            self.authorizations.invalidate_restaurant(
                message.period_id, message.restaurant_id
            )
            return EmailStatus.WAITING_ADMIN_AUTHORIZATION
        if message.financial_hash != financial_hash(settlement):
            self.authorizations.invalidate_restaurant(
                message.period_id, message.restaurant_id
            )
            return EmailStatus.WAITING_ADMIN_AUTHORIZATION
        existing = self.registry.status(message.communication_key)
        if existing == EmailStatus.SENT and not resend_reason:
            return EmailStatus.SENT
        if existing == EmailStatus.SENT and not resend_reason.strip():
            raise ValueError("Manual resend requires a reason")
        try:
            if authorization.automation_mode == AutomationMode.CREATE_DRAFTS:
                if not self.draft_execution_enabled:
                    return EmailStatus.WAITING_ADMIN_AUTHORIZATION
                provider_id = self.gmail.create_draft(
                    message.recipient, message.subject, message.body, attachments
                )
                status = EmailStatus.AUTHORIZED
                sent_at = None
            elif authorization.automation_mode == AutomationMode.SEND_EMAILS:
                if not self.production_send_enabled:
                    return EmailStatus.WAITING_ADMIN_AUTHORIZATION
                provider_id = self.gmail.send_message(
                    message.recipient, message.subject, message.body, attachments
                )
                status = EmailStatus.SENT
                sent_at = datetime.now(UTC)
            else:
                return EmailStatus.WAITING_ADMIN_AUTHORIZATION
            self.registry.record(
                message.communication_key,
                message.restaurant_id,
                message.period_id,
                status,
                provider_id=provider_id,
                sent_at=sent_at,
                resend_reason=resend_reason,
            )
            return status
        except Exception as exc:  # noqa: BLE001 - provider boundary records per-recipient failure
            self.registry.record(
                message.communication_key,
                message.restaurant_id,
                message.period_id,
                EmailStatus.FAILED,
                error=type(exc).__name__,
                resend_reason=resend_reason,
            )
            return EmailStatus.FAILED

    def send_test(
        self, message: EmailMessage, internal_recipient: str, attachments: list[bytes]
    ) -> str:
        if not internal_recipient:
            raise ValueError("Internal test recipient is required")
        if not self.test_send_enabled:
            raise PermissionError("TEST_SEND_DISABLED")
        if internal_recipient != self.allowed_test_recipient:
            raise PermissionError("TEST_RECIPIENT_NOT_APPROVED")
        return self.gmail.send_message(
            internal_recipient,
            f"[TEST CASHCO] {message.subject}",
            message.body,
            attachments,
        )
