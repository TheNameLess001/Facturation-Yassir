from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from src.audit import AuditService
from src.models.domain import AuditEvent, Order, OrderAdjustment
from src.models.enums import AuditLevel, FinancialDecision


class AdjustmentRepository(Protocol):
    def append(self, adjustment: OrderAdjustment) -> None: ...
    def list_for_order(self, order_id: str) -> tuple[OrderAdjustment, ...]: ...


class InMemoryAdjustmentRepository:
    def __init__(self) -> None:
        self._items: list[OrderAdjustment] = []

    def append(self, adjustment: OrderAdjustment) -> None:
        self._items.append(adjustment)

    def list_for_order(self, order_id: str) -> tuple[OrderAdjustment, ...]:
        return tuple(item for item in self._items if item.order_id == order_id)


class AdjustmentService:
    def __init__(
        self,
        repository: AdjustmentRepository,
        audit: AuditService,
    ) -> None:
        self.repository = repository
        self.audit = audit

    def reclassify(
        self,
        order: Order,
        new_decision: FinancialDecision,
        *,
        reason: str,
        comment: str | None,
        user_id: str,
        period_locked: bool = False,
    ) -> tuple[Order, OrderAdjustment]:
        if period_locked:
            raise PermissionError("Locked settlement periods are read-only")
        if not reason.strip():
            raise ValueError("Adjustment reason is required")
        if new_decision == order.final_settlement_decision:
            raise ValueError(
                "New financial decision must differ from the current decision"
            )
        now = datetime.now(UTC)
        adjustment = OrderAdjustment(
            order_id=order.order_id,
            period_id=order.settlement_period,
            previous_decision=order.final_settlement_decision,
            new_decision=new_decision,
            reason=reason,
            comment=comment,
            user_id=user_id,
            created_at=now,
        )
        updated = order.model_copy(
            update={
                "final_settlement_decision": new_decision,
                "settlement_reason": reason,
                "manual_override": True,
                "override_reason": reason,
                "override_comment": comment,
                "modified_by": user_id,
                "modified_at": now,
            }
        )
        self.repository.append(adjustment)
        self.audit.record(
            AuditEvent(
                event_type="ORDER_RECLASSIFIED",
                level=AuditLevel.WARNING,
                actor_id=user_id,
                period_id=order.settlement_period,
                restaurant_id=order.restaurant_id,
                entity_type="ORDER",
                entity_id=order.order_id,
                details={
                    "previous_decision": adjustment.previous_decision.value,
                    "new_decision": adjustment.new_decision.value,
                    "reason": reason,
                },
            )
        )
        return updated, adjustment
