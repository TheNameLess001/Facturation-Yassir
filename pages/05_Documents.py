from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.documents.phase8 import (
    CashCoDocumentType,
    DocumentReadinessStatus,
    Phase8DocumentEngine,
)
from src.google.exceptions import GoogleIntegrationError
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=900, show_spinner="Preparing document readiness…")
def load_document_workspace(period_code: str):
    return load_phase5_workspace(period_code)


@st.dialog("Document preview", width="large")
def document_preview_dialog(restaurant, settlement) -> None:
    engine = Phase8DocumentEngine()
    document_type = st.selectbox(
        "Document type",
        list(CashCoDocumentType),
        format_func=lambda item: item.value,
    )
    preview = engine.preview(document_type, restaurant, settlement)
    st.error(preview.watermark)
    identity, legal, financial = st.tabs(
        ["Partner & Legal", "Financial Breakdown", "Readiness"]
    )
    with identity:
        st.write(
            {
                "Partner": preview.content["partner"],
                "Restaurant ID": preview.restaurant_id,
                "Period": preview.period_code,
                "Legal Entity": preview.content["legal_entity"],
                "ICE": preview.content["ice"],
                "IF": preview.content["if"],
                "RC": preview.content["rc"],
                "Address": preview.content["address"],
            }
        )
    with legal:
        st.write(
            {
                "Commission": preview.content["commission"],
                "Gross order value": preview.content["gross_order_value"],
                "Commission amount": "NOT VALIDATED",
                "HT": "NOT VALIDATED",
                "TVA": "NOT VALIDATED",
                "TTC": "NOT VALIDATED",
                "Note de débours": "NOT VALIDATED",
                "Net payable": "NOT VALIDATED",
            }
        )
    with financial:
        st.write(preview.readiness.model_dump())
    st.download_button(
        "Download local JSON preview",
        data=engine.render_local_preview(preview),
        file_name=(
            f"{preview.restaurant_id}_{preview.period_code}_"
            f"{preview.document_type.value}_v{preview.version}_DRAFT.json"
        ),
        mime="application/json",
    )
    st.caption(
        "Preview only. No Drive file is created and no production document number is reserved."
    )


page_setup("Documents")
st.title("Document Engine")
st.caption("Versioned previews · Legacy formula hard gate · No Drive creation")
settings = get_settings()
periods = SettlementPeriodService(settings.timezone)
today = datetime.now(ZoneInfo(settings.timezone)).date()
latest = periods.latest_complete(as_of=today)
controls = st.columns(3)
year = controls[0].selectbox(
    "Year", list(range(today.year, 2023, -1)), index=max(0, today.year - latest.year)
)
month = controls[1].selectbox(
    "Month",
    list(range(1, 13)),
    index=latest.month - 1,
    format_func=lambda value: calendar.month_name[value],
)
half = controls[2].selectbox(
    "Half", ["P1", "P2"], index=0 if latest.half == "P1" else 1
)
period_code = periods.create(year, month, half, as_of=today).period_code

try:
    workspace = load_document_workspace(period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Document workspace is unavailable: {exc}")
    st.stop()

settlements = {item.restaurant_id: item for item in workspace.summary.restaurants}
restaurants = {
    item.restaurant_id: item
    for item in workspace.registry.restaurants
    if item.restaurant_id in settlements
}
engine = Phase8DocumentEngine()
readiness = {
    restaurant_id: engine.readiness(restaurant, settlements[restaurant_id])
    for restaurant_id, restaurant in restaurants.items()
}
render_kpis(
    [
        (
            "Eligible",
            f"{sum(item.potentially_eligible for item in readiness.values()):,}",
            "Except legacy formula gate",
        ),
        (
            "Ready",
            f"{sum(item.status == DocumentReadinessStatus.READY for item in readiness.values()):,}",
            "Production generation allowed",
        ),
        ("Draft", "0", "Previews are generated on demand"),
        (
            "Blocked",
            f"{sum(item.status != DocumentReadinessStatus.READY for item in readiness.values()):,}",
            "Financial, legal, or formula gate",
        ),
        ("Generated", "0", "Drive creation disabled"),
    ]
)

st.error(
    "LEGACY FORMULA VALIDATION REQUIRED · Production documents are disabled. "
    "Shared Drive or delegated OAuth is also required before automatic Drive creation."
)

rows = [
    {
        "Restaurant": restaurants[restaurant_id].restaurant_name,
        "Restaurant ID": restaurant_id,
        "Period": period_code,
        "Invoice": "DRAFT PREVIEW",
        "Note de débours": "DRAFT PREVIEW",
        "Statement": "DRAFT PREVIEW",
        "Financial Validation": (
            "READY" if item.settlement_final else "REVIEW"
        ),
        "Legal Readiness": "READY" if item.legal_ready else "MISSING",
        "Status": item.status.value,
    }
    for restaurant_id, item in readiness.items()
]
st.markdown("### Document readiness")
event = st.dataframe(
    pd.DataFrame(rows),
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
if event.selection.rows:
    selected = rows[event.selection.rows[0]]["Restaurant ID"]
    document_preview_dialog(restaurants[selected], settlements[selected])

st.markdown("### Legacy Parity Workbench")
evidence = LegacyFormulaRegistry().discover()
st.dataframe(
    pd.DataFrame(
        [
            {
                "Financial Field": item.financial_field,
                "Formula": item.formula,
                "Evidence": item.evidence_source,
                "Source": item.source_file,
                "Location": item.source_location,
                "Confidence": item.confidence.value,
                "Status": "FORMULA_NOT_VALIDATED",
            }
            for item in evidence
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Legacy expected amounts can be compared after an authoritative reference is supplied. "
    "No 0.005 MAD ingestion tolerance is applied to document parity."
)
st.warning("AUTOMATION OFF · No document implies Admin authorization")
