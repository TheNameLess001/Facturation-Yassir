from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.documents.legal_readiness import (
    DocumentLegalPolicy,
    DocumentLegalStatus,
)
from src.documents.phase8 import (
    CashCoDocumentType,
    DocumentReadinessStatus,
    Phase8DocumentEngine,
)
from src.google.exceptions import GoogleIntegrationError
from src.restaurants.legal_master import PartnerLegalMasterSource
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.settlement.reference_import import (
    HistoricalReferenceImporter,
    ReferenceArtifactType,
)
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=300, show_spinner="Preparing document readiness…")
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
    if preview.readiness.financial_formulas_validated:
        st.info(preview.watermark)
    else:
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
                "Sales TTC": preview.content["sales_ttc"],
                "Sales HT / Commission base": preview.content["sales_ht"],
                "Commission HT": preview.content["commission_amount"],
                "TVA": preview.content["invoice_tva"],
                "Invoice TTC": preview.content["invoice_ttc"],
                "Note de débours payable": preview.content["note_de_debours"],
                "Net payable": preview.content["final_net_payable"],
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


@st.dialog("Legal data review", width="large")
def legal_review_dialog(restaurant, settlement) -> None:
    policy = DocumentLegalPolicy()
    results = policy.evaluate_package(restaurant)
    st.markdown(f"### {restaurant.restaurant_name or restaurant.restaurant_id}")
    st.caption(
        f"{restaurant.restaurant_id} · Canonical identity · Read-only source diagnostics"
    )
    st.write(
        {
            "Legal Master Review": restaurant.legal_master_review_status,
            "Finance Email": restaurant.finance_email or "RST email fallback",
            "RIB Status": restaurant.payment_readiness_status.value,
            "Masked RIB": PartnerLegalMasterSource.mask_rib(restaurant.rib),
            "Payment Ready": restaurant.readiness.payment_ready,
        }
    )
    for result in results:
        st.markdown(f"#### {result.document_type.value} · {result.status.value}")
        st.write(
            {
                "Partner Name": result.document_partner_name,
                "Name Source": (
                    result.document_partner_name_source.value
                    if result.document_partner_name_source
                    else "MISSING"
                ),
                "Required": ", ".join(result.required_fields),
                "Missing Required": ", ".join(result.missing_required_fields) or "None",
                "Optional Missing": ", ".join(result.optional_missing_fields) or "None",
            }
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Field": item.field,
                        "Value": item.value,
                        "Source": item.source or "NOT AVAILABLE",
                        "Requirement": "REQUIRED" if item.required else "OPTIONAL",
                        "Validation": item.status.value,
                    }
                    for item in result.field_traces
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    readiness = Phase8DocumentEngine().readiness(restaurant, settlement)
    st.write(
        {
            "Financial Status": settlement.settlement_status.value,
            "Financial Policy": settlement.financial_policy_version,
            "Document Readiness": readiness.status.value,
            "Drive Publishing": Phase8DocumentEngine.DRIVE_PUBLISHING_STATUS,
        }
    )
    st.caption("No source field can be edited from this review dialog.")


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
document_candidates = {
    restaurant_id: {
        document_type: engine.production_candidate(
            document_type,
            restaurants[restaurant_id],
            settlements[restaurant_id],
        )
        for document_type in CashCoDocumentType
    }
    for restaurant_id in restaurants
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

st.success(
    "FINANCIAL POLICY CERTIFIED · cashco_legacy_v1 · Formula blocker removed. "
    "Legal, review, commission and data-quality gates remain independent."
)

rows = [
    {
        "Restaurant": restaurants[restaurant_id].restaurant_name,
        "Restaurant ID": restaurant_id,
        "Period": period_code,
        "Invoice": document_candidates[restaurant_id][
            CashCoDocumentType.INVOICE
        ].status.value,
        "Note de débours": document_candidates[restaurant_id][
            CashCoDocumentType.NOTE_DE_DEBOURS
        ].status.value,
        "Statement": document_candidates[restaurant_id][
            CashCoDocumentType.PARTNER_STATEMENT
        ].status.value,
        "Financial Validation": (
            "READY" if item.settlement_final else "REVIEW"
        ),
        "Legal Readiness": item.legal_status.value,
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

st.markdown("### Legal Data Review")
legal_policy = DocumentLegalPolicy()
legal_results = {
    restaurant_id: legal_policy.evaluate_package(restaurant)
    for restaurant_id, restaurant in restaurants.items()
}


def package_legal_status(results):
    if any(item.status == DocumentLegalStatus.BLOCKED for item in results):
        return DocumentLegalStatus.BLOCKED
    if any(item.status == DocumentLegalStatus.READY_WITH_WARNINGS for item in results):
        return DocumentLegalStatus.READY_WITH_WARNINGS
    return DocumentLegalStatus.READY


package_statuses = {
    restaurant_id: package_legal_status(results)
    for restaurant_id, results in legal_results.items()
}
render_kpis(
    [
        (
            "Document Candidates",
            f"{sum(item.financial_policy_version == 'cashco_legacy_v1' for item in settlements.values()):,}",
            "Financially calculable",
        ),
        (
            "Legal Ready",
            f"{sum(item == DocumentLegalStatus.READY for item in package_statuses.values()):,}",
            "No legal warning",
        ),
        (
            "Ready With Warnings",
            f"{sum(item == DocumentLegalStatus.READY_WITH_WARNINGS for item in package_statuses.values()):,}",
            "Approved optional fields",
        ),
        (
            "Legal Blocked",
            f"{sum(item == DocumentLegalStatus.BLOCKED for item in package_statuses.values()):,}",
            "Missing required field",
        ),
        (
            "Missing Address",
            f"{sum(not item.address for item in restaurants.values()):,}",
            "Blocking for Invoice / Débours",
        ),
        (
            "Missing ICE",
            f"{sum(not item.ice for item in restaurants.values()):,}",
            "Optional warning",
        ),
        (
            "Missing Raison Sociale",
            f"{sum(not item.legal_entity for item in restaurants.values()):,}",
            "Restaurant Name fallback",
        ),
        (
            "Invalid Legal Fields",
            f"{sum(any(result.invalid_fields for result in results) for results in legal_results.values()):,}",
            "Visible diagnostic",
        ),
    ]
)
legal_rows = []
for restaurant_id, restaurant in restaurants.items():
    by_type = {item.document_type: item for item in legal_results[restaurant_id]}
    invoice_legal = by_type[CashCoDocumentType.INVOICE]
    blockers = tuple(
        dict.fromkeys(
            field
            for item in by_type.values()
            for field in item.missing_required_fields
        )
    )
    legal_rows.append(
        {
            "Restaurant": restaurant.restaurant_name,
            "Restaurant ID": restaurant_id,
            "City": restaurant.city,
            "Partner Name": invoice_legal.document_partner_name,
            "Name Source": (
                invoice_legal.document_partner_name_source.value
                if invoice_legal.document_partner_name_source
                else "MISSING"
            ),
            "Address": restaurant.address,
            "ICE": restaurant.ice or "MISSING",
            "IF": restaurant.if_number or "MISSING",
            "RC": restaurant.rc or "MISSING",
            "RIB Status": "AVAILABLE · MASKED" if restaurant.rib else "MISSING",
            "Invoice Legal Status": by_type[CashCoDocumentType.INVOICE].status.value,
            "Débours Legal Status": by_type[
                CashCoDocumentType.NOTE_DE_DEBOURS
            ].status.value,
            "Statement Legal Status": by_type[
                CashCoDocumentType.PARTNER_STATEMENT
            ].status.value,
            "Blocking Fields": " · ".join(blockers) or "None",
        }
    )
legal_event = st.dataframe(
    pd.DataFrame(legal_rows),
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="legal_data_review_table",
)
if legal_event.selection.rows:
    selected = legal_rows[legal_event.selection.rows[0]]["Restaurant ID"]
    legal_review_dialog(restaurants[selected], settlements[selected])

st.markdown("### Financial Formula Certification")
formula_registry = LegacyFormulaRegistry()
report = formula_registry.evidence_report()
certification = formula_registry.certification()
st.caption(
    "Source · 4_Generateur bulk.py · PRODUCTION_SOURCE_CODE · "
    "BUSINESS_OWNER_CONFIRMED"
)
render_kpis(
    [
        (
            "Policy Version",
            certification.policy_version or "NOT ASSIGNED",
            "Approved monetary policy",
        ),
        ("Evidence", "AUTHORITATIVE", "Production source code"),
        ("Parity Cases", str(certification.parity_cases), "Optional regression evidence"),
        (
            "Source Reconstructions",
            str(certification.source_reconstructed_cases),
            "Optional additional validation",
        ),
        ("Matches", str(certification.parity_matches), "Exact legacy precision"),
        ("Mismatches", str(certification.parity_mismatches), "Never silently tolerated"),
        ("Certification", certification.status.value, "Production hard gate"),
    ]
)
evidence = report.evidence
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
                "Evidence Type": item.evidence_type.value if item.evidence_type else "—",
                "Business Approval": item.approval.value,
                "Category": item.category.value if item.category else "—",
                "Status": "CERTIFIED",
            }
            for item in evidence
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Historical reconstruction is optional additional regression validation. It can rebuild: "
    "partner amount → commission base → commission → HT → TVA → TTC → note de "
    "débours → net payable. The approved production source and deterministic source-code "
    "tests certify the policy even when no historical settlement artifact is loaded."
)

with st.expander("Import a local historical reference · no persistence"):
    st.caption(
        "PDFs are accepted for later human review without OCR. Structured CSV/Excel "
        "references are profiled by schema and row count; row values are not retained."
    )
    uploaded = st.file_uploader(
        "Reference document",
        type=["pdf", "csv", "xlsx", "xls"],
        accept_multiple_files=False,
    )
    pdf_kind = st.selectbox(
        "PDF reference type",
        [
            ReferenceArtifactType.PDF_INVOICE,
            ReferenceArtifactType.PDF_NOTE_DE_DEBOURS,
        ],
        format_func=lambda item: item.value,
    )
    if uploaded is not None:
        try:
            profile = HistoricalReferenceImporter().inspect(
                uploaded.name,
                uploaded.getvalue(),
                pdf_type=pdf_kind,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.write(
                {
                    "Filename": profile.filename,
                    "Type": profile.artifact_type.value,
                    "Status": profile.status.value,
                    "Size": profile.size,
                    "SHA-256": profile.content_sha256,
                    "Sheets": len(profile.sheets),
                }
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Sheet": sheet.name,
                            "Columns": " · ".join(sheet.columns),
                            "Rows": sheet.row_count,
                        }
                        for sheet in profile.sheets
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            st.success("Inspected in memory. Nothing was uploaded to Drive or committed.")
st.warning("AUTOMATION OFF · No document implies Admin authorization")
