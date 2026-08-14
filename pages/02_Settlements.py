from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.google.exceptions import GoogleIntegrationError
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import (
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)
from src.settlement.phase5_runtime import run_phase5_settlement
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=900, show_spinner="Evaluating the settlement period…")
def load_settlement(period_code: str):
    return run_phase5_settlement(period_code)


@st.dialog("Restaurant settlement evaluation", width="large")
def settlement_dialog(settlement: RestaurantSettlementEvaluation) -> None:
    st.markdown(f"### {settlement.restaurant_name or settlement.restaurant_id}")
    st.caption(
        f"{settlement.restaurant_id} · {settlement.period_code} · "
        f"{settlement.settlement_status.value}"
    )
    summary_tab, orders_tab, classification_tab, reconciliation_tab, issues_tab = (
        st.tabs(
            [
                "Summary",
                "Orders",
                "Financial Classification",
                "Reconciliation",
                "Issues",
            ]
        )
    )
    with summary_tab:
        render_kpis(
            [
                ("Orders", f"{settlement.total_orders:,}", "Canonical only"),
                ("Delivered", f"{settlement.delivered_orders:,}", "PAY_PARTNER by rule"),
                ("Cancelled", f"{settlement.cancelled_orders:,}", "Responsibility evaluated"),
                ("Manual Review", f"{settlement.manual_review_orders:,}", "Unresolved"),
            ]
        )
        st.metric("Gross order value", f"{settlement.gross_order_value:,.2f} MAD")
        st.metric(
            "Invoice Scope commission",
            (
                f"{settlement.invoice_scope_commission_rate:.4%}"
                if settlement.invoice_scope_commission_rate is not None
                else "MISSING"
            ),
        )
        if settlement.financial_policy_version:
            st.write(
                {
                    "Policy": settlement.financial_policy_version,
                    "Sales TTC": f"{settlement.sales_ttc:,.2f} MAD",
                    "Sales HT / Commission Base": f"{settlement.sales_ht:,.2f} MAD",
                    "Commission HT": f"{settlement.commission_amount:,.2f} MAD",
                    "TVA": f"{settlement.invoice_tva:,.2f} MAD",
                    "Invoice TTC": f"{settlement.invoice_ttc:,.2f} MAD",
                    "Net Payable": f"{settlement.net_payable:,.2f} MAD",
                }
            )
        else:
            st.info(
                "Monetary calculation is unavailable until this restaurant clears "
                "its financial review, commission, and data-quality blockers."
            )
    with orders_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Order ID": order.order_id,
                        "Date": order.order_date,
                        "Operational Status": order.source_order_status,
                        "Cancellation Reason": order.cancellation_reason,
                        "Responsibility": order.cancellation_responsibility.value,
                        "System Decision": order.system_financial_decision.value,
                        "Final Decision": order.final_financial_decision.value,
                        "Override": "APPLIED" if order.manual_override_applied else "NONE",
                        "Order Amount": order.order_amount,
                        "Commission Base": order.commission_base,
                        "Issue": " · ".join(order.issue_codes),
                    }
                    for order in settlement.orders
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with classification_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Order ID": order.order_id,
                        "Classification": order.financial_classification.value,
                        "Responsibility": order.cancellation_responsibility.value,
                        "System Decision": order.system_financial_decision.value,
                        "Final Decision": order.final_financial_decision.value,
                        "Rule": order.decision_trace.decision_rule,
                        "Engine": order.decision_trace.engine_version,
                    }
                    for order in settlement.orders
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with reconciliation_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Population": "All restaurant orders",
                        "Orders": settlement.total_orders,
                        "Amount": settlement.gross_order_value,
                    },
                    {
                        "Population": "PAY_PARTNER",
                        "Orders": settlement.pay_partner_orders,
                        "Amount": settlement.eligible_partner_amount,
                    },
                    {
                        "Population": "EXCLUDE",
                        "Orders": settlement.excluded_orders,
                        "Amount": settlement.excluded_amount,
                    },
                    {
                        "Population": "YASSIR_COMPENSATION",
                        "Orders": settlement.yassir_compensation_orders,
                        "Amount": settlement.compensation_amount,
                    },
                    {
                        "Population": "MANUAL_REVIEW",
                        "Orders": settlement.manual_review_orders,
                        "Amount": None,
                    },
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with issues_tab:
        if settlement.issue_codes:
            for issue in settlement.issue_codes:
                st.error(issue)
        else:
            st.success("No settlement-input issue was detected.")


page_setup("Settlements")
st.title("Settlement Period")
st.caption(
    "P1 / P2 financial eligibility · Canonical Admin orders · Identity-ready scope only"
)
settings = get_settings()
periods = SettlementPeriodService(settings.timezone)
today = datetime.now(ZoneInfo(settings.timezone)).date()
latest = periods.latest_complete(as_of=today)

controls = st.columns([1, 1, 1, 1.3])
year = controls[0].selectbox(
    "Year",
    list(range(today.year, 2023, -1)),
    index=max(0, today.year - latest.year),
)
month = controls[1].selectbox(
    "Month",
    list(range(1, 13)),
    index=latest.month - 1,
    format_func=lambda value: calendar.month_name[value],
)
half = controls[2].selectbox("Half", ["P1", "P2"], index=0 if latest.half == "P1" else 1)
selected = periods.create(year, month, half, as_of=today)
with controls[3]:
    st.caption("Selected period")
    st.markdown(f"**{selected.period_code}** · {selected.status.value}")

actions = st.columns([1, 1, 4])
evaluate = actions[0].button("Evaluate Period", type="primary")
if actions[1].button("Refresh Google Sources"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.session_state.pop("phase5_period", None)
    st.rerun()
if evaluate:
    st.session_state["phase5_period"] = selected.period_code

if selected.status.value != "COMPLETE":
    st.warning(f"{selected.status.value} · The selected period may contain incomplete data.")

active_period = st.session_state.get("phase5_period")
if active_period is None:
    st.info(
        f"Latest complete period: {latest.period_code}. Select a period and click "
        "Evaluate Period. No Google source is modified."
    )
    st.warning("AUTOMATION OFF · No automatic validation or document generation")
    st.stop()

try:
    result = load_settlement(active_period)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Settlement evaluation is unavailable: {exc}")
    st.stop()

st.markdown(f"### {result.period.display_name} · {result.period.status.value}")
render_kpis(
    [
        ("Invoice Scope Restaurants", f"{len(result.restaurants):,}", "Identity-ready population"),
        ("Identity Ready", f"{result.identity_ready_restaurants:,}", "Included for evaluation"),
        ("With Orders", f"{result.restaurants_with_orders:,}", "Selected actual order dates"),
        ("No Orders", f"{result.no_orders_restaurants:,}", "Retained in population"),
        (
            "Settlement Ready",
            f"{result.restaurant_status_count(RestaurantSettlementStatus.READY):,}",
            "All inputs classified",
        ),
        (
            "Review Required",
            f"{sum(item.manual_review_orders > 0 for item in result.restaurants):,}",
            "At least one MANUAL_REVIEW order",
        ),
        (
            "Blocked",
            f"{sum(item.settlement_status in {RestaurantSettlementStatus.BLOCKED_COMMISSION, RestaurantSettlementStatus.BLOCKED_DATA} for item in result.restaurants):,}",
            "Commission or financial data",
        ),
    ]
)
render_kpis(
    [
        ("Orders", f"{result.settlement_evaluated_orders:,}", "Identity-ready scope"),
        ("Delivered", f"{result.pay_partner_orders:,}", "PAY_PARTNER"),
        ("Cancelled", f"{sum(item.cancelled_orders for item in result.restaurants):,}", "All cancellation responsibilities"),
        ("Manual Review", f"{result.manual_review_orders:,}", "No automatic resolution"),
    ]
)

st.markdown("### Restaurant settlements")
restaurants = list(result.restaurants)
table = pd.DataFrame(
    [
        {
            "Restaurant": item.restaurant_name,
            "Restaurant ID": item.restaurant_id,
            "Orders": item.total_orders,
            "Delivered": item.delivered_orders,
            "Cancelled": item.cancelled_orders,
            "Manual Review": item.manual_review_orders,
            "Commission": item.commission_rate,
            "Eligible Amount": item.eligible_partner_amount,
            "Commission Amount": item.commission_amount,
            "Status": item.settlement_status.value,
        }
        for item in restaurants
    ]
)
event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
if event.selection.rows:
    settlement_dialog(restaurants[event.selection.rows[0]])

with st.expander("Period reconciliation", expanded=True):
    render_kpis(
        [
            ("Canonical in period", f"{result.canonical_orders_in_period:,}", "Actual order date"),
            ("Invoice Scope orders", f"{result.invoice_scope_orders:,}", "Ready + explicitly blocked IDs"),
            ("Identity blocked orders", f"{result.identity_blocked_orders:,}", "Excluded from calculations"),
            ("Outside Invoice Scope", f"{result.outside_invoice_scope_orders:,}", "Not settlement-evaluated"),
        ]
    )
    st.dataframe(
        pd.DataFrame([item.model_dump() for item in result.money_reconciliation]),
        hide_index=True,
        width="stretch",
    )

with st.expander("Real Admin Earnings status profile"):
    st.markdown("#### Operational statuses")
    st.dataframe(
        pd.DataFrame([item.model_dump() for item in result.status_profile.operational_statuses]),
        hide_index=True,
        width="stretch",
    )
    st.markdown("#### Cancellation reasons")
    st.dataframe(
        pd.DataFrame([item.model_dump() for item in result.status_profile.cancellation_reasons]),
        hide_index=True,
        width="stretch",
    )

st.info(
    "Monetary calculations use certified policy cashco_legacy_v1. System decisions and "
    "append-only manual overrides remain independently auditable."
)
st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
