from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.google.exceptions import GoogleIntegrationError
from src.restaurants.registry_models import (
    MappingReviewCase,
    MappingStatus,
    RegisteredRestaurant,
    SuggestionStrength,
)
from src.restaurants.registry_runtime import run_restaurant_registry
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=900, show_spinner="Building the mapping review workspace…")
def load_registry():
    return run_restaurant_registry()


def invoice_scope_url() -> str:
    file_id = get_settings().invoice_scope_file_id
    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit?gid=0"


@st.dialog("Invoice Scope mapping review", width="large")
def mapping_dialog(
    case: MappingReviewCase,
    restaurant: RegisteredRestaurant,
) -> None:
    first = case.scope_rows[0]
    st.markdown(f"### {first.restaurant_name or 'Unnamed scope restaurant'}")
    st.warning("SUGGESTION — HUMAN VALIDATION REQUIRED")
    st.caption(
        "CashCo will not apply a candidate. Validate the restaurant and update "
        "Restaurant ID directly in Invoice Scope."
    )

    st.markdown("#### Invoice Scope")
    scope_frame = pd.DataFrame(
        [
            {
                "Scope Row": row.source_row,
                "Restaurant Name": row.restaurant_name,
                "Restaurant ID": row.restaurant_id,
                "City": row.city,
                "Commission": row.commission_rate,
                "Comment": row.comment,
                **row.extra_fields,
            }
            for row in case.scope_rows
        ]
    )
    st.dataframe(scope_frame, hide_index=True, width="stretch")
    if case.mapping_status == MappingStatus.CONFLICTING_SCOPE:
        st.error(
            "Conflicting Invoice Scope rows. No row was collapsed. Conflicting "
            f"fields: {', '.join(case.conflict_fields) or 'unknown'}."
        )
    elif (
        case.mapping_status == MappingStatus.DUPLICATE_SCOPE
        and not case.conflict_fields
    ):
        st.warning(
            "IDENTICAL_SCOPE_DUPLICATE · Rows are identical but Invoice Scope "
            "should contain one authoritative row per restaurant."
        )

    st.markdown("#### RST candidates")
    if case.candidates:
        candidates = pd.DataFrame(
            [
                {
                    "Restaurant ID": item.restaurant_id,
                    "Restaurant Name": item.restaurant_name,
                    "City": item.city,
                    "Area": item.area,
                    "Chain": item.chain,
                    "Store Type": item.store_type,
                    "Status": item.status,
                    "Commission": item.commission_rate,
                    "Email": item.email,
                    "Admin Orders": item.canonical_order_count,
                    "Advisory Score": item.advisory_score,
                    "Similarity Indicators": " · ".join(
                        item.similarity_indicators
                    ),
                }
                for item in case.candidates
            ]
        )
        st.dataframe(
            candidates,
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
        if case.suggestion_strength == SuggestionStrength.NO_USEFUL_CANDIDATE:
            st.info("No useful candidate met the advisory relevance threshold.")
        else:
            likely = case.candidates[0]
            st.markdown("**Top advisory candidate — verify before use**")
            st.code(likely.restaurant_id, language=None)
            st.caption(
                f"{likely.restaurant_name or 'Unnamed'} · Scope row "
                f"{first.source_row} · {case.suggestion_strength.value}"
            )
    else:
        st.info("No useful RST candidate was generated for this row.")

    st.link_button("Open Invoice Scope", invoice_scope_url())
    st.success("Update Restaurant ID in Invoice Scope, then Refresh CashCo.")
    st.caption(
        f"Current identity readiness: "
        f"{'READY' if restaurant.readiness.identity_ready else 'BLOCKING'} · "
        "Settlement Engine is not implemented."
    )


page_setup("Invoice Scope Mapping Review")
header, action = st.columns([4, 1])
with header:
    st.title("Invoice Scope Mapping Review")
    st.caption("Human correction workspace · Real Invoice Scope, RST and Admin diagnostics")
with action:
    if st.button("Refresh Google Sources", type="primary"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

try:
    registry = load_registry()
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Mapping Review is unavailable: {exc}")
    st.stop()

render_kpis(
    [
        (
            "Ambiguous Mapping",
            f"{registry.issue_count('AMBIGUOUS_RESTAURANT_MAPPING'):,}",
            "Human validation required",
        ),
        (
            "Unmatched",
            f"{registry.issue_count('UNMATCHED_SCOPE_RESTAURANT'):,}",
            "No exact deterministic match",
        ),
        (
            "Conflicting Scope",
            f"{registry.issue_count('CONFLICTING_SCOPE_ROW'):,}",
            "Source rows disagree",
        ),
        (
            "Duplicate Scope",
            f"{registry.issue_count('DUPLICATE_SCOPE_ROW'):,}",
            "Identical group warning",
        ),
        (
            "Missing Restaurant ID",
            f"{registry.scope_rows_without_restaurant_id:,}",
            "Can still exact-match uniquely by name",
        ),
        ("Mapped", f"{registry.mapped_count:,}", "Exact deterministic identity"),
    ]
)

completion, blocking, ready = st.columns(3)
completion.metric("Mapping Completion", f"{registry.mapping_completion:.1%}", "Target 100%")
blocking.metric(
    "Blocking Mapping Issues",
    f"{registry.blocking_mapping_issues:,}",
    "Identity blockers only",
)
ready.metric(
    "Ready for Settlement Mapping",
    "YES" if registry.ready_for_settlement_mapping else "NO",
    "Settlement calculations remain unavailable",
)
st.progress(registry.mapping_completion)

case_pairs = list(zip(registry.mapping_cases, registry.restaurants, strict=True))
filter_columns = st.columns(3)
view = filter_columns[0].selectbox(
    "View",
    ["Needs review", "All scope restaurants", "Mapped only"],
)
status = filter_columns[1].selectbox(
    "Mapping status",
    ["All", *(item.value for item in MappingStatus)],
)
search = filter_columns[2].text_input("Search", placeholder="Restaurant, ID or city")

if view == "Needs review":
    case_pairs = [
        pair
        for pair in case_pairs
        if not pair[1].readiness.identity_ready
        or pair[0].mapping_status
        in {MappingStatus.DUPLICATE_SCOPE, MappingStatus.CONFLICTING_SCOPE}
    ]
elif view == "Mapped only":
    case_pairs = [pair for pair in case_pairs if pair[1].readiness.identity_ready]
if status != "All":
    case_pairs = [pair for pair in case_pairs if pair[0].mapping_status.value == status]
if search:
    needle = search.casefold().strip()
    case_pairs = [
        pair
        for pair in case_pairs
        if any(
            needle in (value or "").casefold()
            for value in (
                pair[0].scope_rows[0].restaurant_name,
                pair[0].scope_rows[0].restaurant_id,
                pair[0].scope_rows[0].city,
            )
        )
    ]


def likely_label(
    case: MappingReviewCase,
    restaurant: RegisteredRestaurant,
) -> str:
    if case.likely_candidate:
        return (
            f"{case.likely_candidate.restaurant_name or 'Unnamed'} · "
            f"{case.likely_candidate.restaurant_id}"
        )
    if restaurant.readiness.identity_ready:
        return f"{restaurant.restaurant_name or 'Unnamed'} · {restaurant.restaurant_id}"
    return "No useful candidate"


table = pd.DataFrame(
    [
        {
            "Scope Row": case.scope_rows[0].source_row,
            "Invoice Scope Restaurant": case.scope_rows[0].restaurant_name,
            "Scope Restaurant ID": case.scope_rows[0].restaurant_id,
            "City": case.scope_rows[0].city,
            "Commission": case.scope_rows[0].commission_rate,
            "Mapping Status": case.mapping_status.value,
            "RST Candidates": len(case.candidates),
            "Likely Candidate": likely_label(case, restaurant),
            "Issue": " · ".join(case.issue_codes) or "None",
            "Action": (
                "Review and update Invoice Scope"
                if not restaurant.readiness.identity_ready
                or case.mapping_status
                in {MappingStatus.DUPLICATE_SCOPE, MappingStatus.CONFLICTING_SCOPE}
                else "No action required"
            ),
        }
        for case, restaurant in case_pairs
    ]
)
st.markdown(
    f'<div class="cc-section">Mapping cases · {len(case_pairs):,}</div>',
    unsafe_allow_html=True,
)
event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
st.caption("Select an unresolved row to inspect source details and advisory RST candidates.")
if event.selection.rows:
    selected_case, selected_restaurant = case_pairs[event.selection.rows[0]]
    mapping_dialog(selected_case, selected_restaurant)

with st.expander("Readiness boundaries"):
    st.markdown(
        "- **Identity readiness:** exact valid mapping only.\n"
        "- **Orders:** diagnostic availability; zero orders does not remove scope.\n"
        "- **Settlement:** not evaluated until the Settlement Engine exists.\n"
        "- **Documents:** legal identity fields required later.\n"
        "- **Email:** restaurant or Finance email required later.\n"
        "- **Payment:** RIB required where applicable later."
    )

st.info(
    "CashCo is read-only against Invoice Scope, RST List, and Admin Earnings. "
    "No candidate is auto-applied and no alias master is created."
)
st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
