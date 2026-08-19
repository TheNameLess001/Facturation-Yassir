from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.documents.publishing import DocumentPublicationRepository
from src.google.exceptions import GoogleIntegrationError
from src.operations.reporting import BillingExportService, BillingReportingService
from src.operations.review import ReviewCenterBuilder, ReviewRepository, ReviewStatus
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis


@st.cache_data(ttl=300, show_spinner="Building billing reports…")
def load_reporting_workspace(period_code: str):
    return load_phase5_workspace(period_code)


settings = get_settings()
period_service = SettlementPeriodService(settings.timezone)
today = datetime.now(ZoneInfo(settings.timezone)).date()
latest = period_service.latest_complete(as_of=today)
options = [
    latest.period_code,
    "2026-07-P2",
    "2026-07-P1",
]
options = list(dict.fromkeys(options))

page_setup("Reports & Audit", period_code=options[0])
st.title("Billing Reporting & Audit Center")
st.caption(
    "Financial, operational and document reporting from certified settlement snapshots."
)

period_code, comparable_code = st.columns(2)
with period_code:
    selected_period = st.selectbox("Period", options)
with comparable_code:
    comparable_period = st.selectbox(
        "Compare with", [value for value in options if value != selected_period]
    )

try:
    workspace = load_reporting_workspace(selected_period)
    comparable_workspace = load_reporting_workspace(comparable_period)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Billing reporting is temporarily unavailable: {exc}")
    st.stop()

reporting = BillingReportingService()
report = reporting.financial(workspace.summary)
comparable = reporting.financial(comparable_workspace.summary)
comparison = reporting.compare(report, comparable)
publication_repository = DocumentPublicationRepository(
    settings.document_publication_registry_path
)
publications = publication_repository.list_latest_for_period(selected_period)
document_report = reporting.documents(publications)
review_repository = ReviewRepository(settings.review_registry_path)
review_items = ReviewCenterBuilder().build(
    workspace, publications, review_repository
)
open_issues = tuple(
    item
    for item in review_items
    if item.status in {ReviewStatus.OPEN, ReviewStatus.IN_REVIEW}
)

st.subheader("Certified financial view")
render_kpis(
    [
        ("Sales TTC", f"{report.sales_ttc:,.2f} MAD", f"{report.order_count:,} orders"),
        ("Sales HT", f"{report.sales_ht:,.2f} MAD", "cashco_legacy_v1"),
        ("Commission HT", f"{report.commission_ht:,.2f} MAD", "Certified snapshot"),
        ("TVA", f"{report.tva:,.2f} MAD", "20% policy"),
        ("Invoice TTC", f"{report.invoice_ttc:,.2f} MAD", "Billing total"),
        ("Net Payable", f"{report.net_payable:,.2f} MAD", "Partner settlement"),
        ("Restaurants", f"{report.restaurant_count:,}", "Financially calculable"),
        ("Open Reviews", f"{len(open_issues):,}", "Central review queue"),
    ]
)

st.subheader(f"Period comparison · {selected_period} vs {comparable_period}")
comparison_rows = []
for field in reporting.FINANCIAL_FIELDS:
    percentage = comparison.percentage_delta[field]
    comparison_rows.append(
        {
            "Metric": field.replace("_", " ").title(),
            "Current (MAD)": float(getattr(report, field)),
            "Comparable (MAD)": float(getattr(comparable, field)),
            "Delta (MAD)": float(comparison.absolute_delta[field]),
            "Delta (%)": float(percentage) if percentage is not None else None,
        }
    )
st.dataframe(pd.DataFrame(comparison_rows), hide_index=True, use_container_width=True)

st.subheader("Document & operational reporting")
render_kpis(
    [
        ("Total PDFs", f"{document_report.total_pdfs:,}", "Cloudflare R2 registry"),
        ("Invoices", f"{document_report.invoices:,}", "Current versions"),
        ("Notes de Débours", f"{document_report.notes_de_debours:,}", "Current versions"),
        ("Statements", f"{document_report.statements:,}", "Current versions"),
        ("Publication Failures", f"{document_report.failures:,}", "Actionable exceptions"),
    ]
)

publication_by_restaurant: dict[str, int] = {}
for publication in publications:
    publication_by_restaurant[publication.restaurant_id] = (
        publication_by_restaurant.get(publication.restaurant_id, 0) + 1
    )
review_by_restaurant = {item.restaurant_id for item in open_issues}
restaurant_rows = tuple(
    {
        "Restaurant": item.restaurant_name,
        "Restaurant ID": item.restaurant_id,
        "Period": selected_period,
        "Sales TTC": float(item.sales_ttc or 0),
        "Commission Rate": float(item.commission_rate or 0),
        "Commission HT": float(item.commission_amount or 0),
        "TVA": float(item.invoice_tva or 0),
        "Invoice TTC": float(item.invoice_ttc or 0),
        "Net Payable": float(item.net_payable or 0),
        "Document Status": f"{publication_by_restaurant.get(item.restaurant_id, 0)}/3",
        "Review Status": "OPEN" if item.restaurant_id in review_by_restaurant else "CLEAR",
    }
    for item in workspace.summary.restaurants
    if item.sales_ttc is not None
)
st.dataframe(pd.DataFrame(restaurant_rows), hide_index=True, use_container_width=True)

st.subheader("Controlled export center")
exporter = BillingExportService()
csv_bytes = exporter.period_csv(report)
xlsx_bytes = exporter.workbook(report, restaurant_rows, review_items, publications)
csv_column, excel_column = st.columns(2)
with csv_column:
    st.download_button(
        "Download period summary (CSV)",
        csv_bytes,
        file_name=f"cashco_{selected_period}_summary.csv",
        mime="text/csv",
    )
with excel_column:
    st.download_button(
        "Download billing workbook (Excel)",
        xlsx_bytes,
        file_name=f"cashco_{selected_period}_billing.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.subheader("Operational audit")
audit_rows = [
    {
        "Occurred At": event.get("occurred_at"),
        "Event": event.get("event"),
        "User": event.get("actor_id"),
        "Restaurant": event.get("restaurant_id"),
        "Severity": event.get("severity", "INFO"),
    }
    for event in review_repository.audit_events(selected_period)
]
if audit_rows:
    st.dataframe(pd.DataFrame(audit_rows), hide_index=True, use_container_width=True)
else:
    st.info("No operational audit events match this period yet.")

st.info(
    "Gmail and payment execution are deferred. This center performs no provider or transfer action."
)
