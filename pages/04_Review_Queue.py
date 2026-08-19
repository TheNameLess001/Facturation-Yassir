from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.auth import AuthService
from src.config import get_settings
from src.documents.publishing import DocumentPublicationRepository
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.exceptions import GoogleIntegrationError
from src.models.enums import FinancialDecision
from src.operations.review import (
    ReviewCenterBuilder,
    ReviewRepository,
    ReviewSeverity,
    ReviewStatus,
)
from src.restaurants.registry_models import (
    CorrectionConfidence,
    MappingReviewCase,
    MappingStatus,
    NoIdClassification,
    RegisteredRestaurant,
)
from src.restaurants.registry_runtime import (
    expire_partner_legal_master_cache,
    run_restaurant_registry,
)
from src.settlement.overrides import (
    FinancialOverrideRepository,
    FinancialOverrideService,
    OverrideReasonCode,
)
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis

IDENTITY_BLOCKER_STATUSES = frozenset(
    {
        MappingStatus.AMBIGUOUS,
        MappingStatus.UNMATCHED,
        MappingStatus.CONFLICTING_SCOPE,
        MappingStatus.SCOPE_ID_NAME_MISMATCH,
    }
)


@st.cache_data(ttl=300, show_spinner="Building the identity blocker workspace…")
def load_registry():
    return run_restaurant_registry()


@st.cache_data(ttl=300, show_spinner="Building the financial review queue…")
def load_financial_review(period_code: str):
    return load_phase5_workspace(period_code)


@st.dialog("Financial decision review", width="large")
def financial_review_dialog(order, restaurant) -> None:
    st.markdown(f"### {restaurant.restaurant_name or restaurant.restaurant_id}")
    st.caption(f"Order {order.order_id} · {order.order_date}")
    source, decision, financial = st.columns(3)
    with source:
        st.markdown("#### Source")
        st.write(
            {
                "Operational status": order.source_order_status,
                "Cancellation reason": order.cancellation_reason,
                "Responsibility": order.cancellation_responsibility.value,
            }
        )
    with decision:
        st.markdown("#### Decision trace")
        st.write(
            {
                "System decision": order.system_financial_decision.value,
                "Final decision": order.final_financial_decision.value,
                "Rule": order.decision_trace.decision_rule,
                "Source fields": order.decision_trace.source_fields_used,
                "Engine": order.decision_trace.engine_version,
            }
        )
    with financial:
        st.markdown("#### Financial fields")
        st.write(
            {
                "Order amount": order.order_amount,
                "Promo": order.promo_amount,
                "Delivery fee": order.delivery_fee,
                "Source commission": order.source_commission_amount,
            }
        )
    settings = get_settings()
    repository = FinancialOverrideRepository(
        settings.financial_override_registry_path
    )
    history = repository.list_for_order(restaurant.period_code, order.order_id)
    with st.expander(f"Override history · {len(history)}"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Previous": item.previous_decision.value,
                        "New": item.new_decision.value,
                        "Reason": item.reason_code.value,
                        "Who": item.created_by,
                        "When": item.created_at,
                        "Why": item.comment,
                        "Supersedes": item.supersedes_override_id,
                    }
                    for item in history
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    st.markdown("#### Override Decision")
    new_decision = st.selectbox(
        "Final decision",
        list(FinancialDecision),
        format_func=lambda item: item.value,
    )
    reason = st.selectbox(
        "Reason code",
        list(OverrideReasonCode),
        format_func=lambda item: item.value,
    )
    comment = st.text_area(
        "Comment",
        help="Required when reason is OTHER.",
    )
    confirmed = st.checkbox(
        "I confirm this financial decision changes the final settlement result."
    )
    if st.button(
        "Save immutable override",
        type="primary",
        disabled=(not confirmed or new_decision == order.final_financial_decision),
    ):
        user = AuthService(settings).current_user()
        try:
            FinancialOverrideService(
                repository,
                EmailWorkflowRepository(
                    settings.email_workflow_registry_path
                ).period_locked,
            ).create(
                period_code=restaurant.period_code,
                restaurant_id=restaurant.restaurant_id,
                order_id=order.order_id,
                system_decision=order.system_financial_decision,
                new_decision=new_decision,
                reason_code=reason,
                comment=comment or None,
                created_by=user.user_id,
                source_engine_version=order.decision_trace.engine_version,
                source_decision_rule=order.decision_trace.decision_rule,
            )
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.cache_data.clear()
            st.success("Override appended. Source operational data was not changed.")
            st.rerun()


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


def render_financial_review(view: str) -> None:
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
        workspace = load_financial_review(period_code)
        result = workspace.summary
    except (GoogleIntegrationError, ValueError, OSError) as exc:
        st.error(f"Financial Review is unavailable: {exc}")
        return
    render_kpis(
        [
            ("MANUAL_REVIEW orders", f"{result.manual_review_orders:,}", "No automatic resolution"),
            ("Overrides Applied", f"{result.overrides_applied:,}", "Latest valid override"),
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
    commission_rows = [
        {
            "Restaurant": item.restaurant_name,
            "Restaurant ID": item.restaurant_id,
            "Scope Commission": item.commission_resolution.scope_commission,
            "RST Commission": item.commission_resolution.rst_commission,
            "Difference": item.commission_resolution.difference,
            "Orders": item.total_orders,
            "Potential Financial Impact": (
                item.commission_resolution.potential_financial_impact
            ),
            "Resolution Status": item.commission_resolution.status.value,
            "Authority": item.commission_resolution.resolution_source,
        }
        for item in result.restaurants
        if item.commission_resolution.status.value
        in {"MISMATCH", "RST_ONLY", "MISSING"}
    ]
    if view == "COMMISSION REVIEW":
        st.markdown("### Commission Issues")
        st.caption(
            "Invoice Scope is authoritative when valid. RST differences remain visible diagnostics."
        )
        st.dataframe(pd.DataFrame(commission_rows), hide_index=True, width="stretch")
        return
    registry_by_id = {
        item.restaurant_id: item for item in workspace.registry.restaurants
    }
    rows = []
    row_objects = []
    for restaurant in result.restaurants:
        for order in restaurant.orders:
            invalid = any(code.startswith("INVALID_") for code in order.issue_codes)
            if view == "DATA ISSUES" and not invalid:
                continue
            if view == "FINANCIAL REVIEW" and (
                order.final_financial_decision.value != "MANUAL_REVIEW" or invalid
            ):
                continue
            registered = registry_by_id.get(restaurant.restaurant_id)
            rows.append(
                {
                    "Restaurant": restaurant.restaurant_name,
                    "Restaurant ID": restaurant.restaurant_id,
                    "City": registered.city if registered else None,
                    "AM": registered.account_manager if registered else None,
                    "Order ID": order.order_id,
                    "Order Date": order.order_date,
                    "Operational Status": order.source_order_status,
                    "Cancellation Reason": order.cancellation_reason,
                    "Responsibility": order.cancellation_responsibility.value,
                    "System Decision": order.system_financial_decision.value,
                    "Final Decision": order.final_financial_decision.value,
                    "Amount": order.order_amount,
                    "Override": "APPLIED" if order.manual_override_applied else "NONE",
                    "Issue": " · ".join(order.issue_codes)
                    or order.decision_trace.decision_rule,
                    "Action": "Review",
                }
            )
            row_objects.append((order, restaurant))
    st.markdown(f"### {view.title()} · {period_code}")
    filters = st.columns(4)
    restaurant_filter = filters[0].text_input(
        "Restaurant", key="financial_restaurant_filter"
    )
    decision_filter = filters[1].selectbox(
        "Decision",
        ["All", *(item.value for item in FinancialDecision)],
        key="financial_decision_filter",
    )
    responsibility_filter = filters[2].selectbox(
        "Responsibility",
        ["All", *sorted({row["Responsibility"] for row in rows})],
        key="financial_responsibility_filter",
    )
    issue_filter = filters[3].text_input("Issue", key="financial_issue_filter")
    visible = list(zip(rows, row_objects, strict=True))
    if restaurant_filter:
        needle = restaurant_filter.casefold()
        visible = [
            item
            for item in visible
            if needle in (item[0]["Restaurant"] or "").casefold()
        ]
    if decision_filter != "All":
        visible = [
            item for item in visible if item[0]["Final Decision"] == decision_filter
        ]
    if responsibility_filter != "All":
        visible = [
            item
            for item in visible
            if item[0]["Responsibility"] == responsibility_filter
        ]
    if issue_filter:
        needle = issue_filter.casefold()
        visible = [
            item for item in visible if needle in item[0]["Issue"].casefold()
        ]
    event = st.dataframe(
        pd.DataFrame([item[0] for item in visible]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
    )
    if event.selection.rows:
        order, restaurant = visible[event.selection.rows[0]][1]
        financial_review_dialog(order, restaurant)
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


page_setup("Review Center")
header, action = st.columns([4, 1])
with header:
    st.title("Review / Exception Center")
    st.caption(
        "Identity correction and financial eligibility review · Read-only Google sources"
    )
with action:
    if st.button("Refresh Google Sources", type="primary"):
        expire_partner_legal_master_cache()
        st.cache_data.clear()
        st.cache_resource.clear()
        st.session_state.pop("financial_review_period", None)
        st.rerun()

review_area = st.segmented_control(
    "Review area",
    [
        "ALL EXCEPTIONS",
        "IDENTITY REVIEW",
        "FINANCIAL REVIEW",
        "COMMISSION REVIEW",
        "LEGAL MASTER",
        "DATA ISSUES",
    ],
    default="ALL EXCEPTIONS",
)
if review_area == "ALL EXCEPTIONS":
    settings = get_settings()
    periods = SettlementPeriodService(settings.timezone)
    latest = periods.latest_complete(
        as_of=datetime.now(ZoneInfo(settings.timezone)).date()
    )
    period_code = st.selectbox(
        "Period", ["2026-07-P2", latest.period_code], key="unified_review_period"
    )
    try:
        workspace = load_financial_review(period_code)
    except (GoogleIntegrationError, ValueError, OSError) as exc:
        st.error(f"Review Center is unavailable: {exc}")
        st.stop()
    review_repository = ReviewRepository(settings.review_registry_path)
    publications = DocumentPublicationRepository(
        settings.document_publication_registry_path
    ).list_latest_for_period(period_code)
    review_items = ReviewCenterBuilder().build(
        workspace, publications, review_repository
    )
    open_items = tuple(
        item
        for item in review_items
        if item.status in {ReviewStatus.OPEN, ReviewStatus.IN_REVIEW, ReviewStatus.BLOCKED_EXTERNAL}
    )
    render_kpis(
        [
            ("Total Open", f"{len(open_items):,}", "Unified queue"),
            ("Financial Review", f"{sum(item.issue_type.value == 'MANUAL_REVIEW' for item in open_items):,}", "Order decisions"),
            ("Commission Issues", f"{sum(item.issue_type.value == 'COMMISSION_BLOCKER' for item in open_items):,}", "Invoice Scope authority"),
            ("Invalid Financial", f"{sum(item.issue_type.value == 'INVALID_FINANCIAL' for item in open_items):,}", "Source correction"),
            ("Identity", f"{sum(item.issue_type.value == 'IDENTITY_BLOCKER' for item in open_items):,}", "Mapping blockers"),
            ("Legal Master", f"{sum(item.issue_type.value == 'LEGAL_MASTER_ISSUE' for item in open_items):,}", "Read-only issues"),
            ("Document Failures", f"{sum('FAILURE' in item.issue_type.value for item in open_items):,}", "Generation / R2"),
            ("Critical", f"{sum(item.severity == ReviewSeverity.CRITICAL for item in open_items):,}", "Immediate attention"),
        ]
    )
    filters = st.columns(6)
    issue_filter = filters[0].selectbox(
        "Issue Type", ["ALL", *sorted({item.issue_type.value for item in review_items})]
    )
    severity_filter = filters[1].selectbox(
        "Severity", ["ALL", *(item.value for item in ReviewSeverity)]
    )
    city_filter = filters[2].selectbox(
        "City", ["ALL", *sorted({item.city for item in review_items if item.city})]
    )
    am_filter = filters[3].selectbox(
        "AM", ["ALL", *sorted({item.account_manager for item in review_items if item.account_manager})]
    )
    status_filter = filters[4].selectbox(
        "Status", ["ALL", *(item.value for item in ReviewStatus)]
    )
    search = filters[5].text_input("Search", key="unified_review_search")
    visible = [
        item
        for item in review_items
        if (issue_filter == "ALL" or item.issue_type.value == issue_filter)
        and (severity_filter == "ALL" or item.severity.value == severity_filter)
        and (city_filter == "ALL" or item.city == city_filter)
        and (am_filter == "ALL" or item.account_manager == am_filter)
        and (status_filter == "ALL" or item.status.value == status_filter)
        and (
            not search
            or search.casefold()
            in f"{item.restaurant_name} {item.restaurant_id} {item.description}".casefold()
        )
    ]
    event = st.dataframe(
        pd.DataFrame(
            [
                {
                    "Restaurant": item.restaurant_name,
                    "Restaurant ID": item.restaurant_id,
                    "City": item.city,
                    "AM": item.account_manager,
                    "Issue Type": item.issue_type.value,
                    "Severity": item.severity.value,
                    "Dimension": item.blocking_dimension,
                    "Description": item.description,
                    "Current Value": item.current_value,
                    "Recommended Action": item.recommended_action,
                    "Status": item.status.value,
                    "Retryable": "YES" if item.retryable else "NO",
                }
                for item in visible
            ]
        ),
        hide_index=True,
        width="stretch",
        selection_mode="single-row",
        on_select="rerun",
        key="unified_review_table",
    )
    if event.selection.rows:
        selected_item = visible[event.selection.rows[0]]
        st.markdown(f"### Review · {selected_item.restaurant_name or selected_item.issue_type.value}")
        st.info(selected_item.recommended_action)
        transition = st.selectbox(
            "New status", list(ReviewStatus), format_func=lambda item: item.value
        )
        transition_reason = st.text_area("Decision reason")
        if st.button(
            "Save review status",
            disabled=not transition_reason.strip(),
            type="primary",
        ):
            review_repository.transition(
                selected_item,
                transition,
                actor_id=AuthService(settings).current_user().user_id,
                reason=transition_reason,
            )
            st.cache_data.clear()
            st.rerun()
    st.caption(
        "Issues never disappear silently. Financial overrides remain available in the dedicated Financial Review view."
    )
    st.stop()
if review_area == "LEGAL MASTER":
    try:
        legal_registry = load_registry()
    except (GoogleIntegrationError, ValueError, OSError) as exc:
        st.error(f"Partner Legal Master review is unavailable: {exc}")
        st.stop()
    snapshot = legal_registry.partner_legal_master
    if not snapshot or not snapshot.profile:
        st.error("Partner Legal Master has no successful synchronized snapshot.")
        st.stop()
    profile = snapshot.profile
    render_kpis(
        [
            ("Rows", f"{profile.row_count:,}", snapshot.status.value),
            ("Missing ID", f"{profile.missing_ids:,}", "Human source correction"),
            ("Duplicate IDs", f"{profile.duplicate_id_groups:,}", "Never selected arbitrarily"),
            ("Legal conflicts", f"{profile.conflict_groups:,}", "Not applied to registry"),
            ("Name mismatch", f"{profile.name_mismatches:,}", "Exact ID review"),
            ("RST matches", f"{profile.matched_rst:,}", "Exact Restaurant ID"),
        ]
    )
    issue_filters = st.columns(5)
    city_filter = issue_filters[0].selectbox(
        "City",
        [
            "All",
            *sorted(
                {
                    item.city
                    for item in legal_registry.restaurants
                    if item.city
                },
                key=str.casefold,
            ),
        ],
    )
    restaurant_filter = issue_filters[1].text_input("Restaurant")
    issue_filter = issue_filters[2].selectbox(
        "Issue", ["All", *sorted({item.code for item in snapshot.issues})]
    )
    review_filter = issue_filters[3].selectbox(
        "Review Status",
        [
            "All",
            *sorted(
                {
                    item.review_status
                    for item in snapshot.issues
                    if item.review_status
                }
            ),
        ],
    )
    issue_filters[4].selectbox(
        "Document Type", ["All", "INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT"]
    )
    restaurant_by_id = {
        item.restaurant_id: item
        for item in legal_registry.restaurants
        if item.restaurant_id
    }
    legal_issues = list(snapshot.issues)
    if city_filter != "All":
        legal_issues = [
            item
            for item in legal_issues
            if restaurant_by_id.get(item.restaurant_id)
            and restaurant_by_id[item.restaurant_id].city == city_filter
        ]
    if restaurant_filter:
        needle = restaurant_filter.casefold().strip()
        legal_issues = [
            item
            for item in legal_issues
            if needle in (item.restaurant_name or "").casefold()
            or needle in (item.restaurant_id or "").casefold()
        ]
    if issue_filter != "All":
        legal_issues = [item for item in legal_issues if item.code == issue_filter]
    if review_filter != "All":
        legal_issues = [
            item for item in legal_issues if item.review_status == review_filter
        ]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Restaurant": item.restaurant_name,
                    "Restaurant ID": item.restaurant_id,
                    "City": (
                        restaurant_by_id[item.restaurant_id].city
                        if item.restaurant_id in restaurant_by_id
                        else None
                    ),
                    "Issue": item.code,
                    "Review Status": item.review_status,
                    "Source Rows": ", ".join(map(str, item.source_rows)),
                    "Conflicting Fields": ", ".join(item.fields),
                }
                for item in legal_issues
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Read-only diagnostics. Full RIB values and legal identifiers are never shown in this table."
    )
    st.warning("AUTOMATION OFF · No source correction is applied by CashCo")
    st.stop()
if review_area != "IDENTITY REVIEW":
    render_financial_review(review_area)
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
