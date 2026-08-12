import streamlit as st

from src.config import get_settings
from src.ui.layout import page_setup, period_banner

page_setup("Data Sources")
period_banner()
st.title("Google Drive Configuration")
st.caption("Configuration status only · No Drive connection or source discovery is run.")

settings = get_settings()
credentials_status = (
    "CONFIGURED" if settings.google_credentials_configured else "NOT_CONFIGURED"
)

credentials_column, sources_column = st.columns(2)
with credentials_column:
    st.metric("Google Credentials", credentials_status)
with sources_column:
    st.metric("Drive Sources", "NOT TESTED")

if not settings.google_credentials_configured:
    st.warning(
        "Google credentials are not configured. Streamlit remains available in its safe state."
    )
elif not settings.drive_sources_configured:
    st.warning("Credentials are configured, but one or more required Drive IDs are absent.")
else:
    st.info(
        "Credentials and Drive IDs are configured. Connectivity has not been tested."
    )

st.markdown("### Access policy")
st.write("Read only: Admin Earnings, Finance Tracking, and RST List.")
st.write("Future read/write workspace: Config, Processed, Partners, Documents, and Audit.")
st.caption(
    "Automation remains OFF. No ingestion, document generation, email, or Drive write workflow is available here."
)

with st.expander("Technical configuration details"):
    configured_ids = {
        "Admin Earnings folder": settings.admin_earnings_folder_id,
        "RST List file": settings.rst_list_file_id,
        "Finance Tracking file": settings.finance_tracking_file_id,
        "Finance Tracking folder": settings.finance_tracking_folder_id,
        "Config folder": settings.config_folder_id,
        "Processed folder": settings.processed_folder_id,
        "Partners folder": settings.partners_folder_id,
        "Documents folder": settings.documents_folder_id,
        "Audit folder": settings.audit_folder_id,
    }
    for label, value in configured_ids.items():
        st.write(f"{label}: {'Configured' if value else 'Not configured'}")
