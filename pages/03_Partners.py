from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.ingestion.eligibility_runtime import run_configured_eligibility
from src.models.enums import IngestionStatus
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Payment Scope Eligibility")
period_banner()
st.title("Payment Scope Eligibility")
st.caption(
    "Official partner population control · Restaurant ID is the only eligibility key."
)

period_id = st.selectbox(
    "Settlement period",
    ["2026-08-P1", "2026-08-P2"],
    help="The selected period determines which versioned Payment Scope file is used.",
)
settings = get_settings()
connected_mode = settings.google_auth_mode in {"SERVICE_ACCOUNT", "ADC"}

st.markdown(
    """<div class="cc-panel"><div class="cc-kpi-label">Eligibility control</div>
    <div class="cc-kpi-note" style="margin-top:8px">Payment Scope is the sole eligibility source. RST List is enrichment only. Restaurant names, RIBs, emails, and Admin Earnings presence cannot make a partner eligible.</div></div>""",
    unsafe_allow_html=True,
)

if not connected_mode:
    st.warning(
        f"Drive mode is {settings.google_auth_mode}. Configure a real read-only connection before evaluating eligibility."
    )

if st.button(
    "Validate Scope & Evaluate Eligibility",
    type="primary",
    disabled=not connected_mode,
    help="Explicit read-only operation. No settlement or partner master is created.",
):
    with st.spinner("Validating the period scope and matching Restaurant IDs…"):
        st.session_state["eligibility_result"] = run_configured_eligibility(period_id)

result = st.session_state.get("eligibility_result")
if result is None or result.period_id != period_id:
    st.info(
        "Eligibility has not been evaluated for this period in the current session."
    )
else:
    status_tone = {
        IngestionStatus.SUCCESS: "success",
        IngestionStatus.COMPLETED_WITH_WARNINGS: "warning",
        IngestionStatus.BLOCKED: "danger",
        IngestionStatus.NOT_RUN: "neutral",
    }[result.status]
    st.markdown(
        f'<div class="cc-section">Latest evaluation · <span class="cc-status {status_tone}">{result.status.value}</span></div>',
        unsafe_allow_html=True,
    )
    scope = result.scope_snapshot
    render_kpis(
        [
            (
                "Scoped partners",
                f"{scope.restaurant_count:,}" if scope else "—",
                "Unique Restaurant IDs in official scope",
            ),
            (
                "Eligible orders",
                f"{len(result.eligible_orders):,}",
                "Restaurant ID found in scope",
            ),
            (
                "Out-of-scope orders",
                f"{len(result.out_of_scope_orders):,}",
                "Excluded before later processing",
            ),
            (
                "Eligible restaurants",
                f"{len(result.eligible_restaurant_ids):,}",
                "Observed in Admin Earnings and scope",
            ),
            (
                "Out-of-scope restaurants",
                f"{len(result.out_of_scope_restaurant_ids):,}",
                "Observed in Admin Earnings only",
            ),
        ]
    )
    if scope:
        st.markdown(
            '<div class="cc-section">Scope version</div>', unsafe_allow_html=True
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Period": scope.period_id,
                        "File": scope.filename,
                        "Drive File ID": scope.drive_file_id,
                        "Drive Modified": scope.drive_modified_at,
                        "Restaurant IDs": scope.restaurant_count,
                        "Content Hash": scope.content_hash[:16] + "…",
                        "Snapshot": scope.snapshot_id[:16] + "…",
                    }
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if result.out_of_scope_orders:
        st.markdown(
            '<div class="cc-section">Out-of-scope diagnostics</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Order ID": item.order.order_id,
                        "Restaurant ID": item.order.restaurant_id,
                        "Restaurant Name (diagnostic only)": item.order.restaurant_name,
                        "Source": item.order.source_filename,
                        "Reason": item.reason,
                    }
                    for item in result.out_of_scope_orders[:500]
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        if len(result.out_of_scope_orders) > 500:
            st.caption("Showing the first 500 out-of-scope orders.")
    if result.issues:
        with st.expander(f"Validation issues ({len(result.issues)})", expanded=True):
            for issue in result.issues:
                st.markdown(
                    f"- **{issue.level.value} · {issue.code}:** {issue.message}"
                )

st.markdown('<div class="cc-section">Phase boundary</div>', unsafe_allow_html=True)
st.error(
    "No RST enrichment, settlement-period assignment, financial decisions, Sheets, documents, email, or payment actions are available in Phase 4. Automation remains OFF."
)
