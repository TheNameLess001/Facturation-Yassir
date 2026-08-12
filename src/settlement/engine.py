from __future__ import annotations

from datetime import UTC, datetime

from src.config import Settings
from src.ingestion.admin_earnings_models import NormalizedAdminEarningsRow
from src.models.domain import Order
from src.settlement.periods import SettlementPeriodService
from src.settlement.rules import SettlementRuleConfig, SettlementRuleEngine


class SettlementEngine:
    def __init__(self, settings: Settings) -> None:
        self.periods = SettlementPeriodService(settings.timezone)
        self.rules = SettlementRuleEngine(
            SettlementRuleConfig.from_overrides(settings.settlement_rules)
        )

    def process(
        self, records: tuple[NormalizedAdminEarningsRow, ...]
    ) -> tuple[Order, ...]:
        processed_at = datetime.now(UTC)
        orders: list[Order] = []
        for record in records:
            outcome = self.rules.decide(
                record.operational_status, record.cancellation_reason
            )
            orders.append(
                Order(
                    order_id=record.order_id,
                    restaurant_id=record.restaurant_id,
                    restaurant_name=record.restaurant_name or record.restaurant_id,
                    order_date=record.order_date,
                    settlement_period=self.periods.period_for(
                        record.order_date
                    ).period_id,
                    gross_amount=record.gross_amount,
                    original_status=record.operational_status,
                    cancellation_reason=record.cancellation_reason,
                    automatic_settlement_decision=outcome.decision,
                    final_settlement_decision=outcome.decision,
                    settlement_reason=outcome.reason,
                    source_file_id=record.source_file_id,
                    source_filename=record.source_filename,
                    processed_at=processed_at,
                )
            )
        return tuple(orders)
