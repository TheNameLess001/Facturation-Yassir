import streamlit as st

from src.auth import AuthService
from src.config import get_settings
from src.models.enums import Role
from src.utils.logging import configure_logging

st.set_page_config(
    page_title="CashCo · Partner Billing Control Tower", page_icon="◆", layout="wide"
)
configure_logging()
user = AuthService(get_settings()).current_user()

navigation = {
    "Control Tower": [
        st.Page(
            "pages/01_Overview.py",
            title="Overview",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page("pages/03_Partners.py", title="Partners", icon=":material/storefront:"),
        st.Page(
            "pages/02_Settlements.py",
            title="Billing Operations",
            icon=":material/receipt_long:",
        ),
        st.Page(
            "pages/04_Review_Queue.py", title="Review Center", icon=":material/rule:"
        ),
    ],
    "Operations": [
        st.Page(
            "pages/05_Documents.py", title="Documents", icon=":material/description:"
        ),
        st.Page("pages/06_Emails.py", title="Email Center", icon=":material/mail:"),
        st.Page(
            "pages/07_Payments.py", title="Payments", icon=":material/account_balance:"
        ),
        st.Page("pages/08_Audit.py", title="Reports & Audit", icon=":material/analytics:"),
    ],
}
if user.role == Role.ADMIN:
    navigation["Administration"] = [
        st.Page(
            "pages/09_Admin_Control.py",
            title="Admin Control",
            icon=":material/admin_panel_settings:",
        ),
        st.Page(
            "pages/10_Settings.py", title="Data Sources", icon=":material/database:"
        ),
    ]

st.navigation(navigation).run()
