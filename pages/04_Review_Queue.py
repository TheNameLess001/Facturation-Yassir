from __future__ import annotations

import pandas as pd
import streamlit as st

from src.google.exceptions import GoogleIntegrationError
from src.restaurants.registry_runtime import run_restaurant_registry
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=900, show_spinner="Loading restaurant issues…")
def load_registry():
    return run_restaurant_registry()


page_setup("Review Queue")
st.title("Review Queue")
st.caption("Real Invoice Scope and Restaurant Registry data-quality issues")

try:
    registry = load_registry()
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Review Queue is unavailable: {exc}")
    st.stop()

codes = (
    "UNMATCHED_SCOPE_RESTAURANT",
    "AMBIGUOUS_RESTAURANT_MAPPING",
    "DUPLICATE_SCOPE_ROW",
    "CONFLICTING_SCOPE_ROW",
    "MISSING_EMAIL",
    "MISSING_RIB",
    "MISSING_ICE",
    "MISSING_LEGAL_ENTITY",
    "MISSING_COMMISSION",
)
render_kpis(
    [
        (code.replace("_", " ").title(), f"{registry.issue_count(code):,}", "REAL registry issue")
        for code in codes
    ]
)

filter_columns = st.columns(3)
selected_code = filter_columns[0].selectbox("Issue", ["All", *codes])
selected_severity = filter_columns[1].selectbox(
    "Severity", ["All", "BLOCKING", "WARNING", "INFO"]
)
search = filter_columns[2].text_input("Search", placeholder="Restaurant or ID")
issues = list(registry.issues)
if selected_code != "All":
    issues = [item for item in issues if item.code == selected_code]
if selected_severity != "All":
    issues = [item for item in issues if item.severity.value == selected_severity]
if search:
    needle = search.casefold().strip()
    issues = [
        item
        for item in issues
        if needle in (item.restaurant_name or "").casefold()
        or needle in (item.restaurant_id or "").casefold()
    ]

st.dataframe(
    pd.DataFrame(
        [
            {
                "Issue": item.code,
                "Severity": item.severity.value,
                "Restaurant": item.restaurant_name,
                "Restaurant ID": item.restaurant_id,
                "Invoice Scope Row": item.scope_source_row,
                "Description": item.message,
            }
            for item in issues
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "No manual mapping or source edit is persisted in this phase. Ambiguous and "
    "unmatched restaurants remain blocking for future billing."
)
st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
