from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.ingestion.admin_earnings_runtime import run_configured_admin_earnings_ingestion
from src.models.enums import DuplicateKind, IngestionStatus
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Admin Earnings Ingestion")
period_banner()
st.title("Admin Earnings Ingestion")
st.caption(
    "Read-only schema validation and duplicate diagnostics · No eligibility filtering or settlement calculation."
)

settings = get_settings()
connected_mode = settings.google_auth_mode in {"SERVICE_ACCOUNT", "ADC"}
st.markdown(
    """<div class="cc-panel"><div class="cc-kpi-label">Phase 3 processing boundary</div>
    <div class="cc-kpi-note" style="margin-top:8px">Downloads configured CSV/XLSX sources in memory, maps source columns to a canonical transaction schema, validates required values, and detects duplicate Order IDs. It does not persist processed orders or determine partner eligibility.</div></div>""",
    unsafe_allow_html=True,
)

with st.expander("Canonical schema and mapping rules"):
    st.markdown(
        "Required: `order_id`, `restaurant_id`, `order_date`, `gross_amount`, `operational_status`. "
        "Optional: `restaurant_name`, `cancellation_reason`. Exact source headings can be configured "
        "with `CASHCO_ADMIN_EARNINGS_COLUMN_MAP`. Operational status is preserved as source semantics; "
        "no financial decision is created."
    )

if not connected_mode:
    st.warning(
        f"Drive mode is {settings.google_auth_mode}. Configure a real read-only connection before running validation."
    )

if st.button(
    "Validate Admin Earnings Sources",
    type="primary",
    disabled=not connected_mode,
    help="Explicit read-only validation. It never edits Drive files.",
):
    with st.spinner("Reading and validating Admin Earnings sources…"):
        st.session_state["admin_earnings_ingestion_result"] = (
            run_configured_admin_earnings_ingestion()
        )

result = st.session_state.get("admin_earnings_ingestion_result")
if result is None:
    st.info("Validation has not run in this session. No source rows were downloaded.")
else:
    status_tone = {
        IngestionStatus.SUCCESS: "success",
        IngestionStatus.COMPLETED_WITH_WARNINGS: "warning",
        IngestionStatus.BLOCKED: "danger",
        IngestionStatus.NOT_RUN: "neutral",
    }[result.status]
    st.markdown(
        f'<div class="cc-section">Latest validation · <span class="cc-status {status_tone}">{result.status.value}</span></div>',
        unsafe_allow_html=True,
    )
    exact = sum(item.kind == DuplicateKind.EXACT for item in result.duplicates)
    conflicts = sum(
        item.kind == DuplicateKind.CONFLICTING for item in result.duplicates
    )
    render_kpis(
        [
            (
                "Files checked",
                str(len(result.file_results)),
                "Configured Admin Earnings sources",
            ),
            ("Rows read", f"{result.rows_read:,}", "Before validation"),
            (
                "Canonical records",
                f"{len(result.records):,}",
                "Not persisted in Phase 3",
            ),
            ("Exact duplicates", str(exact), "Collapsed deterministically"),
            ("Conflicts", str(conflicts), "Blocking · no version accepted"),
        ]
    )
    if result.file_results:
        st.markdown(
            '<div class="cc-section">File validation</div>', unsafe_allow_html=True
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "File": item.source_filename,
                        "Rows read": item.rows_read,
                        "Rows valid": item.rows_valid,
                        "Columns mapped": len(item.detected_columns),
                        "Issues": len(item.issues),
                    }
                    for item in result.file_results
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if result.duplicates:
        st.markdown(
            '<div class="cc-section">Duplicate diagnostics</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Order ID": item.order_id,
                        "Type": item.kind.value,
                        "Occurrences": item.occurrences,
                        "Locations": " · ".join(item.source_locations),
                    }
                    for item in result.duplicates
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if result.issues:
        with st.expander(f"Validation issues ({len(result.issues)})", expanded=True):
            for issue in result.issues:
                location = (
                    f" · {issue.source_filename}:row {issue.source_row_number}"
                    if issue.source_filename and issue.source_row_number
                    else f" · {issue.source_filename}"
                    if issue.source_filename
                    else ""
                )
                st.markdown(
                    f"- **{issue.level.value} · {issue.code}**{location} — {issue.message}"
                )

st.markdown('<div class="cc-section">Downstream controls</div>', unsafe_allow_html=True)
st.error(
    "Invoice Scope registry diagnostics are separate. Settlement calculations, documents, and communication remain unavailable. Automation is OFF."
)
