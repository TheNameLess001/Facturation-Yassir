from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from src.models.domain import Order, Restaurant, RestaurantSettlement
from src.models.enums import FinancialDecision, WorkflowState

MONEY = Decimal("0.01")


class SettlementCalculator:
    def summarize(
        self,
        orders: tuple[Order, ...],
        restaurants: tuple[Restaurant, ...],
        period_id: str,
        adjustments: dict[str, Decimal] | None = None,
    ) -> tuple[RestaurantSettlement, ...]:
        adjustments = adjustments or {}
        master = {item.restaurant_id: item for item in restaurants}
        grouped: dict[str, list[Order]] = defaultdict(list)
        for order in orders:
            if order.settlement_period == period_id:
                grouped[order.restaurant_id].append(order)
        summaries: list[RestaurantSettlement] = []
        for restaurant_id, items in grouped.items():
            restaurant = master.get(restaurant_id)
            gross = sum((item.gross_amount for item in items), Decimal(0))
            payable = sum(
                (
                    item.gross_amount
                    for item in items
                    if item.final_settlement_decision
                    in {
                        FinancialDecision.PAY_PARTNER,
                        FinancialDecision.YASSIR_COMPENSATION,
                    }
                ),
                Decimal(0),
            )
            rate = restaurant.commission_rate if restaurant else Decimal(0)
            commission = (payable * rate).quantize(MONEY, rounding=ROUND_HALF_UP)
            adjustment = adjustments.get(restaurant_id, Decimal(0))
            net = (payable - commission + adjustment).quantize(
                MONEY, rounding=ROUND_HALF_UP
            )
            manual_review = any(
                item.final_settlement_decision == FinancialDecision.MANUAL_REVIEW
                for item in items
            )
            state = (
                WorkflowState.BLOCKED
                if restaurant is None or net < 0
                else WorkflowState.TO_REVIEW
                if manual_review
                else WorkflowState.DATA_READY
            )
            summaries.append(
                RestaurantSettlement(
                    restaurant_id=restaurant_id,
                    period_id=period_id,
                    gross_sales=gross.quantize(MONEY, rounding=ROUND_HALF_UP),
                    commission=commission,
                    adjustments=adjustment.quantize(MONEY, rounding=ROUND_HALF_UP),
                    net_payable=net,
                    state=state,
                    readiness_score=100 if state == WorkflowState.DATA_READY else 60,
                )
            )
        return tuple(sorted(summaries, key=lambda item: item.restaurant_id))
