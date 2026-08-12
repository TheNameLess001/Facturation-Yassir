import pandas as pd
import streamlit as st

from src.config import get_settings
from src.payments import PaymentRegistry
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Payments")
period_banner()
st.title("Payments & Reconciliation")
settings = get_settings()
period_id = st.selectbox("Settlement period", ["2026-08-P1", "2026-08-P2"])
payments = PaymentRegistry(settings.payment_registry_path).list_for_period(period_id)
render_kpis(
    [
        ("Paid partners", str(len(payments)), "Unique payment records"),
        (
            "Paid value",
            f"{sum((item.amount for item in payments), 0):,.2f} MAD",
            "Recorded payments",
        ),
        ("Pending", "0", "Awaiting payment"),
        ("Disputed", "0", "Requires review"),
        ("Period lock", "Open", "Admin-only lock"),
    ]
)
st.dataframe(
    pd.DataFrame([item.model_dump(mode="json") for item in payments]),
    hide_index=True,
    use_container_width=True,
)
st.caption(
    "Payments require SENT settlements. Duplicate restaurant/period/reference combinations are rejected."
)
