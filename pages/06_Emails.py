import pandas as pd
import streamlit as st

from src.config import get_settings
from src.emails import AutomationAuthorizationService
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Email Center")
period_banner()
st.title("Communication Control")
st.caption(
    "Preparation, authorization, delivery, failure isolation, and explicit retries."
)
settings = get_settings()
period_id = st.selectbox("Settlement period", ["2026-08-P1", "2026-08-P2"])
mode = AutomationAuthorizationService(
    settings.authorization_registry_path
).mode_for_period(period_id)
st.markdown(
    f'<div class="cc-off-banner"><div class="cc-off-title">AUTOMATION · {mode.value}</div><div class="cc-off-copy">Backend authorization status for {period_id}. No authorization means no Gmail call.</div></div>',
    unsafe_allow_html=True,
)
render_kpis(
    [
        ("Eligible", "423", "Mock dashboard population"),
        ("Email ready", "0", "Current documents required"),
        ("Missing email", "0", "Blocking"),
        ("Admin authorized", "0", "Snapshot-specific"),
        ("Sent", "0", "Idempotent communication keys"),
    ]
)
st.markdown('<div class="cc-section">Delivery queue</div>', unsafe_allow_html=True)
st.dataframe(
    pd.DataFrame(
        columns=[
            "Restaurant",
            "Recipient",
            "Net payable",
            "Documents",
            "Authorization",
            "Status",
            "Last attempt",
            "Error",
        ]
    ),
    hide_index=True,
    use_container_width=True,
)
st.info(
    "Dry-run and preview are side-effect free. Failed partners do not stop the batch; successfully sent communication keys are not automatically resent."
)
