from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.settlement.overrides import FinancialOverrideRepository
from src.ui.layout import page_setup, render_kpis

page_setup("Audit")
st.title("Immutable Audit Trail")
st.caption("Financial overrides are append-only and cannot be deleted through CashCo.")

period_code = st.selectbox(
    "Settlement period",
    ["2026-07-P2", "2026-07-P1"],
)
repository = FinancialOverrideRepository(
    get_settings().financial_override_registry_path
)
overrides = repository.list_for_period(period_code)
render_kpis(
    [
        ("Financial Overrides", f"{len(overrides):,}", "Append-only records"),
        (
            "Orders Adjusted",
            f"{len({item.order_id for item in overrides}):,}",
            "Latest override drives final decision",
        ),
        (
            "Superseding Overrides",
            f"{sum(item.supersedes_override_id is not None for item in overrides):,}",
            "History retained",
        ),
        ("Deleted", "0", "No delete operation exists"),
    ]
)
st.markdown("### Override history")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Override ID": item.override_id,
                "Period": item.period_code,
                "Restaurant ID": item.restaurant_id,
                "Order ID": item.order_id,
                "Previous": item.previous_decision.value,
                "New": item.new_decision.value,
                "Reason": item.reason_code.value,
                "Comment": item.comment,
                "Created by": item.created_by,
                "Created at": item.created_at,
                "Engine": item.source_engine_version,
                "Rule": item.source_decision_rule,
                "Supersedes": item.supersedes_override_id,
            }
            for item in overrides
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.info(
    "Source operational status and system decision remain unchanged. The latest valid "
    "override determines the final decision while every prior record remains visible."
)
st.warning("AUTOMATION OFF · Audit history does not authorize downstream actions")
