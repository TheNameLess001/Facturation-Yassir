from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.google.exceptions import GoogleIntegrationError
from src.restaurants.registry_models import (
    CorrectionConfidence,
    MappingReviewCase,
    MappingStatus,
    NoIdClassification,
    RegisteredRestaurant,
)
from src.restaurants.registry_runtime import run_restaurant_registry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import run_phase5_settlement
from src.ui.layout import page_setup, render_kpis

IDENTITY_BLOCKER_STATUSES = frozenset(
    {
        MappingStatus.AMBIGUOUS,
        MappingStatus.UNMATCHED,
        MappingStatus.CONFLICTING_SCOPE,
        MappingStatus.SCOPE_ID_NAME_MISMATCH,
    }
)


@st.cache_data(ttl=900, show_spinner="Building the identity blocker workspace…")
def load_registry():
    return run_restaurant_registry()


@st.cache_data(ttl=900, show_spinner="Building the financial review queue…")
def load_financial_review(period_code: str):
    return run_phase5_settlement(period_code)


def invoice_scope_url() -> str:
    file_id = get_settings().invoice_scope_file_id
    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit?gid=0"


def candidate_frame(case: MappingReviewCase) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Candidate Restaurant ID": candidate.restaurant_id,
                "Candidate Restaurant Name": candidate.restaurant_name,
                "City": candidate.city,
                "Area": candidate.area,
                "Chain": candidate.chain,
                "Address": candidate.address,
                "RST Status": candidate.status,
                "Admin Orders": candidate.canonical_order_count,
                "Admin Restaurant Name": candidate.admin_restaurant_name,
                "Match Signals": " · ".join(candidate.similarity_indicators),
                "Confidence": candidate.confidence.value,
                "Advisory Score": candidate.advisory_score,
            }
            for candidate in case.candidates
        ]
    )


def scope_frame(case: MappingReviewCase) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Scope Row": row.source_row,
                "Restaurant ID": row.restaurant_id,
                "Restaurant Name": row.restaurant_name,
                "City": row.city,
                "Area": row.area,
                "Commission": row.commission_rate,
                "Phone": row.phone,
                "Email": row.email,
                "Comment": row.comment,
                **row.extra_fields,
            }
            for row in case.scope_rows
        ]
    )


def render_copy_fix(case: MappingReviewCase) -> None:
    fix = case.copy_fix
    st.markdown("#### Copy-friendly fix")
    columns = st.columns(3)
    with columns[0]:
        st.caption("Invoice Scope Row")
        st.code(str(fix.scope_row), language=None)
        st.caption("Current Restaurant ID")
        st.code(fix.current_restaurant_id or "EMPTY", language=None)
    with columns[1]:
        st.caption("Suggested Restaurant ID")
        st.code(fix.suggested_restaurant_id or "NO CANDIDATE", language=None)
        st.caption("Suggested canonical Restaurant Name")
        st.code(fix.suggested_restaurant_name or "—", language=None)
    with columns[2]:
        st.caption("Suggested City")
        st.code(fix.suggested_city or "—", language=None)
        st.caption("Advisory confidence")
        st.code(case.correction_confidence.value, language=None)
    st.success("Correct the CASH-CO source row manually, then click Refresh Google Sources.")


def render_financial_review() -> None:
    settings = get_settings()
    periods = SettlementPeriodService(settings.timezone)
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    latest = periods.latest_complete(as_of=today)
    controls = st.columns(4)
    year = controls[0].selectbox(
        "Year",
        list(range(today.year, 2023, -1)),
        index=max(0, today.year - latest.year),
        key="review_financial_year",
    )
    month = controls[1].selectbox(
        "Month",
        list(range(1, 13)),
        index=latest.month - 1,
        format_func=lambda value: calendar.month_name[value],
        key="review_financial_month",
    )
    half = controls[2].selectbox(
        "Half",
        ["P1", "P2"],
        index=0 if latest.half == "P1" else 1,
        key="review_financial_half",
    )
    selected = periods.create(year, month, half, as_of=today)
    with controls[3]:
        st.caption("Selected period")
        st.markdown(f"**{selected.period_code}** · {selected.status.value}")
    if st.button("Load Financial Review", type="primary"):
        st.session_state["financial_review_period"] = selected.period_code
    period_code = st.session_state.get("financial_review_period")
    if not period_code:
        st.info(
            f"Latest complete period: {latest.period_code}. Load a period to see "
            "system-derived review items."
        )
        return
    try:
        result = load_financial_review(period_code)
    except (GoogleIntegrationError, ValueError, OSError) as exc:
        st.error(f"Financial Review is unavailable: {exc}")
        return
    render_kpis(
        [
            ("MANUAL_REVIEW orders", f"{result.manual_review_orders:,}", "No automatic resolution"),
            ("Unknown statuses", f"{result.unknown_statuses:,}", "Unconfigured source status"),
            (
                "Unknown responsibility",
                f"{result.unknown_cancellation_responsibilities:,}",
                "Cancellation requires review",
            ),
            ("Commission mismatches", f"{result.commission_mismatches:,}", "Restaurant-level blocker"),
            ("Invalid financial rows", f"{result.invalid_financial_rows:,}", "Never coerced to zero"),
        ]
    )
    rows = []
    for restaurant in result.restaurants:
        if "COMMISSION_MISMATCH" in restaurant.issue_codes:
            rows.append(
                {
                    "Restaurant": restaurant.restaurant_name,
                    "Restaurant ID": restaurant.restaurant_id,
                    "Order ID": None,
                    "Order Date": None,
                    "Operational Status": None,
                    "Cancellation Reason": None,
                    "Responsibility": None,
                    "Suggested Financial Decision": None,
                    "Issue": "COMMISSION_MISMATCH",
                }
            )
        for order in restaurant.orders:
            if order.financial_decision.value != "MANUAL_REVIEW" and not any(
                code.startswith("INVALID_") for code in order.issue_codes
            ):
                continue
            rows.append(
                {
                    "Restaurant": restaurant.restaurant_name,
                    "Restaurant ID": restaurant.restaurant_id,
                    "Order ID": order.order_id,
                    "Order Date": order.order_date,
                    "Operational Status": order.source_order_status,
                    "Cancellation Reason": order.cancellation_reason,
                    "Responsibility": order.cancellation_responsibility.value,
                    "Suggested Financial Decision": order.financial_decision.value,
                    "Issue": " · ".join(order.issue_codes)
                    or order.decision_trace.decision_rule,
                }
            )
    st.markdown(f"### Financial Review · {period_code}")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.warning(
        "System-derived decisions only. Persistent manual reclassification is disabled until Phase 6."
    )


@st.dialog("Identity blocker review", width="large")
def mapping_dialog(
    case: MappingReviewCase,
    restaurant: RegisteredRestaurant,
) -> None:
    first = case.scope_rows[0]
    st.markdown(f"### {first.restaurant_name or 'Unnamed scope restaurant'}")
    st.warning("SUGGESTION — HUMAN VALIDATION REQUIRED")
    st.caption(
        "CashCo ranks candidates for review only. It never writes to Invoice Scope "
        "and never converts a fuzzy suggestion into a match."
    )

    st.markdown("#### Invoice Scope")
    st.dataframe(scope_frame(case), hide_index=True, width="stretch")
    if case.mapping_method == "RESTAURANT_ID_NOT_FOUND":
        st.error("ID NOT FOUND IN RST")

    if case.mapping_status == MappingStatus.CONFLICTING_SCOPE:
        st.error(
            f"{case.conflict_reason or 'OTHER'} · "
            f"{case.conflict_interpretation or 'UNCERTAIN'}"
        )
        st.caption(
            "Materially different fields: "
            f"{', '.join(case.conflict_fields) or 'not identified'}. "
            "All source rows remain visible; none was collapsed."
        )

    if case.mapping_status == MappingStatus.SCOPE_ID_NAME_MISMATCH:
        expected = case.scope_id_rst_candidate
        st.markdown("#### EXPECTED RESTAURANT FOR THIS ID")
        if expected:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Scope Restaurant ID": first.restaurant_id,
                            "Scope Restaurant Name": first.restaurant_name,
                            "RST Restaurant ID": expected.restaurant_id,
                            "RST canonical Restaurant Name": expected.restaurant_name,
                            "RST City": expected.city,
                            "RST Chain": expected.chain,
                            "Admin Earnings Restaurant Name": (
                                expected.admin_restaurant_name or "Not available"
                            ),
                        }
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        st.caption(
            "Decide whether the Restaurant ID is wrong or the Invoice Scope name is wrong."
        )

    st.markdown("#### RST candidates")
    if case.candidates:
        st.dataframe(
            candidate_frame(case),
            hide_index=True,
            width="stretch",
            column_config={
                "Advisory Score": st.column_config.ProgressColumn(
                    min_value=0.0,
                    max_value=1.0,
                    format="percent",
                )
            },
        )
    else:
        st.info("No useful RST candidate was generated for this case.")

    render_copy_fix(case)
    st.link_button("Open Invoice Scope", invoice_scope_url())
    st.caption(
        f"Current identity readiness: "
        f"{'READY' if restaurant.readiness.identity_ready else 'BLOCKING'} · "
        "blocked identities never enter settlement evaluation."
    )


page_setup("Review Queue")
header, action = st.columns([4, 1])
with header:
    st.title("Review Queue")
    st.caption(
        "Identity correction and financial eligibility review · Read-only Google sources"
    )
with action:
    if st.button("Refresh Google Sources", type="primary"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.session_state.pop("financial_review_period", None)
        st.rerun()

review_area = st.segmented_control(
    "Review area",
    ["IDENTITY REVIEW", "FINANCIAL REVIEW"],
    default="IDENTITY REVIEW",
)
if review_area == "FINANCIAL REVIEW":
    render_financial_review()
    st.info(
        "Invoice Scope, RST and Admin Earnings remain read-only. No decision is persisted."
    )
    st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
    st.stop()

try:
    registry = load_registry()
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Identity Blockers is unavailable: {exc}")
    st.stop()

case_pairs = list(zip(registry.mapping_cases, registry.restaurants, strict=True))
blocker_pairs = [
    pair for pair in case_pairs if pair[0].mapping_status in IDENTITY_BLOCKER_STATUSES
]

render_kpis(
    [
        ("Total blockers", f"{len(blocker_pairs):,}", "Identity only"),
        (
            "Ambiguous",
            f"{registry.mapping_count(MappingStatus.AMBIGUOUS):,}",
            "Competing RST identities",
        ),
        (
            "Unmatched",
            f"{registry.mapping_count(MappingStatus.UNMATCHED):,}",
            "No deterministic match",
        ),
        (
            "Conflicting",
            f"{registry.mapping_count(MappingStatus.CONFLICTING_SCOPE):,}",
            "Invoice Scope rows disagree",
        ),
        (
            "ID/name mismatch",
            f"{registry.mapping_count(MappingStatus.SCOPE_ID_NAME_MISMATCH):,}",
            "Existing ID contradicts name",
        ),
    ]
)

st.markdown("### Identity Quality")
quality = st.columns(4)
quality[0].metric(
    "Current",
    f"{registry.mapped_count:,} / {len(registry.restaurants):,} READY",
    f"{registry.mapping_completion:.2%}",
)
quality[1].metric("Target", "≥ 98%", "Identity readiness")
quality[2].metric("Blocking identity", f"{registry.blocking_mapping_issues:,}")
quality[3].metric(
    "READY_FOR_SETTLEMENT_ENGINE",
    "YES" if registry.ready_for_settlement_mapping else "NO",
    "Identity gate only",
)
st.progress(registry.mapping_completion)

confidence_counts = {
    confidence: sum(
        case.correction_confidence == confidence for case, _ in blocker_pairs
    )
    for confidence in (
        CorrectionConfidence.HIGH_CONFIDENCE,
        CorrectionConfidence.MEDIUM_CONFIDENCE,
        CorrectionConfidence.LOW_CONFIDENCE,
        CorrectionConfidence.NO_CANDIDATE,
    )
}
render_kpis(
    [
        ("High confidence", f"{confidence_counts[CorrectionConfidence.HIGH_CONFIDENCE]:,}", "Review first"),
        ("Medium confidence", f"{confidence_counts[CorrectionConfidence.MEDIUM_CONFIDENCE]:,}", "Multiple signals"),
        ("Low confidence", f"{confidence_counts[CorrectionConfidence.LOW_CONFIDENCE]:,}", "Weak advisory match"),
        ("No candidate", f"{confidence_counts[CorrectionConfidence.NO_CANDIDATE]:,}", "Manual RST research"),
    ]
)

filters = st.columns(3)
status_filter = filters[0].selectbox(
    "Blocker type",
    ["All", *(item.value for item in IDENTITY_BLOCKER_STATUSES)],
)
confidence_filter = filters[1].selectbox(
    "Correction confidence",
    [
        "All",
        CorrectionConfidence.HIGH_CONFIDENCE.value,
        CorrectionConfidence.MEDIUM_CONFIDENCE.value,
        CorrectionConfidence.LOW_CONFIDENCE.value,
        CorrectionConfidence.NO_CANDIDATE.value,
    ],
)
search = filters[2].text_input("Search", placeholder="Restaurant, ID or city")
visible_pairs = blocker_pairs
if status_filter != "All":
    visible_pairs = [
        pair for pair in visible_pairs if pair[0].mapping_status.value == status_filter
    ]
if confidence_filter != "All":
    visible_pairs = [
        pair
        for pair in visible_pairs
        if pair[0].correction_confidence.value == confidence_filter
    ]
if search:
    needle = search.casefold().strip()
    visible_pairs = [
        pair
        for pair in visible_pairs
        if any(
            needle in (value or "").casefold()
            for value in (
                pair[0].scope_rows[0].restaurant_name,
                pair[0].scope_rows[0].restaurant_id,
                pair[0].scope_rows[0].city,
            )
        )
    ]

blocker_table = pd.DataFrame(
    [
        {
            "Scope Row": case.scope_rows[0].source_row,
            "Invoice Scope Restaurant": case.scope_rows[0].restaurant_name,
            "Current Restaurant ID": case.scope_rows[0].restaurant_id,
            "City": case.scope_rows[0].city,
            "Mapping Status": case.mapping_status.value,
            "Confidence": case.correction_confidence.value,
            "RST Candidates": len(case.candidates),
            "Suggested Restaurant ID": case.copy_fix.suggested_restaurant_id,
            "Conflict Reason": case.conflict_reason,
            "Issue": " · ".join(case.issue_codes),
            "Action": "Review and correct CASH-CO manually",
        }
        for case, _ in visible_pairs
    ]
)
st.markdown(f"### Identity blockers · {len(visible_pairs):,}")
event = st.dataframe(
    blocker_table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
st.caption("Select a row to compare Invoice Scope with advisory RST candidates.")
if event.selection.rows:
    selected_case, selected_restaurant = visible_pairs[event.selection.rows[0]]
    mapping_dialog(selected_case, selected_restaurant)

st.markdown("### Rows without Restaurant ID")
no_id_counts = registry.no_id_row_counts()
render_kpis(
    [
        ("No-ID rows", f"{registry.scope_rows_without_restaurant_id:,}", "Source cells empty"),
        ("Exact-name mapped", f"{no_id_counts[NoIdClassification.EXACT_NAME_MAPPED]:,}", "ID can be copied safely"),
        ("Ambiguous", f"{no_id_counts[NoIdClassification.AMBIGUOUS]:,}", "Human choice required"),
        ("Unmatched", f"{no_id_counts[NoIdClassification.UNMATCHED]:,}", "No exact identity"),
        ("Conflict / duplicate", f"{no_id_counts[NoIdClassification.CONFLICTING_OR_DUPLICATE]:,}", "Source cleanup required"),
        ("Other", f"{no_id_counts[NoIdClassification.OTHER]:,}", "Inspect source"),
    ]
)
no_id_exact = [
    case
    for case, _ in case_pairs
    if case.identity_ready
    and case.mapping_method.startswith("EXACT_UNIQUE_NAME")
    and any(row.restaurant_id is None for row in case.scope_rows)
    and case.mapping_status != MappingStatus.DUPLICATE_SCOPE
]
with st.expander("Exact-name mappings that should receive a Restaurant ID"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Scope Row": case.copy_fix.scope_row,
                    "Invoice Scope Restaurant": case.scope_rows[0].restaurant_name,
                    "Restaurant ID to add": case.copy_fix.suggested_restaurant_id,
                    "RST canonical name": case.copy_fix.suggested_restaurant_name,
                    "City": case.copy_fix.suggested_city,
                }
                for case in no_id_exact
            ]
        ),
        hide_index=True,
        width="stretch",
    )

with st.expander("Readiness contract for the next phase"):
    st.markdown(
        f"- **IDENTITY_READY:** {len(registry.identity_ready_restaurants):,} restaurants may enter future settlement evaluation.\n"
        f"- **IDENTITY_BLOCKED:** {len(registry.identity_blocked_restaurants):,} restaurants remain excluded and visible here.\n"
        "- Missing RIB or legal data does not invalidate identity.\n"
        "- Only identity-ready restaurants enter Phase 5 financial eligibility."
    )

st.info(
    "CashCo is read-only against Invoice Scope, RST List, and Admin Earnings. "
    "No candidate is auto-applied and no Google artifact is created."
)
st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
