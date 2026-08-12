from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.models.domain import Order, Restaurant
from src.models.enums import FinancialDecision
from src.settlement.calculator import SettlementCalculator


def restaurant(rate: str) -> Restaurant:
    return Restaurant(
        restaurant_id="R1",
        restaurant_name="Restaurant",
        commission_rate=Decimal(rate),
    )


def order(order_id: str, amount: str, decision: FinancialDecision) -> Order:
    return Order(
        order_id=order_id,
        restaurant_id="R1",
        restaurant_name="Restaurant",
        order_date=datetime(2026, 8, 1, tzinfo=UTC),
        settlement_period="2026-08-P1",
        gross_amount=Decimal(amount),
        original_status="SOURCE_VALUE",
        automatic_settlement_decision=decision,
        final_settlement_decision=decision,
        settlement_reason="PARITY_FIXTURE",
        source_file_id="fixture",
        source_filename="fixture.csv",
        processed_at=datetime(2026, 8, 16, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("rate", "expected_commission", "expected_net"),
    [
        ("0", "0.00", "100.00"),
        ("0.10", "10.00", "90.00"),
        ("0.20", "20.00", "80.00"),
    ],
)
def test_repository_prototype_multiple_commission_rate_parity(
    rate: str,
    expected_commission: str,
    expected_net: str,
) -> None:
    result = SettlementCalculator().summarize(
        (order("D", "100", FinancialDecision.PAY_PARTNER),),
        (restaurant(rate),),
        "2026-08-P1",
    )[0]
    assert result.commission == Decimal(expected_commission)
    assert result.net_payable == Decimal(expected_net)


def test_repository_prototype_decision_and_decimal_parity() -> None:
    result = SettlementCalculator().summarize(
        (
            order("D", "100.005", FinancialDecision.PAY_PARTNER),
            order("R", "50", FinancialDecision.EXCLUDE),
            order("Y", "25.005", FinancialDecision.YASSIR_COMPENSATION),
        ),
        (restaurant("0.20"),),
        "2026-08-P1",
    )[0]
    assert result.gross_sales == Decimal("175.01")
    assert result.commission == Decimal("25.00")
    assert result.net_payable == Decimal("100.01")
