import streamlit as st

from src.ui.layout import page_setup, period_banner

page_setup("Audit")
period_banner()
st.title("Immutable Audit Trail")
st.caption(
    "Critical user and system actions are append-only and cannot be deleted through CashCo."
)
categories = [
    "Drive sync",
    "Source import",
    "Manual adjustment",
    "Validation",
    "Documents",
    "Admin authorization",
    "Email",
    "Payment",
    "Lock / unlock",
]
columns = st.columns(3)
for index, category in enumerate(categories):
    with columns[index % 3]:
        st.markdown(
            f'<div class="cc-card"><div class="cc-kpi-label">{category}</div><div class="cc-kpi-value">0</div><div class="cc-kpi-note">No persisted production events</div></div>',
            unsafe_allow_html=True,
        )
st.info(
    "The demo environment has no production audit events. Service-level audit tests cover adjustments, payments, locks, and unlocks."
)
