from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.auth import User
from src.documents import DocumentRegistry, DocumentService
from src.emails import (
    AutomationAuthorizationService,
    EmailExecutionService,
    EmailPreparationService,
    EmailRegistry,
)
from src.models.domain import Restaurant, RestaurantSettlement
from src.models.enums import AutomationMode, EmailStatus, Role, WorkflowState


def restaurant() -> Restaurant:
    return Restaurant(
        restaurant_id="R-1",
        restaurant_name="One",
        legal_entity="One SARL",
        ice="ICE",
        address="1 Approved Address",
        email="partner@example.com",
    )


def settlement(net: str = "80") -> RestaurantSettlement:
    return RestaurantSettlement(
        restaurant_id="R-1",
        period_id="2026-08-P1",
        gross_sales=Decimal(100),
        commission=Decimal(20),
        net_payable=Decimal(net),
        state=WorkflowState.VALIDATED,
    )


def admin() -> User:
    return User("admin-1", "Admin", "admin@example.com", Role.ADMIN)


def prepared(tmp_path, value: str = "80"):
    current = settlement(value)
    documents = DocumentService(DocumentRegistry(tmp_path / "docs.sqlite3")).generate(
        restaurant(), current
    )
    message = EmailPreparationService().prepare(
        restaurant(), current, tuple(item[0] for item in documents)
    )
    return current, message, [item[1] for item in documents]


def test_new_period_default_automation_state(tmp_path) -> None:
    service = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    assert service.mode_for_period("2026-08-P1") == AutomationMode.OFF
    assert service.mode_for_period("2026-08-P2") == AutomationMode.OFF


def test_attempt_email_send_without_admin_authorization_is_blocked(tmp_path) -> None:
    current, message, attachments = prepared(tmp_path)
    gmail = Mock()
    executor = EmailExecutionService(
        gmail,
        EmailRegistry(tmp_path / "emails.sqlite3"),
        AutomationAuthorizationService(tmp_path / "auth.sqlite3"),
    )
    assert (
        executor.execute(message, current, attachments)
        == EmailStatus.WAITING_ADMIN_AUTHORIZATION
    )
    gmail.send_message.assert_not_called()
    gmail.create_draft.assert_not_called()


def test_finance_cannot_authorize_send(tmp_path) -> None:
    finance = User("f", "Finance", "f@example.com", Role.FINANCE)
    with pytest.raises(PermissionError):
        AutomationAuthorizationService(tmp_path / "auth.sqlite3").authorize(
            finance,
            "2026-08-P1",
            AutomationMode.SEND_EMAILS,
            (settlement(),),
            confirmed=True,
            typed_confirmation="SEND 2026-08-P1",
        )


def test_send_requires_exact_typed_confirmation(tmp_path) -> None:
    service = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    with pytest.raises(PermissionError):
        service.authorize(
            admin(),
            "2026-08-P1",
            AutomationMode.SEND_EMAILS,
            (settlement(),),
            confirmed=True,
            typed_confirmation="confirm send",
        )


def test_period_authorization_does_not_carry_forward(tmp_path) -> None:
    service = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    service.authorize(
        admin(),
        "2026-08-P1",
        AutomationMode.SEND_EMAILS,
        (settlement(),),
        confirmed=True,
        typed_confirmation="SEND 2026-08-P1",
    )
    assert service.mode_for_period("2026-08-P1") == AutomationMode.SEND_EMAILS
    assert service.mode_for_period("2026-08-P2") == AutomationMode.OFF


def test_authorized_send_is_idempotent(tmp_path) -> None:
    current, message, attachments = prepared(tmp_path)
    authorizations = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    authorizations.authorize(
        admin(),
        current.period_id,
        AutomationMode.SEND_EMAILS,
        (current,),
        confirmed=True,
        typed_confirmation="SEND 2026-08-P1",
    )
    gmail = Mock()
    gmail.send_message.return_value = "provider-1"
    executor = EmailExecutionService(
        gmail,
        EmailRegistry(tmp_path / "emails.sqlite3"),
        authorizations,
        production_send_enabled=True,
    )
    assert executor.execute(message, current, attachments) == EmailStatus.SENT
    assert executor.execute(message, current, attachments) == EmailStatus.SENT
    gmail.send_message.assert_called_once()


def test_create_drafts_never_sends(tmp_path) -> None:
    current, message, attachments = prepared(tmp_path)
    authorizations = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    authorizations.authorize(
        admin(),
        current.period_id,
        AutomationMode.CREATE_DRAFTS,
        (current,),
        confirmed=True,
    )
    gmail = Mock()
    gmail.create_draft.return_value = "draft-1"
    executor = EmailExecutionService(
        gmail,
        EmailRegistry(tmp_path / "emails.sqlite3"),
        authorizations,
        draft_execution_enabled=True,
    )
    assert executor.execute(message, current, attachments) == EmailStatus.AUTHORIZED
    gmail.create_draft.assert_called_once()
    gmail.send_message.assert_not_called()


def test_financial_adjustment_after_authorization_is_stale(tmp_path) -> None:
    current, message, attachments = prepared(tmp_path)
    authorizations = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    authorizations.authorize(
        admin(),
        current.period_id,
        AutomationMode.SEND_EMAILS,
        (current,),
        confirmed=True,
        typed_confirmation="SEND 2026-08-P1",
    )
    modified = current.model_copy(update={"net_payable": Decimal(81)})
    gmail = Mock()
    executor = EmailExecutionService(
        gmail,
        EmailRegistry(tmp_path / "emails.sqlite3"),
        authorizations,
        production_send_enabled=True,
    )
    assert (
        executor.execute(message, modified, attachments)
        == EmailStatus.WAITING_ADMIN_AUTHORIZATION
    )
    assert authorizations.active_for_period(current.period_id) is None
    gmail.send_message.assert_not_called()


def test_failed_email_does_not_stop_or_mark_sent(tmp_path) -> None:
    current, message, attachments = prepared(tmp_path)
    authorizations = AutomationAuthorizationService(tmp_path / "auth.sqlite3")
    authorizations.authorize(
        admin(),
        current.period_id,
        AutomationMode.SEND_EMAILS,
        (current,),
        confirmed=True,
        typed_confirmation="SEND 2026-08-P1",
    )
    gmail = Mock()
    gmail.send_message.side_effect = RuntimeError("provider error")
    registry = EmailRegistry(tmp_path / "emails.sqlite3")
    assert (
        EmailExecutionService(
            gmail,
            registry,
            authorizations,
            production_send_enabled=True,
        ).execute(
            message, current, attachments
        )
        == EmailStatus.FAILED
    )
    assert registry.status(message.communication_key) == EmailStatus.FAILED


def test_test_email_uses_internal_recipient_and_does_not_mark_partner_sent(
    tmp_path,
) -> None:
    _current, message, attachments = prepared(tmp_path)
    gmail = Mock()
    gmail.send_message.return_value = "test-1"
    registry = EmailRegistry(tmp_path / "emails.sqlite3")
    executor = EmailExecutionService(
        gmail,
        registry,
        AutomationAuthorizationService(tmp_path / "auth.sqlite3"),
        test_send_enabled=True,
        allowed_test_recipient="internal@example.com",
    )
    executor.send_test(message, "internal@example.com", attachments)
    args = gmail.send_message.call_args.args
    assert args[0] == "internal@example.com"
    assert args[1].startswith("[TEST CASHCO]")
    assert registry.status(message.communication_key) is None
