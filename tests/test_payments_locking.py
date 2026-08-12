import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from src.audit import AuditService, InMemoryAuditRepository
from src.auth import User
from src.ingestion.admin_earnings_models import IngestionIssue
from src.models.domain import RestaurantSettlement
from src.models.enums import AuditLevel, Role, WorkflowState
from src.payments import PaymentRegistry, PaymentService, PeriodLockService


def settlement(state: WorkflowState = WorkflowState.SENT) -> RestaurantSettlement:
    return RestaurantSettlement(
        restaurant_id="R-1",
        period_id="2026-08-P1",
        gross_sales=Decimal(100),
        commission=Decimal(20),
        net_payable=Decimal(80),
        state=state,
    )


def admin() -> User:
    return User("admin", "Admin", "admin@example.com", Role.ADMIN)


def finance() -> User:
    return User("finance", "Finance", "finance@example.com", Role.FINANCE)


def test_payment_requires_sent_settlement(tmp_path) -> None:
    service = PaymentService(
        PaymentRegistry(tmp_path / "payments.sqlite3"),
        AuditService(InMemoryAuditRepository()),
    )
    with pytest.raises(ValueError, match="sent settlement"):
        service.record_paid(
            settlement(WorkflowState.EMAIL_READY),
            payment_date=date(2026, 8, 20),
            reference="BANK-1",
            actor_id="finance",
        )


def test_payment_record_and_audit(tmp_path) -> None:
    audit = InMemoryAuditRepository()
    registry = PaymentRegistry(tmp_path / "payments.sqlite3")
    payment = PaymentService(registry, AuditService(audit)).record_paid(
        settlement(),
        payment_date=date(2026, 8, 20),
        reference="BANK-1",
        actor_id="finance",
    )
    assert payment.status == "PAID"
    assert registry.list_for_period("2026-08-P1") == (payment,)
    assert audit.list_events()[0].event_type == "PAYMENT_RECORDED"


def test_duplicate_payment_reference_is_prevented(tmp_path) -> None:
    service = PaymentService(
        PaymentRegistry(tmp_path / "payments.sqlite3"),
        AuditService(InMemoryAuditRepository()),
    )
    kwargs = {
        "payment_date": date(2026, 8, 20),
        "reference": "BANK-1",
        "actor_id": "finance",
    }
    service.record_paid(settlement(), **kwargs)
    with pytest.raises(sqlite3.IntegrityError):
        service.record_paid(settlement(), **kwargs)


def test_non_admin_cannot_lock_period(tmp_path) -> None:
    locks = PeriodLockService(
        tmp_path / "locks.sqlite3", AuditService(InMemoryAuditRepository())
    )
    with pytest.raises(PermissionError):
        locks.lock(finance(), "2026-08-P1", reason="Complete")


def test_blocking_errors_prevent_lock(tmp_path) -> None:
    locks = PeriodLockService(
        tmp_path / "locks.sqlite3", AuditService(InMemoryAuditRepository())
    )
    issue = IngestionIssue(
        level=AuditLevel.BLOCKING, code="EMAIL_FAILED", message="Failed"
    )
    with pytest.raises(ValueError, match="blocking issues"):
        locks.lock(admin(), "2026-08-P1", reason="Complete", blocking_issues=(issue,))


def test_admin_lock_and_explicit_unlock_are_audited(tmp_path) -> None:
    audit = InMemoryAuditRepository()
    locks = PeriodLockService(tmp_path / "locks.sqlite3", AuditService(audit))
    locks.lock(admin(), "2026-08-P1", reason="Reconciled")
    assert locks.is_locked("2026-08-P1")
    locks.unlock(admin(), "2026-08-P1", reason="Correct payment reference")
    assert not locks.is_locked("2026-08-P1")
    assert [item.event_type for item in audit.list_events()] == [
        "PERIOD_LOCKED",
        "PERIOD_UNLOCKED",
    ]
