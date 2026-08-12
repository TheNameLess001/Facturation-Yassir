from collections.abc import Sequence

import streamlit as st

from src.auth import AuthService
from src.config import get_settings
from src.ui.styles import CSS


def page_setup(title: str) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    page_header()


def page_header() -> None:
    user = AuthService(get_settings()).current_user()
    st.markdown(
        f"""<div class="cc-header"><div><div class="cc-brand">CashCo</div>
        <div class="cc-subtitle">Partner Billing Control Tower</div></div>
        <div class="cc-meta"><span class="cc-chip">2026-08-P1</span>
        <span class="cc-chip">Data synced · Mock</span><span class="cc-chip">{user.name} · {user.role}</span>
        <span class="cc-chip off">Automation OFF</span></div></div>""",
        unsafe_allow_html=True,
    )


def period_banner() -> None:
    st.markdown(
        """<div class="cc-period"><div><div class="cc-period-label">Current settlement period</div>
        <div class="cc-period-value">01 → 15 August 2026</div></div>
        <div class="cc-period-value">2026-08-P1</div></div>""",
        unsafe_allow_html=True,
    )


def render_kpis(items: Sequence[tuple[str, str, str]]) -> None:
    for columns_start in range(0, len(items), 5):
        chunk = items[columns_start : columns_start + 5]
        columns = st.columns(len(chunk))
        for column, (label, value, note) in zip(columns, chunk, strict=True):
            with column:
                st.markdown(
                    f'<div class="cc-card"><div class="cc-kpi-label">{label}</div>'
                    f'<div class="cc-kpi-value">{value}</div><div class="cc-kpi-note">{note}</div></div>',
                    unsafe_allow_html=True,
                )


def status_badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="cc-status {tone}">{label}</span>'


def placeholder_page(title: str, description: str) -> None:
    page_setup(title)
    period_banner()
    st.title(title)
    st.caption(description)
    st.info(
        "Phase 1 foundation · This workspace is ready for its dedicated implementation phase."
    )
