from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.auth import AuthService, Permission, RBACService
from src.config import get_settings
from src.documents.publishing import (
    DocumentStorageMode,
    inspect_drive_destination,
    inspect_existing_document_storage_validation,
)
from src.documents.storage import build_document_storage_service
from src.emails.gmail_adapter import validate_gmail_capability
from src.emails.period_locking import Phase10PeriodLockService
from src.emails.phase10_authorization import PeriodAuthorizationService
from src.emails.phase10_models import EmailAutomationMode
from src.emails.runtime import load_email_center_snapshot
from src.emails.sandbox import SandboxDraftStatus, inspect_gmail_sandbox
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError
from src.operations.go_live_runtime import build_go_live_snapshot
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import load_phase5_workspace
from src.ui.layout import page_setup, render_kpis

settings = get_settings()
user = AuthService(settings).current_user()
page_setup("Admin Control Center")
if not RBACService().can(user, Permission.AUTHORIZE_AUTOMATION):
    st.error("Admin access required.")
    st.stop()

periods = SettlementPeriodService(settings.timezone)
latest = periods.latest_complete(as_of=datetime.now(ZoneInfo(settings.timezone)).date())
st.title("Admin Control Center")
st.caption("Snapshot-bound authorization, production safety and period lifecycle")
period_code = st.selectbox("Settlement period", [latest.period_code])
try:
    workspace = load_phase5_workspace(period_code)
    snapshot = load_email_center_snapshot(period_code)
except (GoogleIntegrationError, ValueError, OSError) as exc:
    st.error(f"Admin readiness is unavailable: {exc}")
    st.stop()

repository = EmailWorkflowRepository(settings.email_workflow_registry_path)
authorization = repository.active_authorization(period_code)
period_mode = repository.mode_for_period(period_code)
capability = validate_gmail_capability(settings)
sandbox = inspect_gmail_sandbox(settings)
storage_mode = DocumentStorageMode(settings.document_storage_mode)
try:
    storage_drive = (
        build_document_storage_service(settings)
        if storage_mode != DocumentStorageMode.DISABLED
        else GoogleDriveService(build_google_credentials(settings))
    )
    drive_destination = inspect_drive_destination(
        storage_drive,
        settings.documents_folder_id,
        storage_mode=storage_mode,
        shared_drive_id=settings.documents_shared_drive_id,
    )
    drive_validation = inspect_existing_document_storage_validation(
        storage_drive, drive_destination
    )
except (GoogleIntegrationError, ValueError, OSError):
    source_drive = GoogleDriveService(build_google_credentials(settings))
    drive_destination = inspect_drive_destination(
        source_drive, None, storage_mode=storage_mode
    )
    drive_validation = None
sandbox_drafts = repository.list_latest_sandbox_drafts(period_code)
go_live = build_go_live_snapshot(
    workspace,
    settings=settings,
    destination=drive_destination,
    drive_validation=drive_validation,
    gmail=capability,
    sandbox=sandbox,
    sandbox_draft=sandbox_drafts[-1] if sandbox_drafts else None,
    document_storage_ready=(
        settings.r2_configured
    ),
)
st.markdown(
    f"""<div class="cc-off-banner"><div class="cc-off-title">AUTOMATION · {period_mode.value}</div>
    <div class="cc-off-copy">PRODUCTION SEND FLAG · {'ON' if settings.production_email_send_enabled else 'OFF'} · Current authorization: {'ACTIVE' if authorization else 'NONE'}</div></div>""",
    unsafe_allow_html=True,
)

st.markdown("### Operational workflow")
stages = [
    ("DATA", "COMPLETE"),
    ("IDENTITY", "WARNING" if workspace.summary.identity_blocked_restaurants else "COMPLETE"),
    ("SETTLEMENT", "READY" if snapshot.settlement_ready else "BLOCKED"),
    ("FINANCIAL REVIEW", "WARNING" if snapshot.financial_review_pending else "COMPLETE"),
    ("DOCUMENTS", "READY" if snapshot.document_ready else "BLOCKED"),
    ("EMAIL", "READY" if snapshot.email_ready else "BLOCKED"),
    ("AUTHORIZATION", "COMPLETE" if authorization else "BLOCKED"),
    ("SEND", "COMPLETE" if snapshot.sent else "BLOCKED"),
    ("LOCK", "COMPLETE" if repository.period_locked(period_code) else "BLOCKED"),
]
render_kpis([(name, status, period_code) for name, status in stages])

st.markdown("### Go-Live Readiness")
render_kpis(
    [
        ("Financial Policy", "READY", "cashco_legacy_v1"),
        ("Legal Master", "READY", "Live read-only source"),
        ("Settlement", "READY", f"{snapshot.settlement_ready:,} restaurants"),
        ("Documents", "READY", f"{snapshot.document_ready:,} candidates"),
        (
            "Document Storage",
            (
                "READY"
                if settings.r2_configured
                else "BLOCKED"
            ),
            f"{settings.document_storage_provider} · Google Drive deprecated",
        ),
        ("Email Packages", "READY", f"{snapshot.email_ready:,} buildable"),
        ("Gmail Authentication", capability.authentication.value, sandbox.auth_method.value),
        ("Sender", "READY" if sandbox.sender_configured else "BLOCKED", "Explicit company identity"),
        (
            "Sandbox Validation",
            (
                "READY"
                if go_live.sandbox_draft
                and go_live.sandbox_draft.status
                in {
                    SandboxDraftStatus.CREATED,
                    SandboxDraftStatus.ALREADY_CREATED,
                }
                else "BLOCKED"
            ),
            "Draft-only provider test",
        ),
        ("Authorization", "NOT AUTHORIZED", "Production count 0"),
        ("Production Flag", "OFF", "Backend hard stop"),
    ]
)
if go_live.readiness.status.value == "READY_FOR_CANARY_AUTHORIZATION":
    st.success("GO-LIVE · READY FOR CANARY AUTHORIZATION · Production SEND remains OFF")
else:
    st.error(
        "GO-LIVE · BLOCKED · "
        + " · ".join(item.value for item in go_live.readiness.blockers)
    )

st.markdown("### Authorization impact preview")
render_kpis(
    [
        ("Total Restaurants", f"{snapshot.scope_restaurants:,}", "Invoice Scope"),
        ("Eligible", f"{snapshot.email_ready:,}", "All pre-authorization gates"),
        ("Blocked", f"{snapshot.blocked:,}", "Multiple blockers retained"),
        ("Email Packages", f"{len(snapshot.rows):,}", "Preview models"),
        ("Recipients Valid", f"{sum(row.email_status == 'EMAIL_VALID' for row in snapshot.rows):,}", "No CC by default"),
        ("Documents Attached", "0", "Production-validated only"),
    ]
)
st.dataframe(
    pd.DataFrame(
        [
            ("PAY_PARTNER orders", workspace.summary.pay_partner_orders),
            ("EXCLUDE orders", workspace.summary.excluded_orders),
            ("COMPENSATION orders", workspace.summary.yassir_compensation_orders),
            ("MANUAL_REVIEW orders", workspace.summary.manual_review_orders),
            ("Commission mismatches", workspace.summary.commission_mismatches),
            ("Invalid financial rows", workspace.summary.invalid_financial_rows),
            ("Formula blockers", snapshot.formula_blocked),
            ("Legal blockers", snapshot.legal_blocked),
            ("Missing/invalid email", snapshot.missing_email + snapshot.invalid_email),
        ],
        columns=["Control", "Count"],
    ),
    hide_index=True,
    width="stretch",
)
st.info(
    "Financial policy cashco_legacy_v1 is certified. Document production remains "
    "subject to legal, review, commission and financial-data gates."
)

st.markdown("### Financial Formula Certification")
certification = LegacyFormulaRegistry().certification()
st.caption(
    "Authoritative source · 4_Generateur bulk.py · Business approval CONFIRMED"
)
render_kpis(
    [
        ("Policy", certification.policy_version or "NOT ASSIGNED", "Production source approved"),
        ("Commission Base", "CERTIFIED", "Sales TTC / 1.2"),
        ("HT / TVA / TTC", "CERTIFIED", "TVA 20% on commission HT"),
        ("Note de débours", "CERTIFIED", "Sales TTC minus invoice TTC"),
        ("Net Payable", "CERTIFIED", "No intermediate rounding"),
        ("Certification", certification.status.value, certification.reason),
    ]
)

st.markdown("### Period-scoped automation")
mode = st.radio(
    "Mode",
    list(EmailAutomationMode),
    index=list(EmailAutomationMode).index(period_mode),
    horizontal=True,
)
if mode == EmailAutomationMode.OFF:
    st.success("Safe state: no Gmail write, draft, send, or automatic authorization.")
    if mode != period_mode and st.button("Apply OFF mode"):
        PeriodAuthorizationService(repository).set_safe_mode(
            user=user, period_code=period_code, mode=mode
        )
        st.rerun()
elif mode == EmailAutomationMode.PREVIEW:
    st.info("Preview generation is side-effect free and requires no Gmail API write.")
    if mode != period_mode and st.button("Apply PREVIEW mode"):
        PeriodAuthorizationService(repository).set_safe_mode(
            user=user, period_code=period_code, mode=mode
        )
        st.rerun()
else:
    expected = (
        f"SEND CASHCO {period_code}"
        if mode == EmailAutomationMode.SEND
        else f"AUTHORIZE {period_code}"
    )
    phrase = st.text_input(f"Type {expected}")
    accepted = st.checkbox("I reviewed this exact immutable period snapshot.")
    packages = tuple(
        row.package for row in snapshot.rows if row.preauthorization_ready
    )
    mode_enabled = (
        mode == EmailAutomationMode.DRAFT
        and settings.email_allow_drafts
        and snapshot.document_ready > 0
    ) or (
        mode == EmailAutomationMode.SEND
        and settings.email_allow_send
        and settings.production_email_send_enabled
        and snapshot.production_send_eligible > 0
    )
    if st.button(
        "Create snapshot authorization",
        type="primary",
        disabled=not (accepted and phrase == expected and mode_enabled),
    ):
        PeriodAuthorizationService(repository).authorize(
            user=user,
            period_code=period_code,
            mode=mode,
            packages=packages,
            eligible_restaurant_count=len(packages),
            blocked_restaurant_count=snapshot.blocked,
            confirmation_text=phrase,
        )
        st.cache_data.clear()
        st.rerun()
    if not mode_enabled:
        st.error("Authorization blocked by production policy, document gates, or backend safety flags.")

st.markdown("### Gmail capability")
st.write(
    {
        "Credentials detected": capability.credentials_detected,
        "Authentication": capability.authentication.value,
        "Draft capability": capability.draft_capability.value,
        "Send capability": capability.send_capability.value,
    }
)
st.caption("Configuration-only check · no Gmail API call was made.")

st.markdown("### Period locking")
locked = repository.period_locked(period_code)
st.write("LOCKED" if locked else "OPEN")
lock_service = Phase10PeriodLockService(repository)
with st.expander("Controlled lock / reopen"):
    reason = st.text_area("Reason", key="period_lock_reason")
    if not locked:
        manual_close = st.checkbox(
            "Approved manual close (no production sends will be inferred)",
            key="period_manual_close",
        )
        lock_phrase = st.text_input(f"Type LOCK {period_code}")
        if st.button(
            "Lock period",
            disabled=not (
                reason.strip()
                and lock_phrase == f"LOCK {period_code}"
                and (
                    (
                        snapshot.email_ready > 0
                        and snapshot.sent == snapshot.email_ready
                    )
                    or manual_close
                )
            ),
        ):
            lock_service.lock(
                user=user,
                period_code=period_code,
                intended_send_count=snapshot.email_ready,
                manual_close=manual_close,
                reason=reason,
                confirmation_text=lock_phrase,
            )
            st.cache_data.clear()
            st.rerun()
    else:
        reopen_phrase = st.text_input(f"Type REOPEN {period_code}")
        if st.button(
            "Reopen period",
            disabled=not (
                reason.strip() and reopen_phrase == f"REOPEN {period_code}"
            ),
        ):
            lock_service.reopen(
                user=user,
                period_code=period_code,
                reason=reason,
                confirmation_text=reopen_phrase,
            )
            st.cache_data.clear()
            st.rerun()

st.markdown("### Authorization history")
st.dataframe(
    pd.DataFrame(
        repository.authorization_history(period_code),
        columns=["Authorization ID", "Status", "Created at"],
    ),
    hide_index=True,
    width="stretch",
)
