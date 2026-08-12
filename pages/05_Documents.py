import pandas as pd
import streamlit as st

from src.config import get_settings
from src.documents import DocumentRegistry
from src.ui.layout import page_setup, period_banner, render_kpis

page_setup("Documents")
period_banner()
st.title("Document Control")
st.caption("Versioned commission invoices, disbursement notes, and partner statements.")
settings = get_settings()
registry = DocumentRegistry(settings.document_registry_path)
period_id = st.selectbox("Settlement period", ["2026-08-P1", "2026-08-P2"])
st.info(
    "Document generation is backend-blocked until each restaurant settlement is VALIDATED."
)
render_kpis(
    [
        ("Generated", "0", "Current versions"),
        ("Stale", "0", "Require regeneration"),
        ("Blocked", "0", "Missing validation/legal data"),
        ("Invoices", "0", "Unique numbering"),
        ("Statements", "0", "Partner settlement statements"),
    ]
)
st.markdown('<div class="cc-section">Document registry</div>', unsafe_allow_html=True)
st.dataframe(
    pd.DataFrame(
        columns=["Restaurant", "Type", "Number", "Status", "Generated", "Supersedes"]
    ),
    hide_index=True,
    use_container_width=True,
)
st.caption(
    f"Registry: {settings.document_registry_path} · Financial changes mark generated documents STALE; files are never silently overwritten."
)
