from __future__ import annotations

from src.config import get_settings
from src.emails.gmail_adapter import inspect_gmail_capability
from src.emails.runtime import build_email_center_snapshot
from src.settlement.phase5_runtime import load_phase5_workspace


def main() -> None:
    settings = get_settings()
    workspace = load_phase5_workspace("2026-07-P2")
    snapshot = build_email_center_snapshot(workspace, settings=settings)
    gmail = inspect_gmail_capability(settings)
    print("REAL_PHASE10_READ_ONLY")
    print("period", snapshot.period_code)
    print("scope_restaurants", snapshot.scope_restaurants)
    print("identity_ready", snapshot.identity_ready)
    print("settlement_ready", snapshot.settlement_ready)
    print("financial_review_pending_restaurants", snapshot.financial_review_pending)
    print("document_ready", snapshot.document_ready)
    print("email_ready", snapshot.email_ready)
    print("missing_email", snapshot.missing_email)
    print("invalid_email", snapshot.invalid_email)
    print("formula_blocked", snapshot.formula_blocked)
    print("legal_blocked", snapshot.legal_blocked)
    print("admin_authorized", snapshot.authorized)
    print("production_send_eligible", snapshot.production_send_eligible)
    print("sent", snapshot.sent)
    print("failed", snapshot.failed)
    print("production_send_flag", settings.production_email_send_enabled)
    print("google_credentials_detected", settings.google_credentials_configured)
    print("gmail_authentication", gmail.authentication.value)
    print("gmail_draft_capability", gmail.draft_capability.value)
    print("gmail_send_capability", gmail.send_capability.value)
    print("external_messages_sent_by_validation", 0)


if __name__ == "__main__":
    main()
