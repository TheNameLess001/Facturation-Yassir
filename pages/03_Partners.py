from __future__ import annotations

import pandas as pd
import streamlit as st

from src.google.exceptions import GoogleIntegrationError
from src.restaurants.registry_models import MappingStatus, RegisteredRestaurant
from src.restaurants.registry_runtime import run_restaurant_registry
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=900, show_spinner="Building the real Restaurant Registry…")
def load_registry():
    return run_restaurant_registry()


def mask_rib(value: str | None) -> str:
    if not value:
        return "Missing"
    compact = "".join(value.split())
    return f"•••• •••• •••• {compact[-4:]}" if len(compact) > 4 else "••••"


@st.dialog("Restaurant details", width="large")
def restaurant_dialog(restaurant: RegisteredRestaurant) -> None:
    st.markdown(f"### {restaurant.restaurant_name or 'Unnamed restaurant'}")
    st.caption(
        f"Restaurant ID: {restaurant.restaurant_id or 'Missing'} · "
        f"{restaurant.mapping_status.value}"
    )
    overview, identity, legal, contact, sources, orders, issues = st.tabs(
        [
            "Overview",
            "Identity",
            "Legal & Billing",
            "Contact",
            "Data Sources",
            "Orders Availability",
            "Issues",
        ]
    )
    with overview:
        st.write(
            {
                "Restaurant": restaurant.restaurant_name,
                "Chain": restaurant.chain or "Standalone",
                "City": restaurant.city,
                "Area": restaurant.area,
                "Account Manager": restaurant.account_manager,
                "Identity": "READY" if restaurant.readiness.identity_ready else "BLOCKING",
                "Orders": "AVAILABLE" if restaurant.readiness.orders_available else "NONE AVAILABLE",
                "Settlement": "NOT EVALUATED",
                "Documents": "READY" if restaurant.readiness.document_ready else "MISSING LEGAL",
                "Email": "READY" if restaurant.readiness.email_ready else "MISSING",
                "Payment": "READY" if restaurant.readiness.payment_ready else "MISSING RIB",
            }
        )
    with identity:
        st.write(
            {
                "Restaurant ID": restaurant.restaurant_id,
                "Restaurant Name": restaurant.restaurant_name,
                "Chain": restaurant.chain,
                "Chain member": restaurant.is_chain,
            }
        )
    with legal:
        st.write(
            {
                "Legal Entity": restaurant.legal_entity,
                "ICE": restaurant.ice,
                "IF": restaurant.if_number,
                "RC": restaurant.rc,
                "Address": restaurant.address,
                "RIB": mask_rib(restaurant.rib),
                "Bank": restaurant.bank,
                "Commission": str(restaurant.commission_rate)
                if restaurant.commission_rate is not None
                else None,
            }
        )
    with contact:
        st.write(
            {
                "Email": restaurant.email,
                "Finance Email": restaurant.finance_email,
                "Phone": restaurant.phone,
            }
        )
    with sources:
        st.write(
            {
                "Invoice Scope row": restaurant.scope_source_row,
                "RST reference": restaurant.rst_source_reference,
                "Mapping method": restaurant.mapping_method,
            }
        )
    with orders:
        st.metric("Canonical Admin orders", restaurant.canonical_order_count)
        st.caption(
            "Diagnostic only. Invoice Scope eligibility is retained even when no "
            "canonical Admin order is currently available."
        )
    with issues:
        if restaurant.issue_codes:
            for code in restaurant.issue_codes:
                st.warning(code)
        else:
            st.success("No current registry data-quality issue.")


page_setup("Restaurant Registry")
header, action = st.columns([4, 1])
with header:
    st.title("Restaurant Registry")
    st.caption("Invoice Scope eligibility · RST identity & enrichment · Real production data")
with action:
    if st.button("Refresh Google Sources", type="primary"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

try:
    registry = load_registry()
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Restaurant Registry is unavailable: {exc}")
    st.stop()

restaurants = list(registry.restaurants)
render_kpis(
    [
        ("Invoice Scope Restaurants", f"{len(restaurants):,}", "Distinct active scope entries"),
        (
            "Mapped",
            f"{sum(item.rst_source_reference is not None for item in restaurants):,}",
            "Deterministic mapping",
        ),
        ("Unmatched", f"{registry.issue_count('UNMATCHED_SCOPE_RESTAURANT'):,}", "BLOCKING for future billing"),
        ("Ambiguous", f"{registry.issue_count('AMBIGUOUS_RESTAURANT_MAPPING'):,}", "BLOCKING for future billing"),
        ("With Orders", f"{sum(item.admin_orders_available for item in restaurants):,}", "Canonical Admin diagnostics"),
        ("Without Orders", f"{sum(not item.admin_orders_available for item in restaurants):,}", "Still remains in Invoice Scope"),
        ("Missing Email", f"{registry.issue_count('MISSING_EMAIL'):,}", "Future EMAIL_READY issue"),
        ("Missing RIB", f"{registry.issue_count('MISSING_RIB'):,}", "Future payment issue"),
        ("Missing Legal Data", f"{registry.issue_count('MISSING_LEGAL_ENTITY'):,}", "Future document issue"),
    ]
)

st.markdown('<div class="cc-section">Filters</div>', unsafe_allow_html=True)
filter_columns = st.columns(4)
search = filter_columns[0].text_input("Search", placeholder="Name or Restaurant ID")


def options(field: str) -> list[str]:
    return sorted(
        {
            str(getattr(item, field))
            for item in restaurants
            if getattr(item, field) not in (None, "")
        },
        key=str.casefold,
    )


chain = filter_columns[1].selectbox("Chain", ["All", *options("chain")])
city = filter_columns[2].selectbox("City", ["All", *options("city")])
area = filter_columns[3].selectbox("Area", ["All", *options("area")])
second_row = st.columns(4)
account_manager = second_row[0].selectbox(
    "Account Manager", ["All", *options("account_manager")]
)
mapping_status = second_row[1].selectbox(
    "Mapping Status", ["All", *(item.value for item in MappingStatus)]
)
orders_filter = second_row[2].selectbox("Has Orders", ["All", "Yes", "No"])
quality_filter = second_row[3].selectbox(
    "Data Quality", ["All", "HEALTHY", "WARNING", "BLOCKING"]
)
third_row = st.columns(3)
missing_email = third_row[0].checkbox("Missing Email")
missing_rib = third_row[1].checkbox("Missing RIB")
missing_legal = third_row[2].checkbox("Missing Legal Data")

filtered = restaurants
if search:
    needle = search.casefold().strip()
    filtered = [
        item
        for item in filtered
        if needle in (item.restaurant_name or "").casefold()
        or needle in (item.restaurant_id or "").casefold()
    ]
for field, selected in (
    ("chain", chain),
    ("city", city),
    ("area", area),
    ("account_manager", account_manager),
):
    if selected != "All":
        filtered = [item for item in filtered if getattr(item, field) == selected]
if mapping_status != "All":
    filtered = [item for item in filtered if item.mapping_status.value == mapping_status]
if orders_filter != "All":
    wanted = orders_filter == "Yes"
    filtered = [item for item in filtered if item.admin_orders_available == wanted]
if quality_filter != "All":
    filtered = [item for item in filtered if item.data_quality_status.value == quality_filter]
if missing_email:
    filtered = [item for item in filtered if "MISSING_EMAIL" in item.issue_codes]
if missing_rib:
    filtered = [item for item in filtered if "MISSING_RIB" in item.issue_codes]
if missing_legal:
    filtered = [item for item in filtered if "MISSING_LEGAL_ENTITY" in item.issue_codes]

st.markdown(
    f'<div class="cc-section">Restaurants · {len(filtered):,}</div>',
    unsafe_allow_html=True,
)
table = pd.DataFrame(
    [
        {
            "Restaurant": item.restaurant_name,
            "Restaurant ID": item.restaurant_id,
            "Chain": item.chain or "Standalone",
            "City": item.city,
            "Area": item.area,
            "AM": item.account_manager,
            "Identity": "READY" if item.readiness.identity_ready else "BLOCKING",
            "Orders": "AVAILABLE" if item.readiness.orders_available else "NONE",
            "Documents": "READY" if item.readiness.document_ready else "MISSING LEGAL",
            "Email": "READY" if item.readiness.email_ready else "MISSING",
            "Payment": "READY" if item.readiness.payment_ready else "MISSING RIB",
            "Mapping Status": item.mapping_status.value,
        }
        for item in filtered
    ]
)
event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)
st.caption("Select a row to open its detailed, RIB-masked registry record.")
if event.selection.rows:
    restaurant_dialog(filtered[event.selection.rows[0]])

with st.expander("Real source schema profiles"):
    active = registry.invoice_scope_profile.active
    st.markdown("**Invoice Scope**")
    st.caption(
        f"Workbook: {registry.invoice_scope_profile.filename} · "
        f"Active worksheet: {active.worksheet_name} · Rows: {active.row_count:,}"
    )
    st.write(list(active.columns))
    st.markdown("**RST List**")
    st.caption(
        f"File: {registry.rst_profile.filename} · Rows: {registry.rst_profile.row_count:,}"
    )
    st.write(list(registry.rst_profile.columns))

st.info(
    "Registry outputs remain in application memory. No new Drive artifact, settlement, "
    "document, Sheet, email, or payment action is created."
)
st.warning("AUTOMATION OFF · WAITING FOR ADMIN AUTHORIZATION")
