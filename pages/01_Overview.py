import streamlit as st

from src.ingestion.phase2_models import Phase2DiscoveryResult
from src.ingestion.phase2_runtime import discover_phase2_sources
from src.models.enums import HealthState
from src.ui.layout import page_setup, period_banner, render_kpis
from src.ui.mock_data import EMAIL_FUNNEL, KPI_ITEMS, WORKFLOW, billing_rows


@st.cache_data(ttl=60, show_spinner=False)
def load_compact_source_health() -> Phase2DiscoveryResult:
    return discover_phase2_sources()


page_setup("Overview")

left, *filters = st.columns([2.3, 1, 1, 1])
with left:
    st.selectbox(
        "Settlement period",
        ["2026-08-P1 · 01–15 August 2026", "2026-07-P2 · 16–31 July 2026"],
    )
with filters[0]:
    st.selectbox("City", ["All cities", "Casablanca", "Rabat", "Marrakech"])
with filters[1]:
    st.selectbox("Workflow", ["All statuses", "Ready", "Review", "Blocked"])
with filters[2]:
    st.text_input("Find partner", placeholder="Name or Restaurant ID")

period_banner()
st.markdown("### Financial control at a glance")
st.caption(
    "Mock data · The Phase 1 dashboard demonstrates the intended operating experience."
)
render_kpis(KPI_ITEMS)

st.markdown(
    '<div class="cc-section">Billing & communication status</div>',
    unsafe_allow_html=True,
)
st.dataframe(
    billing_rows(),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Gross": st.column_config.NumberColumn(format="%.0f MAD"),
        "Commission": st.column_config.NumberColumn(format="%.0f MAD"),
        "Adjustment": st.column_config.NumberColumn(format="%+.0f MAD"),
        "Net payable": st.column_config.NumberColumn(format="%.0f MAD"),
    },
)

workflow_col, email_col = st.columns(2)
with workflow_col:
    st.markdown('<div class="cc-section">Workflow status</div>', unsafe_allow_html=True)
    st.bar_chart(WORKFLOW.set_index("Status"), color="#6D4AFF", horizontal=True)
with email_col:
    st.markdown(
        '<div class="cc-section">Email readiness funnel</div>', unsafe_allow_html=True
    )
    st.bar_chart(EMAIL_FUNNEL.set_index("Stage"), color="#8C70FF", horizontal=True)

st.markdown('<div class="cc-section">Automation safety</div>', unsafe_allow_html=True)
st.markdown(
    """<div class="cc-off-banner"><div class="cc-off-title">AUTOMATION OFF</div>
<div class="cc-off-copy">WAITING FOR ADMIN AUTHORIZATION · No documents, drafts, or partner emails can be created for this period until an Admin reviews and explicitly authorizes the workflow.</div></div>""",
    unsafe_allow_html=True,
)

st.markdown('<div class="cc-section">Data source health</div>', unsafe_allow_html=True)
source_result = load_compact_source_health()
health_columns = st.columns(5)
source_cards = (
    ("Drive", source_result.connection_state.value, source_result.health.google_connection),
    ("Admin Earnings", f"{len(source_result.valid_admin_files)} files", source_result.health.admin_earnings),
    ("Finance", "Ready" if source_result.finance_tracking else "Not ready", source_result.health.finance_tracking),
    ("RST", "Ready" if source_result.rst_list else "Not ready", source_result.health.rst_list),
)
for column, (label, value, health) in zip(health_columns[:4], source_cards, strict=True):
    badge_tone = "success" if health == HealthState.HEALTHY else "danger" if health == HealthState.BLOCKING else "warning"
    with column:
        st.markdown(
            f'<div class="cc-card"><div class="cc-kpi-label">{label}</div>'
            f'<div style="margin-top:10px"><span class="cc-status {badge_tone}">{value}</span></div>'
            f'<div class="cc-kpi-note">REAL metadata · {health.value}</div></div>',
            unsafe_allow_html=True,
        )
with health_columns[4]:
    st.markdown(
        f'<div class="cc-card"><div class="cc-kpi-label">Last Drive check</div>'
        f'<div class="cc-kpi-value" style="font-size:1.1rem">{source_result.last_checked_at:%H:%M}</div>'
        '<div class="cc-kpi-note">UTC · cached for 60 seconds</div></div>',
        unsafe_allow_html=True,
    )
