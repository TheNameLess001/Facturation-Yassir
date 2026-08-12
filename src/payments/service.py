from __future__ import annotations

from datetime import date

from src.audit import AuditService
from src.models.domain import AuditEvent, Payment, RestaurantSettlement
from src.models.enums import WorkflowState
from src.payments.registry import PaymentRegistry


class PaymentService:
    def __init__(self, registry: PaymentRegistry, audit: AuditService) -> None:
        self.registry = registry
        self.audit = audit

    def record_paid(
        self,
        settlement: RestaurantSettlement,
        *,
        payment_date: date,
        reference: str,
        actor_id: str,
        period_locked: bool = False,
    ) -> Payment:
        if period_locked:
            raise PermissionError("Locked periods are read-only")
        if settlement.state != WorkflowState.SENT:
            raise ValueError("Payment requires a sent settlement")
        if not reference.strip():
            raise ValueError("Payment reference is required")
        payment = Payment(
            restaurant_id=settlement.restaurant_id,
            period_id=settlement.period_id,
            amount=settlement.net_payable,
            status="PAID",
            payment_date=payment_date,
            reference=reference,
        )
        self.registry.save(payment)
        self.audit.record(
            AuditEvent(
                event_type="PAYMENT_RECORDED",
                actor_id=actor_id,
                period_id=settlement.period_id,
                restaurant_id=settlement.restaurant_id,
                entity_type="PAYMENT",
                entity_id=str(payment.payment_id),
                details={"amount": str(payment.amount), "reference": reference},
            )
        )
        return payment
