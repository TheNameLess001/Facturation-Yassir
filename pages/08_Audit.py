from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.emails.workflow_repository import EmailWorkflowRepository
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
email_repository = EmailWorkflowRepository(
    get_settings().email_workflow_registry_path
)
email_events = email_repository.list_audit(period_code)
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
        ("Email Workflow Events", f"{len(email_events):,}", "Operational metadata only"),
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
st.markdown("### Email, authorization and period events")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Event": item.event_type,
                "Actor": item.actor_id,
                "Period": item.period_id,
                "Restaurant": item.restaurant_id,
                "At": item.occurred_at,
                "Entity": item.entity_type,
                "Entity ID": item.entity_id,
                "Result": item.details.get("error_code") or item.details.get("mode") or "—",
            }
            for item in email_events
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption("Email bodies, RIBs, credentials and provider tokens are never audit-logged.")
st.warning("AUTOMATION OFF · Audit history does not authorize downstream actions")
