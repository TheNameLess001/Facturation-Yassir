from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.google.exceptions import GoogleIntegrationError
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis

settings = get_settings()
periods = SettlementPeriodService(settings.timezone)
latest = periods.latest_complete(
    as_of=datetime.now(ZoneInfo(settings.timezone)).date()
)
page_setup("Payments", period_code=latest.period_code)
st.title("Payments")
st.caption("Payment readiness only · bank reconciliation is not implemented")
period_code = st.selectbox("Settlement period", [latest.period_code])
try:
    workspace = load_phase5_workspace(period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Payment readiness is unavailable: {exc}")
    st.stop()

registry_by_id = {item.restaurant_id: item for item in workspace.registry.restaurants}
rows = []
for settlement in workspace.summary.restaurants:
    restaurant = registry_by_id.get(settlement.restaurant_id)
    if restaurant is None:
        continue
    rows.append(
        {
            "Restaurant": restaurant.restaurant_name,
            "Restaurant ID": settlement.restaurant_id,
            "Period": period_code,
            "Payment Readiness": (
                "MISSING RIB"
                if not restaurant.rib
                else "READY FOR FUTURE PAYMENT"
                if settlement.net_payable is not None
                else "FINANCIAL REVIEW"
            ),
            "RIB Status": "AVAILABLE · MASKED" if restaurant.rib else "MISSING",
            "Net Payable Status": (
                "VALIDATED · cashco_legacy_v1"
                if settlement.net_payable is not None
                else "NOT CALCULABLE"
            ),
            "Payment Status": "NOT STARTED",
        }
    )
render_kpis(
    [
        ("Restaurants", f"{len(rows):,}", "Identity-ready registry"),
        ("RIB Available", f"{sum(row['RIB Status'].startswith('AVAILABLE') for row in rows):,}", "Values remain masked"),
        (
            "Net Payable Validated",
            f"{sum(row['Net Payable Status'].startswith('VALIDATED') for row in rows):,}",
            "cashco_legacy_v1",
        ),
        ("Payments Started", "0", "Out of scope for this phase"),
    ]
)
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
st.warning(
    "Net payable is formula-certified where settlement inputs are ready. "
    "No bank reconciliation or payment action is implemented."
)
