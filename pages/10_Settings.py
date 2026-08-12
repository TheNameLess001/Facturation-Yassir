from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError
from src.ingestion.phase2_models import Phase2DiscoveryResult
from src.ingestion.phase2_runtime import discover_phase2_sources
from src.ingestion.phase3_runtime import (
    load_latest_ingestion_summary,
    run_phase3_ingestion,
)
from src.models.enums import HealthState
from src.ui.layout import page_setup, period_banner, status_badge


@st.cache_resource(show_spinner=False)
def google_drive_client() -> GoogleDriveService | None:
    """Cache the API client, never the raw service-account JSON."""
    try:
        return GoogleDriveService(build_google_credentials(get_settings()))
    except GoogleIntegrationError:
        return None


@st.cache_data(ttl=120, show_spinner="Checking Google Drive metadata…")
def load_sources() -> Phase2DiscoveryResult:
    settings = get_settings()
    if not settings.google_credentials_configured:
        return discover_phase2_sources(settings)
    return discover_phase2_sources(settings, google_drive_client())


def health_tone(health: HealthState) -> str:
    return {
        HealthState.HEALTHY: "success",
        HealthState.WARNING: "warning",
        HealthState.BLOCKING: "danger",
        HealthState.UNKNOWN: "neutral",
    }[health]


page_setup("Data Sources")
period_banner()
st.title("Data Sources")
st.caption("Google Drive integration & ingestion readiness · Real metadata")

if st.button("Refresh Google Drive", type="primary"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()
st.caption("Refresh validates metadata and access only. It never parses order rows or writes to Drive.")

result = load_sources()
cards = (
    ("Google Connection", result.connection_state.value, result.health.google_connection),
    ("Admin Earnings", f"{len(result.valid_admin_files)} VALID FILES", result.health.admin_earnings),
    ("Finance Tracking", "CONNECTED" if result.finance_tracking else "NOT READY", result.health.finance_tracking),
    ("RST List", "CONNECTED" if result.rst_list else "NOT READY", result.health.rst_list),
    ("CashCo Workspace", "READY" if result.health.workspace == HealthState.HEALTHY else "CHECK ACCESS", result.health.workspace),
)
for column, (label, value, health) in zip(st.columns(5), cards, strict=True):
    with column:
        st.markdown(
            f'<div class="cc-card"><div class="cc-kpi-label">{label}</div>'
            f'<div style="margin-top:10px">{status_badge(value, health_tone(health))}</div>'
            f'<div class="cc-kpi-note">{health.value}</div></div>',
            unsafe_allow_html=True,
        )

if result.message:
    (st.error if result.health.overall.value in {"BLOCKING", "AUTH_ERROR"} else st.warning)(result.message)

st.markdown('<div class="cc-section">Admin Earnings inventory</div>', unsafe_allow_html=True)
if result.valid_admin_files:
    newest = max(result.valid_admin_files, key=lambda item: item.modified_at)
    latest_week = max(result.valid_admin_files, key=lambda item: (item.year, item.week_number))
    oldest_week = min(result.valid_admin_files, key=lambda item: (item.year, item.week_number))
    st.caption(
        f"Last modification: {newest.modified_at:%d %b %Y %H:%M UTC} · "
        f"Latest: week {latest_week.week_number}, {latest_week.year} · "
        f"Oldest: week {oldest_week.week_number}, {oldest_week.year}"
    )
    st.dataframe(
        pd.DataFrame(
            {
                "File": item.filename,
                "Week": item.week_number,
                "Year": item.year,
                "Modified": item.modified_at,
                "Size": item.size,
                "State": item.change_state.value,
            }
            for item in result.valid_admin_files
        ),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No valid Admin Earnings files are currently discoverable.")

with st.expander(f"Ignored files ({len(result.ignored_admin_files)})"):
    if result.ignored_admin_files:
        st.dataframe(
            [{"Filename": item.filename, "Reason": item.reason.value} for item in result.ignored_admin_files],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No files were ignored.")

st.markdown('<div class="cc-section">Admin Earnings ingestion</div>', unsafe_allow_html=True)
summary = load_latest_ingestion_summary()
if summary:
    ingestion_cards = (
        ("Sources processed", str(summary.sources_read)),
        ("Raw rows", f"{summary.raw_rows:,}"),
        ("Canonical orders", f"{summary.canonical_orders:,}"),
        ("Duplicates removed", f"{summary.identical_duplicate_rows:,}"),
        ("Conflicts", f"{summary.conflicting_order_ids:,}"),
        ("Blocking issues", f"{summary.blocking_issues:,}"),
    )
    for column, (label, value) in zip(st.columns(6), ingestion_cards, strict=True):
        column.metric(label, value)
    st.caption(f"Last ingestion: {summary.completed_at:%d %b %Y · %H:%M UTC} · Run {summary.run_id}")
else:
    st.info("No published Phase 3 ingestion summary is available yet.")

if st.button("Run Admin Earnings ingestion", disabled=result.health.admin_earnings != HealthState.HEALTHY):
    with st.spinner("Reading and normalizing Admin Earnings sources…"):
        ingestion = run_phase3_ingestion()
    st.cache_data.clear()
    st.success(
        f"Ingestion complete: {ingestion.summary.canonical_orders:,} canonical orders; "
        f"{ingestion.summary.conflicting_order_ids:,} conflicts isolated."
    )
    st.rerun()
st.caption("This action reads Admin Earnings and publishes validated processed artifacts only. It does not run Finance, RST, settlement, document, or email logic.")

if summary and summary.blocking_issues:
    with st.expander("Ingestion issues"):
        st.warning(
            f"{summary.conflicting_order_ids:,} conflicting Order IDs require REVIEW_QUEUE; "
            f"{summary.invalid_financial_values:,} invalid financial values remain null; "
            f"{summary.missing_order_id_rows:,} rows have missing Order IDs."
        )

st.markdown('<div class="cc-section">Drive access matrix</div>', unsafe_allow_html=True)
for item in result.access:
    read = "READ" if item.readable else "NO READ"
    write = " / WRITE" if item.writable else (" / NO WRITE" if item.writable is False else "")
    st.markdown(f"**{item.location}** &nbsp; {status_badge(read + write, 'success' if item.readable and item.writable is not False else 'danger')}", unsafe_allow_html=True)
    if item.message:
        st.caption(item.message)

with st.expander("Technical details"):
    st.caption("Drive IDs are shown only in this Admin detail view.")
    for item in result.access:
        st.code(f"{item.location}: {item.object_id or 'NOT_CONFIGURED'}")
    st.caption(f"Last refresh: {result.last_checked_at:%d %b %Y · %H:%M:%S UTC}")

st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
