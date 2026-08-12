from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from src.models.enums import FinancialDecision
from src.restaurants.registry_models import MappingStatus
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader
from src.settlement.financial_rules import FinancialEligibilityRuleEngine
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import (
    CancellationResponsibility,
    OperationalClassification,
    RestaurantSettlementStatus,
    SettlementPeriodStatus,
)
from src.settlement.phase5_service import (
    Phase5SettlementService,
    normalize_commission_rate,
)


def rst_row(
    restaurant_id: str,
    name: str,
    *,
    commission: str | None = "20",
) -> dict[str, object]:
    return {
        "Restaurant ID": restaurant_id,
        "Restaurant Name": name,
        "Commission %": commission,
        "Email": "billing@example.test",
        "Legal Entity": "Example SARL",
        "ICE": "001",
        "Address": "1 Main Street",
        "RIB": "001122",
    }


def registry(
    scope_rows: list[dict[str, object]],
    rst_rows: list[dict[str, object]],
):
    scope = pd.DataFrame(scope_rows)
    rst = pd.DataFrame(rst_rows)
    return RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
    )


def order(
    order_id: str,
    restaurant_id: str,
    order_date: str,
    *,
    status: str = "Delivered",
    reason: str | None = None,
    amount: object = "100.00",
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "restaurant_id": restaurant_id,
        "restaurant_name": f"Restaurant {restaurant_id}",
        "order_date": order_date,
        "operational_status": status,
        "cancellation_reason": reason,
        "item_total": amount,
        "promo_amount": "0",
        "delivery_fee": "10",
        "commission_amount": "20",
    }


def evaluate(
    scope_rows: list[dict[str, object]],
    rst_rows: list[dict[str, object]],
    orders: list[dict[str, object]],
    *,
    issues: pd.DataFrame | None = None,
    period_code: str = "2026-08-P1",
):
    period = SettlementPeriodService().get(
        period_code,
        as_of=date(2026, 9, 1),
    )
    return Phase5SettlementService().evaluate(
        period,
        pd.DataFrame(orders),
        registry(scope_rows, rst_rows),
        invalid_financial_issues=issues,
        evaluated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_period_status_and_latest_complete_default() -> None:
    periods = SettlementPeriodService()
    latest = periods.latest_complete(as_of=date(2026, 8, 12))
    open_period = periods.create(2026, 8, "P1", as_of=date(2026, 8, 12))
    future = periods.create(2026, 8, "P2", as_of=date(2026, 8, 12))
    assert latest.period_code == "2026-07-P2"
    assert latest.status == SettlementPeriodStatus.COMPLETE
    assert open_period.status == SettlementPeriodStatus.OPEN_INCOMPLETE
    assert future.status == SettlementPeriodStatus.FUTURE


def test_p1_and_p2_use_actual_order_date_boundaries() -> None:
    periods = SettlementPeriodService()
    assert periods.period_for(date(2026, 8, 1)).period_code == "2026-08-P1"
    assert periods.period_for(date(2026, 8, 15)).period_code == "2026-08-P1"
    assert periods.period_for(date(2026, 8, 16)).period_code == "2026-08-P2"
    assert periods.period_for(date(2026, 8, 31)).period_code == "2026-08-P2"
    assert periods.period_for(date(2026, 2, 28)).period_code == "2026-02-P2"
    assert periods.period_for(date(2024, 2, 29)).period_code == "2024-02-P2"


def test_financial_rule_matrix_is_conservative_and_traceable() -> None:
    engine = FinancialEligibilityRuleEngine()
    delivered = engine.classify("Delivered", None)
    restaurant = engine.classify("Restaurant Rejected", None)
    yassir = engine.classify("Cancelled by admin", "No available courier")
    customer = engine.classify("Cancelled by user", "Order placed accidentally")
    courier = engine.classify(
        "Cancelled by admin",
        "Issue driver after pickup",
    )
    unknown = engine.classify("Cancelled by admin", "unmapped free text")
    other = engine.classify("Scheduled", None)
    assert delivered.decision == FinancialDecision.PAY_PARTNER
    assert delivered.classification == OperationalClassification.DELIVERED
    assert restaurant.decision == FinancialDecision.EXCLUDE
    assert restaurant.responsibility == CancellationResponsibility.RESTAURANT
    assert yassir.decision == FinancialDecision.YASSIR_COMPENSATION
    assert yassir.responsibility == CancellationResponsibility.YASSIR
    assert customer.decision == FinancialDecision.MANUAL_REVIEW
    assert customer.responsibility == CancellationResponsibility.CUSTOMER
    assert courier.decision == FinancialDecision.MANUAL_REVIEW
    assert courier.responsibility == CancellationResponsibility.COURIER
    assert unknown.decision == FinancialDecision.MANUAL_REVIEW
    assert unknown.responsibility == CancellationResponsibility.UNKNOWN
    assert other.decision == FinancialDecision.MANUAL_REVIEW
    assert other.classification == OperationalClassification.OTHER
    trace = delivered.trace("Delivered", None)
    assert trace.decision_rule == "DELIVERED_ORDER"
    assert trace.source_fields_used["operational_status"] == "Delivered"
    assert trace.engine_version == "cashco-phase5-1.0"


def test_identity_ready_is_evaluated_and_blocked_identity_is_diagnostic() -> None:
    result = evaluate(
        [
            {"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"},
            {
                "Column 1": "Blocked",
                "Restaurant ID": "BLOCKED",
                "Commission": "20",
            },
        ],
        [rst_row("R1", "Ready")],
        [
            order("O1", "R1", "2026-08-10"),
            order("O2", "BLOCKED", "2026-08-10", amount="50"),
            order("O3", "OUT", "2026-08-10", amount="25"),
        ],
    )
    assert result.identity_ready_restaurants == 1
    assert result.identity_blocked_restaurants == 1
    assert result.canonical_orders_in_period == 3
    assert result.settlement_evaluated_orders == 1
    assert result.identity_blocked_orders == 1
    assert result.identity_blocked.blocked_gmv == Decimal(50)
    assert result.outside_invoice_scope_orders == 1


def test_decision_and_money_reconciliation_has_no_unexplained_loss() -> None:
    result = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"}],
        [rst_row("R1", "Ready")],
        [
            order("D", "R1", "2026-08-01", amount="100.10"),
            order(
                "E",
                "R1",
                "2026-08-02",
                status="Restaurant Rejected",
                amount="20.20",
            ),
            order(
                "Y",
                "R1",
                "2026-08-03",
                status="Cancelled by admin",
                reason="No available courier",
                amount="30.30",
            ),
            order(
                "M",
                "R1",
                "2026-08-04",
                status="Cancelled by user",
                amount="40.40",
            ),
        ],
    )
    assert (
        result.pay_partner_orders
        + result.excluded_orders
        + result.yassir_compensation_orders
        + result.manual_review_orders
        == result.settlement_evaluated_orders
    )
    reconciliation = result.money_reconciliation[0]
    assert reconciliation.source_total == Decimal("191.00")
    assert reconciliation.classified_total == Decimal("191.00")
    assert reconciliation.difference == Decimal(0)
    restaurant = result.restaurants[0]
    assert restaurant.eligible_partner_amount == Decimal("100.10")
    assert restaurant.excluded_amount == Decimal("20.20")
    assert restaurant.compensation_amount == Decimal("30.30")
    assert restaurant.settlement_status == RestaurantSettlementStatus.REVIEW_REQUIRED


def test_commission_scope_precedence_equivalence_and_mismatch() -> None:
    equivalent = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"}],
        [rst_row("R1", "Ready", commission="0.20")],
        [order("O1", "R1", "2026-08-10")],
    )
    mismatch = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"}],
        [rst_row("R1", "Ready", commission="25")],
        [order("O1", "R1", "2026-08-10")],
    )
    assert equivalent.restaurants[0].commission_rate == Decimal("0.2")
    assert "COMMISSION_MISMATCH" not in equivalent.restaurants[0].issue_codes
    assert mismatch.commission_mismatches == 1
    assert mismatch.restaurants[0].settlement_status == RestaurantSettlementStatus.READY
    assert (
        mismatch.restaurants[0].commission_resolution.resolution_source
        == "INVOICE_SCOPE_AUTHORITY"
    )
    assert mismatch.restaurants[0].commission_rate == Decimal("0.2")


def test_missing_scope_commission_blocks_even_when_rst_has_reference() -> None:
    result = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1"}],
        [rst_row("R1", "Ready", commission="20")],
        [order("O1", "R1", "2026-08-10")],
    )
    restaurant = result.restaurants[0]
    assert restaurant.invoice_scope_commission_rate is None
    assert restaurant.rst_commission_rate == Decimal("0.2")
    assert "MISSING_INVOICE_SCOPE_COMMISSION" in restaurant.issue_codes
    assert restaurant.settlement_status == RestaurantSettlementStatus.BLOCKED_COMMISSION


def test_zero_commission_is_valid_and_no_orders_restaurant_is_retained() -> None:
    result = evaluate(
        [
            {"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "0"},
            {"Column 1": "No Orders", "Restaurant ID": "R2", "Commission": "0"},
        ],
        [
            rst_row("R1", "Ready", commission="0"),
            rst_row("R2", "No Orders", commission="0"),
        ],
        [order("O1", "R1", "2026-08-10")],
    )
    by_id = {item.restaurant_id: item for item in result.restaurants}
    assert by_id["R1"].settlement_status == RestaurantSettlementStatus.READY
    assert by_id["R1"].commission_rate == Decimal(0)
    assert by_id["R2"].settlement_status == RestaurantSettlementStatus.NO_ORDERS
    assert result.no_orders_restaurants == 1


def test_invalid_financial_value_is_never_coerced_to_zero() -> None:
    issues = pd.DataFrame(
        [
            {
                "category": "INVALID_FINANCIAL_VALUE",
                "order_id": "O1",
                "field": "delivery_fee",
            }
        ]
    )
    result = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"}],
        [rst_row("R1", "Ready")],
        [order("O1", "R1", "2026-08-10", amount=None)],
        issues=issues,
    )
    evaluated_order = result.restaurants[0].orders[0]
    assert evaluated_order.order_amount is None
    assert "INVALID_ORDER_AMOUNT" in evaluated_order.issue_codes
    assert result.invalid_financial_rows == 1
    assert result.money_reconciliation[0].blocking_rows == 1
    assert (
        result.restaurants[0].settlement_status
        == RestaurantSettlementStatus.BLOCKED_DATA
    )


def test_status_profile_preserves_actual_source_values() -> None:
    result = evaluate(
        [{"Column 1": "Ready", "Restaurant ID": "R1", "Commission": "20"}],
        [rst_row("R1", "Ready")],
        [
            order("O1", "R1", "2026-08-10", status="Delivered"),
            order(
                "O2",
                "R1",
                "2026-08-11",
                status="Cancelled by admin",
                reason="No available courier",
            ),
        ],
    )
    assert {item.value for item in result.status_profile.operational_statuses} == {
        "Delivered",
        "Cancelled by admin",
    }
    assert result.status_profile.cancellation_fields == ("cancellation_reason",)
    assert any(
        item.value == "No available courier"
        for item in result.status_profile.cancellation_reasons
    )


def test_legacy_calculation_policy_refuses_to_invent_missing_formulas() -> None:
    policy = Phase5SettlementService().legacy_policy
    assert policy.identified is False
    assert policy.authoritative is False
    assert "invoice_tva" in policy.unavailable_outputs
    assert "disbursement_note" in policy.unavailable_outputs


def test_commission_rate_normalization_uses_decimal() -> None:
    assert normalize_commission_rate(Decimal(20)) == (Decimal("0.2"), None)
    assert normalize_commission_rate(Decimal("0.20")) == (Decimal("0.20"), None)
    assert normalize_commission_rate(Decimal(101))[0] is None


def test_scope_id_name_mismatch_never_enters_settlement() -> None:
    result = registry(
        [
            {
                "Column 1": "Completely Different Brand",
                "Restaurant ID": "R1",
                "Commission": "20",
            }
        ],
        [rst_row("R1", "Canonical Restaurant")],
    )
    assert result.restaurants[0].mapping_status == MappingStatus.SCOPE_ID_NAME_MISMATCH
    assert result.restaurants[0].readiness.identity_ready is False
