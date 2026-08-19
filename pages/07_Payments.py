from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.auth import AuthService
from src.config import get_settings
from src.documents.phase8 import DocumentReadinessStatus, Phase8DocumentEngine
from src.documents.publishing import DocumentPublicationRepository
from src.google.exceptions import GoogleIntegrationError
from src.payments.finance import (
    PaymentBatchService,
    PaymentExportService,
    PaymentReadiness,
    PaymentReadinessService,
)
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=300, show_spinner="Recalculating payment readiness…")
def load_payment_workspace(period_code: str):
    return load_phase5_workspace(period_code)


settings = get_settings()
periods = SettlementPeriodService(settings.timezone)
latest = periods.latest_complete(as_of=datetime.now(ZoneInfo(settings.timezone)).date())
period_options = list(dict.fromkeys([latest.period_code, "2026-07-P2", "2026-07-P1"]))
page_setup("Finance / Payments", period_code=latest.period_code)
st.title("Finance / Payment Center")
st.caption(
    "Payment preparation, controlled Finance exports and bank reconciliation — no transfer execution"
)

period_code = st.selectbox("Period", period_options)
try:
    workspace = load_payment_workspace(period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Payment readiness is temporarily unavailable: {exc}")
    st.stop()

registry = {
    item.restaurant_id: item
    for item in workspace.registry.restaurants
    if item.restaurant_id
}
document_engine = Phase8DocumentEngine()
publications = DocumentPublicationRepository(
    settings.document_publication_registry_path
)
service = PaymentReadinessService()
records = []
financially_ready_ids: set[str] = set()
for settlement in workspace.summary.restaurants:
    restaurant = registry.get(settlement.restaurant_id)
    if restaurant is None:
        continue
    if settlement.settlement_status.value == "READY":
        financially_ready_ids.add(settlement.restaurant_id)
    documents_ready = document_engine.readiness(
        restaurant, settlement
    ).status == DocumentReadinessStatus.READY and all(
        publications.current(period_code, settlement.restaurant_id, kind) is not None
        for kind in ("INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT")
    )
    records.append(
        service.evaluate(restaurant, settlement, documents_ready=documents_ready)
    )

payable_population = tuple(
    item for item in records if item.restaurant_id in financially_ready_ids
)
ready = tuple(
    item for item in records if item.payment_readiness == PaymentReadiness.PAYMENT_READY
)
blocked = tuple(item for item in payable_population if item not in ready)
ready_amount = sum((item.net_payable for item in ready), Decimal(0))
blocked_amount = sum((item.net_payable for item in blocked), Decimal(0))
render_kpis(
    [
        (
            "Net Payable",
            f"{ready_amount + blocked_amount:,.2f} MAD",
            "Certified settlement",
        ),
        ("Payment Ready", f"{ready_amount:,.2f} MAD", f"{len(ready):,} restaurants"),
        (
            "Payment Blocked",
            f"{blocked_amount:,.2f} MAD",
            f"{len(blocked):,} restaurants",
        ),
        (
            "Missing RIB",
            f"{sum(item.payment_readiness == PaymentReadiness.RIB_MISSING for item in payable_population):,}",
            "Partner Legal Master",
        ),
        ("In Batch", "0", "No persistent batch created"),
        ("Pending / Paid", "0 / 0", "External execution only"),
        ("Reconciled", "0", "Evidence required"),
        ("Failed / Hold", "0", "No payment action executed"),
    ]
)

st.subheader("Payment funnel")
financially_ready = sum(item.net_payable > 0 for item in records)
st.progress(len(ready) / financially_ready if financially_ready else 0)
st.caption(
    f"Financially ready {financially_ready:,} → Payment data ready {len(ready):,} → In batch 0 → Finance validated 0 → Pending 0 → Paid 0 → Reconciled 0"
)

filters = st.columns(5)
with filters[0]:
    readiness_filter = st.selectbox(
        "Payment Readiness", ["ALL", *[item.value for item in PaymentReadiness]]
    )
with filters[1]:
    city_filter = st.selectbox(
        "City",
        [
            "ALL",
            *sorted(
                {
                    registry[item.restaurant_id].city
                    for item in records
                    if registry[item.restaurant_id].city
                }
            ),
        ],
    )
with filters[2]:
    bank_filter = st.selectbox(
        "Bank", ["ALL", *sorted({item.bank for item in records if item.bank})]
    )
with filters[3]:
    search = st.text_input("Search")
with filters[4]:
    status_filter = st.selectbox("Payment Status", ["ALL", "READY", "NOT_READY"])

filtered = [
    item
    for item in records
    if (readiness_filter == "ALL" or item.payment_readiness.value == readiness_filter)
    and (city_filter == "ALL" or registry[item.restaurant_id].city == city_filter)
    and (bank_filter == "ALL" or item.bank == bank_filter)
    and (status_filter == "ALL" or item.payment_status.value == status_filter)
    and (
        not search
        or search.casefold()
        in f"{item.restaurant_name} {item.restaurant_id}".casefold()
    )
]
st.dataframe(
    pd.DataFrame(
        [
            {
                "Restaurant": item.restaurant_name,
                "Restaurant ID": item.restaurant_id,
                "Period": item.period_code,
                "Net Payable": float(item.net_payable),
                "RIB": item.masked_rib,
                "RIB Status": item.rib_status.value,
                "Bank": item.bank,
                "Payment Readiness": item.payment_readiness.value,
                "Payment Status": item.payment_status.value,
                "Batch": "—",
                "Payment Reference": item.payment_reference,
            }
            for item in filtered
        ]
    ),
    hide_index=True,
    use_container_width=True,
)

st.subheader("Controlled dry-run batch proposal")
sample = ready[:3]
if sample:
    user = AuthService(settings).current_user()
    try:
        batch = PaymentBatchService().preview(
            sample, user, notes="DRY RUN — no persistent mutation"
        )
        st.info(
            f"{batch.restaurant_count} restaurants · {batch.total_net_payable:,.2f} MAD · reconciliation 0.00 MAD · snapshot {batch.snapshot_hash[:12]}…"
        )
        if st.button("Prepare sensitive Finance export", type="primary"):
            payload = PaymentExportService().workbook(batch, blocked)
            st.download_button(
                "Download dry-run Finance workbook",
                payload,
                file_name=f"cashco_payment_dry_run_{period_code}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    except (PermissionError, ValueError) as exc:
        st.warning(str(exc))
else:
    st.info("No payment-ready restaurant is available for a dry-run batch.")

st.warning(
    "CONTROL SYSTEM ONLY · Bank API calls 0 · Transfers executed 0 · Gmail calls 0"
)
