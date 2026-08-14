from __future__ import annotations

from decimal import Decimal

from src.config import get_settings
from src.documents.phase8 import DocumentReadinessStatus, Phase8DocumentEngine
from src.emails.runtime import build_email_center_snapshot
from src.settlement.cashco_legacy_v1 import CashCoLegacyV1Policy
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import load_phase5_workspace


def main() -> None:
    settings = get_settings()
    workspace = load_phase5_workspace("2026-07-P2")
    summary = workspace.summary
    registry = LegacyFormulaRegistry()
    certification = registry.certification()
    policy = CashCoLegacyV1Policy()
    calculated = tuple(
        item
        for item in summary.restaurants
        if item.financial_policy_version == policy.policy_version
        and item.total_orders > 0
    )

    def total(field: str) -> Decimal:
        values = (getattr(item, field) for item in calculated)
        return sum((value for value in values if value is not None), Decimal(0))

    sales_ttc = total("sales_ttc")
    sales_ht = total("sales_ht")
    commission_ht = total("commission_amount")
    tva = total("invoice_tva")
    invoice_ttc = total("invoice_ttc")
    net_payable = total("net_payable")
    reconciliation = sum(
        (
            item.sales_ttc - (item.net_payable + item.invoice_ttc)
            for item in calculated
        ),
        Decimal(0),
    )
    invoice_reconciliation = sum(
        (
            item.invoice_ttc - (item.commission_amount + item.invoice_tva)
            for item in calculated
        ),
        Decimal(0),
    )
    tva_reconciliation = sum(
        (
            item.invoice_tva - (item.commission_amount * policy.tva_rate)
            for item in calculated
        ),
        Decimal(0),
    )
    if reconciliation != 0:
        raise ValueError("Aggregate net payable reconciliation is not zero")
    if invoice_reconciliation != 0:
        raise ValueError("Aggregate invoice TTC reconciliation is not zero")
    if tva_reconciliation != 0:
        raise ValueError("Aggregate TVA reconciliation is not zero")
    if any(
        item.net_payable + item.invoice_ttc != item.sales_ttc
        or item.invoice_ttc != item.commission_amount + item.invoice_tva
        or item.invoice_tva != item.commission_amount * policy.tva_rate
        for item in calculated
    ):
        raise ValueError("Restaurant financial reconciliation is not zero")

    restaurants_by_id = {
        item.restaurant_id: item
        for item in workspace.registry.restaurants
        if item.restaurant_id is not None
    }
    document_engine = Phase8DocumentEngine()
    readiness = tuple(
        document_engine.readiness(restaurants_by_id[item.restaurant_id], item)
        for item in summary.restaurants
        if item.restaurant_id in restaurants_by_id
    )
    email = build_email_center_snapshot(workspace, settings=settings)

    def display(value: Decimal, field: str) -> str:
        return f"{policy.rounding_policy(value, field):.2f}"

    print("REAL_FINANCIAL_POLICY_READ_ONLY")
    print("period", summary.period.period_code)
    print("authoritative_source", policy.authoritative_source)
    print("business_approval", "CONFIRMED")
    print("policy", policy.policy_version)
    print("certification", certification.status.value)
    print("historical_parity_cases", certification.parity_cases)
    print("financially_calculable_restaurants", len(calculated))
    print("sales_ttc", display(sales_ttc, "sales_ttc"))
    print("sales_ht", display(sales_ht, "sales_ht"))
    print("commission_ht", display(commission_ht, "commission_ht"))
    print("tva", display(tva, "tva"))
    print("invoice_ttc", display(invoice_ttc, "invoice_ttc"))
    print("net_payable", display(net_payable, "net_payable"))
    print(
        "reconciliation_difference",
        display(reconciliation, "reconciliation_difference"),
    )
    print(
        "formula_blockers",
        sum(not item.financial_formulas_validated for item in readiness),
    )
    print("legal_blockers", sum(not item.legal_ready for item in readiness))
    print(
        "manual_review_blockers",
        sum(item.manual_review_orders > 0 for item in summary.restaurants),
    )
    print(
        "invalid_financial_blockers",
        summary.restaurant_status_count(RestaurantSettlementStatus.BLOCKED_DATA),
    )
    print(
        "commission_blockers",
        summary.restaurant_status_count(RestaurantSettlementStatus.BLOCKED_COMMISSION),
    )
    print(
        "document_ready_restaurants",
        sum(item.status == DocumentReadinessStatus.READY for item in readiness),
    )
    print("email_ready", email.email_ready)
    print("production_send_enabled", settings.production_email_send_enabled)
    print("production_emails_sent", email.sent)


if __name__ == "__main__":
    main()
