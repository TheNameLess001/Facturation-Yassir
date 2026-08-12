from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.documents.phase8 import Phase8DocumentEngine
from src.google.exceptions import GoogleIntegrationError
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.dashboard_data import (
    dashboard_snapshot,
    period_trend,
    settlement_progress,
)
from src.ui.layout import page_setup, render_alerts, render_kpis


@st.cache_data(ttl=900, show_spinner="Loading real CashCo operations…")
def load_overview(period_code: str):
    return load_phase5_workspace(period_code)


settings = get_settings()
periods = SettlementPeriodService(settings.timezone)
today = datetime.now(ZoneInfo(settings.timezone)).date()
latest = periods.latest_complete(as_of=today)
period_options = [
    latest.period_code,
    periods.create(latest.year, latest.month, "P1", as_of=today).period_code,
]
period_options = list(dict.fromkeys(period_options))

page_setup("Overview", period_code=latest.period_code)
heading, selection = st.columns([3, 1])
with heading:
    st.title("Financial Operations")
    st.caption("Real settlement eligibility, review workload and document readiness")
with selection:
    period_code = st.selectbox("Settlement period", period_options)

try:
    workspace = load_overview(period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"CashCo overview is unavailable: {exc}")
    st.stop()

summary = workspace.summary
registry_by_id = {
    item.restaurant_id: item
    for item in workspace.registry.restaurants
    if item.restaurant_id is not None
}
document_engine = Phase8DocumentEngine()
document_readiness = tuple(
    document_engine.readiness(registry_by_id[item.restaurant_id], item)
    for item in summary.restaurants
    if item.restaurant_id in registry_by_id
)
snapshot = dashboard_snapshot(summary, document_readiness)

st.markdown(f"### {summary.period.display_name} · {snapshot.period_status.value}")
render_kpis(
    [
        ("Restaurants in Scope", f"{snapshot.restaurants_in_scope:,}", "Official Invoice Scope"),
        ("Identity Ready", f"{snapshot.identity_ready:,}", "Eligible for evaluation"),
        ("Settlement Ready", f"{snapshot.settlement_ready:,}", "No financial input blocker"),
        ("Orders Evaluated", f"{snapshot.orders_evaluated:,}", "Canonical orders only"),
        ("Manual Review", f"{snapshot.manual_review:,}", f"{snapshot.overrides_applied:,} overrides applied"),
        ("Documents Ready", f"{snapshot.documents_ready:,}", "Legacy formula gate active"),
    ]
)

chart, progress_column = st.columns([1.65, 1])
with chart:
    st.markdown("### Settlement Status / Orders Classification")
    classification = pd.DataFrame(
        {
            "Decision": [
                "PAY_PARTNER",
                "EXCLUDE",
                "YASSIR_COMPENSATION",
                "MANUAL_REVIEW",
            ],
            "Orders": [
                summary.pay_partner_orders,
                summary.excluded_orders,
                summary.yassir_compensation_orders,
                summary.manual_review_orders,
            ],
        }
    )
    st.bar_chart(
        classification.set_index("Decision"),
        color="#6747E8",
        horizontal=True,
    )
with progress_column:
    st.markdown("### Operational Progress")
    progress = pd.DataFrame(
        settlement_progress(summary, document_readiness),
        columns=["Stage", "Restaurants"],
    )
    st.bar_chart(progress.set_index("Stage"), color="#23866B", horizontal=True)
    st.caption("Email Ready remains 0. Email workflow is not implemented.")

st.markdown("### Attention Required")
render_alerts(
    [
        ("Identity Blockers", f"{snapshot.identity_blockers:,}", "coral"),
        ("Manual Reviews", f"{snapshot.manual_review:,}", "coral"),
        ("Commission Mismatches", f"{snapshot.commission_mismatches:,}", "violet"),
        ("Invalid Financial Rows", f"{snapshot.invalid_financial_rows:,}", "coral"),
        ("Missing Legal Data", f"{snapshot.missing_legal_data:,}", "violet"),
        ("Formula Validation Required", f"{snapshot.formula_validation_required:,}", "coral"),
    ]
)

st.markdown("### P1 / P2 Operations Trend")
summaries = [summary]
for candidate in period_options:
    if candidate != period_code:
        try:
            summaries.append(load_overview(candidate).summary)
        except (GoogleIntegrationError, ValueError, OSError):
            pass
trend = pd.DataFrame(item.__dict__ for item in period_trend(tuple(summaries)))
st.line_chart(
    trend.set_index("period_code")[["orders_evaluated", "pay_partner_orders"]],
    color=["#6747E8", "#23866B"],
)
st.dataframe(trend, hide_index=True, width="stretch")

st.markdown("### Automation Safety")
st.markdown(
    """<div class="cc-off-banner"><div class="cc-off-title">AUTOMATION OFF</div>
    <div class="cc-off-copy">WAITING FOR ADMIN AUTHORIZATION · No email, payment, production document, or automatic approval is enabled.</div></div>""",
    unsafe_allow_html=True,
)
if snapshot.formula_validation_required:
    st.error(
        "LEGACY FORMULA VALIDATION REQUIRED · Financial documents remain NOT VALIDATED."
    )
