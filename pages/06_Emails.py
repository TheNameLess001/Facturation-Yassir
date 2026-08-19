from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.emails.gmail_adapter import inspect_gmail_capability
from src.emails.runtime import load_email_center_snapshot
from src.emails.sandbox import inspect_gmail_sandbox
from src.google.exceptions import GoogleIntegrationError
from src.settlement.periods import SettlementPeriodService
from src.ui.layout import page_setup, render_alerts, render_kpis


@st.cache_data(ttl=300, show_spinner="Building safe email readiness…")
def load_snapshot(period_code: str):
    return load_email_center_snapshot(period_code)


@st.dialog("Partner Email Package", width="large")
def email_detail(row) -> None:
    st.markdown(f"### {row.restaurant}")
    st.caption(f"{row.restaurant_id} · {row.package.period_code}")
    st.markdown("#### Recipient")
    st.code(row.recipient or "EMAIL MISSING", language=None)
    st.caption("CC is disabled by default and no internal recipient is added.")
    st.markdown("#### Subject")
    st.code(row.package.subject, language=None)
    st.markdown("#### Email body preview")
    st.text(row.package.body)
    st.markdown("#### Immutable attachment references")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Type": item.document_type,
                    "Version": item.version,
                    "Status": item.status,
                    "Content hash": item.content_hash[:16] + "…",
                }
                for item in row.package.document_refs
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown("#### Readiness")
    st.write(
        {
            "Financial": row.financial_status,
            "Documents": row.document_status,
            "Email": row.email_status,
            "Authorization": row.authorization_status,
            "Send": row.send_status,
        }
    )
    if row.blockers:
        st.error(" · ".join(row.blockers))
    st.warning("PREVIEW ONLY · No Gmail draft or message is created.")


settings = get_settings()
capability = inspect_gmail_capability(settings)
sandbox = inspect_gmail_sandbox(settings)
periods = SettlementPeriodService(settings.timezone)
today = datetime.now(ZoneInfo(settings.timezone)).date()
latest = periods.latest_complete(as_of=today)
page_setup("Email Center", period_code=latest.period_code)

st.title("Email Center")
st.caption("Recipient validation, immutable package preview and production safety gates")
controls = st.columns(3)
year = controls[0].selectbox("Year", range(today.year, 2023, -1), index=0)
month = controls[1].selectbox("Month", range(1, 13), index=latest.month - 1)
half = controls[2].selectbox("Half", ["P1", "P2"], index=1 if latest.half == "P2" else 0)
period = periods.create(year, month, half, as_of=today)

try:
    snapshot = load_snapshot(period.period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Email Center is unavailable: {exc}")
    st.stop()

st.markdown(f"### {snapshot.period_code} · {snapshot.period_status.value}")
if sandbox.execution_mode.value == "SANDBOX":
    st.warning(
        "SANDBOX MODE · All provider recipients are overridden to the configured "
        "sandbox mailbox. Production sending is OFF."
    )
st.markdown(
    """<div class="cc-off-banner"><div class="cc-off-title">PRODUCTION SEND · DISABLED</div>
    <div class="cc-off-copy">Preview is side-effect free. No Gmail draft or external message can be created with the current backend flags.</div></div>""",
    unsafe_allow_html=True,
)
render_kpis(
    [
        ("Eligible Restaurants", f"{snapshot.identity_ready:,}", "Identity-ready scope"),
        ("Financially Ready", f"{snapshot.settlement_ready:,}", "Settlement status READY"),
        ("Documents Ready", f"{snapshot.document_ready:,}", "Production documents only"),
        ("Email Ready", f"{snapshot.email_ready:,}", "All pre-authorization gates"),
        ("Blocked", f"{snapshot.blocked:,}", "Multiple blockers retained"),
        ("Authorized", f"{snapshot.authorized:,}", "Current snapshot only"),
        ("Sent", f"{snapshot.sent:,}", "Provider-confirmed"),
        ("Failed", f"{snapshot.failed:,}", "Retry requires current authorization"),
    ]
)
render_alerts(
    [
        ("Manual Review", f"{snapshot.financial_review_pending:,}", "coral"),
        ("Formula Blocked", f"{snapshot.formula_blocked:,}", "coral"),
        ("Legal Blocked", f"{snapshot.legal_blocked:,}", "violet"),
        ("Missing Email", f"{snapshot.missing_email:,}", "coral"),
        ("Invalid Email", f"{snapshot.invalid_email:,}", "coral"),
        ("SEND Eligible", f"{snapshot.production_send_eligible:,}", "green"),
    ]
)

st.markdown("### Partner delivery queue")
search = st.text_input("Search", placeholder="Restaurant, ID or recipient")
filter_columns = st.columns(4)
city = filter_columns[0].selectbox("City", ["All"] + sorted({row.city for row in snapshot.rows if row.city}))
am = filter_columns[1].selectbox("AM", ["All"] + sorted({row.account_manager for row in snapshot.rows if row.account_manager}))
email_filter = filter_columns[2].selectbox("Email", ["All", "EMAIL_VALID", "EMAIL_MISSING", "EMAIL_INVALID"])
send_filter = filter_columns[3].selectbox("Send state", ["All", "NOT_ATTEMPTED", "SENT", "FAILED"])
rows = [
    row
    for row in snapshot.rows
    if (not search or search.casefold() in f"{row.restaurant} {row.restaurant_id} {row.recipient}".casefold())
    and (city == "All" or row.city == city)
    and (am == "All" or row.account_manager == am)
    and (email_filter == "All" or row.email_status == email_filter)
    and (send_filter == "All" or row.send_status == send_filter)
]
frame = pd.DataFrame(
    [
        {
            "Restaurant": row.restaurant,
            "Restaurant ID": row.restaurant_id,
            "Period": snapshot.period_code,
            "Production Recipient": row.recipient or "—",
            "Recipient Source": row.recipient_source or "—",
            "Sandbox Recipient": sandbox.sandbox_recipient or "—",
            "Actual delivery target": (
                sandbox.sandbox_recipient
                if sandbox.execution_mode.value == "SANDBOX"
                else "NONE"
            ),
            "Documents": len(row.package.document_refs),
            "Package Status": row.package.workflow_status.value,
            "Gmail Mode": sandbox.execution_mode.value,
            "Draft Status": "NOT_RUN",
            "Financial Status": row.financial_status,
            "Document Status": row.document_status,
            "Email Status": row.email_status,
            "Authorization": row.authorization_status,
            "Send Status": row.send_status,
            "Blocking Reason": " · ".join(row.blockers),
        }
        for row in rows
    ]
)
event = st.dataframe(
    frame,
    hide_index=True,
    width="stretch",
    selection_mode="single-row",
    on_select="rerun",
)
if event.selection.rows:
    email_detail(rows[event.selection.rows[0]])

with st.expander("Gmail capability · configuration only"):
    st.write(
        {
            "Credentials detected": capability.credentials_detected,
            "Authentication": capability.authentication.value,
            "Draft capability": capability.draft_capability.value,
            "Send capability": capability.send_capability.value,
            "Authentication method": sandbox.auth_method.value,
            "Execution mode": sandbox.execution_mode.value,
            "Sender configured": sandbox.sender_configured,
            "Sandbox recipient configured": sandbox.sandbox_recipient_valid,
            "Sandbox draft execution": sandbox.draft_execution_allowed,
            "Sandbox send execution": sandbox.send_execution_allowed,
            "Sandbox send flag": settings.gmail_sandbox_send_enabled,
            "Production safety flag": (
                "ON" if settings.production_email_send_enabled else "OFF"
            ),
        }
    )
    st.caption(
        "No Gmail API request or test message was performed. Sandbox delivery can "
        "never retain the production recipient and requires separate explicit settings."
    )
