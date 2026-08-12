from datetime import UTC, datetime
from decimal import Decimal

from src.config import Settings
from src.ingestion.admin_earnings_models import NormalizedAdminEarningsRow
from src.models.enums import FinancialDecision
from src.settlement.engine import SettlementEngine
from src.settlement.rules import SettlementRuleConfig, SettlementRuleEngine


def test_delivered_pays_partner() -> None:
    result = SettlementRuleEngine().decide("delivered", None)
    assert result.decision == FinancialDecision.PAY_PARTNER
    assert result.reason == "DELIVERED_ORDER"


def test_partner_cancellation_is_excluded() -> None:
    result = SettlementRuleEngine().decide("CANCELLED", "Restaurant")
    assert result.decision == FinancialDecision.EXCLUDE
    assert result.reason == "PARTNER_RESPONSIBILITY"


def test_yassir_and_driver_cancellation_rules_are_distinct() -> None:
    engine = SettlementRuleEngine()
    assert (
        engine.decide("CANCELLED", "YASSIR").decision
        == FinancialDecision.YASSIR_COMPENSATION
    )
    assert (
        engine.decide("CANCELLED", "DRIVER").decision
        == FinancialDecision.MANUAL_REVIEW
    )


def test_unknown_cancellation_requires_manual_review() -> None:
    result = SettlementRuleEngine().decide("CANCELLED", "mystery")
    assert result.decision == FinancialDecision.MANUAL_REVIEW
    assert result.reason == "UNKNOWN_CANCELLATION_RESPONSIBILITY"


def test_unconfigured_status_requires_manual_review() -> None:
    assert (
        SettlementRuleEngine().decide("REFUNDED", None).decision
        == FinancialDecision.MANUAL_REVIEW
    )


def test_rule_configuration_can_override_financial_logic() -> None:
    config = SettlementRuleConfig.from_overrides(
        {
            "cancellation_rules": {
                "YASSIR": {
                    "decision": "PAY_PARTNER",
                    "reason": "CONFIGURED_YASSIR_PAYMENT",
                }
            }
        }
    )
    result = SettlementRuleEngine(config).decide("CANCELLED", "YASSIR")
    assert result.decision == FinancialDecision.PAY_PARTNER


def test_settlement_engine_preserves_operational_source_values() -> None:
    source = NormalizedAdminEarningsRow(
        order_id="O-1",
        restaurant_id="R-1",
        restaurant_name="One",
        order_date=datetime(2026, 8, 12, tzinfo=UTC),
        gross_amount=Decimal(100),
        operational_status="cancelled",
        cancellation_reason="Restaurant",
        source_file_id="file",
        source_filename="earn.csv",
        source_row_number=2,
    )
    order = SettlementEngine(Settings(_env_file=None)).process((source,))[0]
    assert order.original_status == "cancelled"
    assert order.cancellation_reason == "Restaurant"
    assert order.automatic_settlement_decision == FinancialDecision.EXCLUDE
    assert order.final_settlement_decision == FinancialDecision.EXCLUDE
    assert order.settlement_period == "2026-08-P1"
