from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.exceptions import GoogleIntegrationError
from src.restaurants.registry_runtime import run_restaurant_registry
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
try:
    legal_snapshot = run_restaurant_registry().partner_legal_master
    legal_events = legal_snapshot.audit_events if legal_snapshot else ()
except (GoogleIntegrationError, ValueError, OSError):
    legal_events = ()
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
        ("Legal Master Events", f"{len(legal_events):,}", "No legal values recorded"),
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
st.markdown("### Partner Legal Master synchronization")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Event": item.event_type,
                "At": item.occurred_at,
                "Fingerprint": (
                    f"{item.fingerprint[:16]}…" if item.fingerprint else None
                ),
                "Rows": item.rows,
                "Matched IDs": item.matched_ids,
                "Conflicts": item.conflicts,
                "Affected Readiness": item.affected_readiness_count,
            }
            for item in legal_events
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption("No RIB, ICE, IF, RC, email body, token, or credential is stored in sync audit events.")
st.warning("AUTOMATION OFF · Audit history does not authorize downstream actions")
