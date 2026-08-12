import streamlit as st

from src.auth import AuthService, Permission, RBACService
from src.config import get_settings
from src.models.enums import AutomationMode
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Admin Control Center")
user = AuthService(get_settings()).current_user()
if not RBACService().can(user, Permission.AUTHORIZE_AUTOMATION):
    st.error("Admin access required.")
    st.stop()

period_banner()
st.title("Admin Control Center")
st.caption("Period-specific authorization. Every new settlement period starts OFF.")
st.markdown(
    """<div class="cc-off-banner"><div class="cc-off-title">CURRENT AUTOMATION STATUS · OFF</div>
<div class="cc-off-copy">WAITING FOR ADMIN AUTHORIZATION · No partner will receive anything. Last Admin action: —</div></div>""",
    unsafe_allow_html=True,
)
st.info(
    "Data source readiness is monitored separately. Future authorization requires "
    "valid Invoice Scope, Restaurant Registry, settlement period, and documents."
)

st.markdown(
    '<div class="cc-section">Period impact snapshot</div>', unsafe_allow_html=True
)
render_kpis(
    [
        ("Eligible", "423", "Mock · official Invoice Scope"),
        ("Ready", "390", "Can enter preparation"),
        ("Blocked", "17", "Cannot be authorized"),
        ("Needs review", "16", "Finance action required"),
        ("Net payable", "3.90M MAD", "Mock settlement total"),
    ]
)

st.markdown('<div class="cc-section">Automation mode</div>', unsafe_allow_html=True)
mode = st.radio(
    "Select a mode",
    list(AutomationMode),
    format_func=lambda item: item.value,
    horizontal=True,
)
if mode in {AutomationMode.CREATE_DRAFTS, AutomationMode.SEND_EMAILS}:
    st.warning(
        "Impact preview: 390 partners · 390 emails · 0 documents generated · authorization is currently blocked because documents are missing."
    )
    accepted = st.checkbox(
        "I confirm that I reviewed the settlement period and authorize this workflow."
    )
    phrase = st.text_input(
        "Type CONFIRM SEND", disabled=mode != AutomationMode.SEND_EMAILS
    )
    exact = mode != AutomationMode.SEND_EMAILS or phrase == "CONFIRM SEND"
    st.button(
        "Authorize workflow",
        type="primary",
        disabled=not accepted or not exact or True,
        help="The displayed settlement population is mock data, so authorization is intentionally disabled.",
    )
    st.caption(
        "Preview UI only — no authorization is persisted because no validated production settlement snapshot exists."
    )
elif mode == AutomationMode.PREVIEW_ONLY:
    st.info(
        "Dry-run preview shows WOULD SEND / WOULD SKIP / WOULD BLOCK and has no side effects."
    )
else:
    st.success("Safe state: automation remains OFF.")

st.markdown(
    '<div class="cc-section">Authorization history</div>', unsafe_allow_html=True
)
st.dataframe(
    {
        "Period": [],
        "Admin": [],
        "Mode": [],
        "Authorized at": [],
        "Partner count": [],
        "Status": [],
    },
    use_container_width=True,
)
