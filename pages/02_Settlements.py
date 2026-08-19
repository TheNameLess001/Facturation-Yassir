from __future__ import annotations

import calendar
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.auth import AuthService
from src.config import get_settings
from src.documents.publishing import DocumentPublicationRepository
from src.google.exceptions import GoogleIntegrationError
from src.operations.billing import (
    BillingImpactPreview,
    BillingOperationsRepository,
    BillingPeriodControlService,
    BillingPeriodStatus,
)
from src.restaurants.registry_runtime import expire_partner_legal_master_cache
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import (
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)
from src.settlement.phase5_runtime import run_phase5_settlement
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=300, show_spinner="Evaluating the settlement period…")
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


page_setup("Billing Operations")
st.title("Billing Operations Center")
st.caption(
    "P1 / P2 execution · settlement, review, documents, validation and controlled locking"
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
    expire_partner_legal_master_cache()
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

publication_repository = DocumentPublicationRepository(
    settings.document_publication_registry_path
)
current_publications = tuple(
    publication_repository.current(active_period, item.restaurant_id, document_type)
    for item in result.restaurants
    for document_type in ("INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT")
)
published_documents = sum(item is not None for item in current_publications)
documents_ready = sum(
    item.financial_policy_version == "cashco_legacy_v1"
    and item.settlement_status == RestaurantSettlementStatus.READY
    for item in result.restaurants
)
impact = BillingImpactPreview.from_summary(
    result, document_count=documents_ready * 3
)
source_fingerprint = hashlib.sha256(
    json.dumps(
        [
            (
                item.restaurant_id,
                str(item.sales_ttc),
                str(item.invoice_ttc),
                str(item.net_payable),
                item.settlement_status.value,
            )
            for item in result.restaurants
        ],
        separators=(",", ":"),
    ).encode()
).hexdigest()
billing_repository = BillingOperationsRepository(
    settings.billing_operations_registry_path
)
billing_service = BillingPeriodControlService(billing_repository)
stored_period = billing_repository.latest(active_period)
if stored_period:
    operational_status = stored_period.status
elif published_documents == documents_ready * 3 and documents_ready:
    operational_status = BillingPeriodStatus.DOCUMENTS_PUBLISHED
elif result.manual_review_orders:
    operational_status = BillingPeriodStatus.TO_REVIEW
else:
    operational_status = BillingPeriodStatus.DATA_READY

st.markdown(
    f"### {result.period.display_name} · {operational_status.value}"
)
st.caption(
    f"{result.period.start_date:%d %b} → {result.period.end_date:%d %b} · "
    f"Last settlement recompute: {result.generated_at:%Y-%m-%d %H:%M UTC}"
)
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

st.markdown("### Billing funnel")
funnel = pd.DataFrame(
    [
        ("Scope", result.identity_ready_restaurants + result.identity_blocked_restaurants),
        ("Identity Ready", result.identity_ready_restaurants),
        ("Orders in Period", result.restaurants_with_orders),
        ("Settlement Evaluated", len(result.restaurants)),
        ("Financial Ready", impact.restaurant_count),
        (
            "Review Required",
            sum(item.manual_review_orders > 0 for item in result.restaurants),
        ),
        ("Documents Ready", documents_ready),
        ("Fully Published", published_documents // 3),
    ],
    columns=["Stage", "Restaurants"],
)
st.bar_chart(funnel.set_index("Stage"), horizontal=True, color="#6747E8")

st.markdown("### Period impact preview")
render_kpis(
    [
        ("Sales TTC", f"{impact.sales_ttc:,.2f} MAD", "Certified snapshot"),
        ("Sales HT", f"{impact.sales_ht:,.2f} MAD", "cashco_legacy_v1"),
        ("Commission HT", f"{impact.commission_ht:,.2f} MAD", "No intermediate rounding"),
        ("TVA", f"{impact.tva:,.2f} MAD", "20% certified"),
        ("Invoice TTC", f"{impact.invoice_ttc:,.2f} MAD", "Publication impact"),
        ("Net Payable", f"{impact.net_payable:,.2f} MAD", "Partner settlement"),
    ]
)
if impact.reconciliation_difference:
    st.error(
        f"Financial reconciliation failed: {impact.reconciliation_difference:.2f} MAD"
    )
else:
    st.success("Financial reconciliation · 0.00 MAD")

st.markdown("### Safe period actions")
action_tabs = st.tabs(
    ["Refresh / Recalculate", "Validate Period", "Lock Period", "Reopen"]
)
with action_tabs[0]:
    st.info(
        "Refresh Sources and Recalculate Readiness use the controls above. "
        "Generate Preview and R2 publication remain available in Documents."
    )
with action_tabs[1]:
    validate_phrase = st.text_input(
        f"Type VALIDATE {active_period}", key="billing_validate_phrase"
    )
    if st.button("Validate Period", disabled=validate_phrase != f"VALIDATE {active_period}"):
        try:
            billing_service.validate(
                user=AuthService(settings).current_user(),
                impact=impact,
                source_fingerprint=source_fingerprint,
                financial_policy_certified=True,
                source_snapshot_available=True,
                document_readiness_evaluated=True,
                critical_structural_blockers=0,
                review_items_classified=True,
                confirmation_text=validate_phrase,
            )
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("Period validated. Blocked restaurants remain explicitly excluded.")
            st.rerun()
with action_tabs[2]:
    lock_reason = st.text_area("Lock reason", key="billing_lock_reason")
    lock_phrase = st.text_input(f"Type LOCK {active_period}", key="billing_lock_phrase")
    if st.button(
        "Lock Period",
        disabled=not (lock_reason.strip() and lock_phrase == f"LOCK {active_period}"),
    ):
        try:
            billing_service.lock(
                user=AuthService(settings).current_user(),
                impact=impact,
                source_fingerprint=source_fingerprint,
                publication_state_known=True,
                confirmation_text=lock_phrase,
                reason=lock_reason,
            )
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("Period locked.")
            st.rerun()
with action_tabs[3]:
    reopen_reason = st.text_area("Reopen reason", key="billing_reopen_reason")
    reopen_phrase = st.text_input(
        f"Type REOPEN {active_period}", key="billing_reopen_phrase"
    )
    if st.button(
        "Reopen Period",
        disabled=not (
            reopen_reason.strip() and reopen_phrase == f"REOPEN {active_period}"
        ),
    ):
        try:
            billing_service.reopen(
                user=AuthService(settings).current_user(),
                impact=impact,
                source_fingerprint=source_fingerprint,
                confirmation_text=reopen_phrase,
                reason=reopen_reason,
            )
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("Period reopened through a controlled audit event.")
            st.rerun()
if billing_service.source_changed_after_lock(active_period, source_fingerprint):
    st.error("SOURCE_CHANGED_AFTER_LOCK · Controlled reopen is required.")
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
st.markdown("### Historical billing periods")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Period": item.period_code,
                "Restaurants": item.impact.restaurant_count,
                "Sales TTC": item.impact.sales_ttc,
                "Invoice TTC": item.impact.invoice_ttc,
                "Net Payable": item.impact.net_payable,
                "Documents": item.impact.document_count,
                "Status": item.status.value,
                "Validated / Locked At": item.occurred_at,
            }
            for item in billing_repository.history()
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.warning("GMAIL DEFERRED · PAYMENT EXECUTION DEFERRED")
