from __future__ import annotations

from collections import Counter

from src.config import get_settings
from src.documents.publishing import DocumentPublicationRepository
from src.documents.r2_storage import CloudflareR2DocumentSource
from src.emails.attachments import PublicationAttachmentLoader
from src.emails.gmail_adapter import ProductionGmailAdapter, validate_gmail_capability
from src.emails.runtime import build_email_center_snapshot
from src.emails.sandbox import GmailSandboxDraftService, inspect_gmail_sandbox
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.gmail_auth import build_gmail_api
from src.google.gmail_service import GoogleGmailService
from src.settlement.phase5_runtime import load_phase5_workspace

PERIOD = "2026-07-P2"


def main() -> None:
    settings = get_settings()
    workspace = load_phase5_workspace(PERIOD, settings=settings)
    snapshot = build_email_center_snapshot(
        workspace,
        settings=settings.model_copy(update={"document_storage_provider": "R2"}),
    )
    documents_published = sum(
        len(row.package.document_refs) == 3
        and all(item.status == "PUBLISHED" for item in row.package.document_refs)
        for row in snapshot.rows
    )
    reasons = Counter(
        blocker for row in snapshot.rows for blocker in row.blockers
    )
    capability = inspect_gmail_sandbox(settings)
    print("FINAL_EMAIL_REAL_VALIDATION")
    print("period", PERIOD)
    print("documents_published", documents_published)
    print("recipient_available", len(snapshot.rows) - snapshot.missing_email)
    print("recipient_valid", sum(row.email_status == "EMAIL_VALID" for row in snapshot.rows))
    print("recipient_missing", snapshot.missing_email)
    print("recipient_invalid", snapshot.invalid_email)
    print("email_package_ready", snapshot.email_ready)
    print("email_blocked", len(snapshot.rows) - snapshot.email_ready)
    print("reason_distribution", dict(sorted(reasons.items())))
    print("production_sent", snapshot.sent)
    print("gmail_mode", capability.execution_mode.value)
    print("gmail_new_oauth_configured", settings.gmail_oauth_configured)
    print("gmail_sender_configured", capability.sender_configured)
    print("sandbox_recipient_configured", capability.sandbox_recipient_valid)
    print("sandbox_send_enabled", settings.gmail_sandbox_send_enabled)
    print("production_send_enabled", settings.production_email_send_enabled)
    drafts_created = 0
    draft_status = "NOT_RUN"
    if settings.gmail_oauth_configured:
        if capability.execution_mode.value != "SANDBOX":
            raise RuntimeError("INITIAL_GMAIL_MODE_MUST_BE_SANDBOX")
        gmail_capability = validate_gmail_capability(
            settings, api=build_gmail_api(settings)
        )
        if gmail_capability.authentication.value != "PASS":
            raise RuntimeError("GMAIL_AUTH_FAILED")
        ready = sorted(
            (row for row in snapshot.rows if row.preauthorization_ready),
            key=lambda row: row.restaurant_id,
        )
        if not ready:
            raise RuntimeError("NO_EMAIL_READY_CANARY")
        row = ready[0]
        repository = DocumentPublicationRepository(
            settings.document_publication_registry_path
        )
        provider = CloudflareR2DocumentSource.from_settings(settings)
        attachments = PublicationAttachmentLoader(provider, repository).load(
            row.package
        )
        workflow = EmailWorkflowRepository(settings.email_workflow_registry_path)
        result = GmailSandboxDraftService(
            settings,
            workflow,
            ProductionGmailAdapter(GoogleGmailService(build_gmail_api(settings))),
            actor_id="cashco.final-canary",
        ).create_draft(row.package, attachments)
        drafts_created = int(result.status.value == "CREATED")
        draft_status = result.status.value
        print("canary_restaurant", row.restaurant_id)
        print("draft_provider_id", result.provider_draft_id or "NONE")
        print("attachments", len(attachments))
        print("sandbox_recipient_override", result.recipient == capability.sandbox_recipient)
    else:
        print("gmail_configuration", "GMAIL_CONFIGURATION_REQUIRED")
    print("draft_canary", draft_status)
    print("drafts_created", drafts_created)
    print("sandbox_sends", 0)
    print("production_provider_calls", 0)
    print("production_sends", 0)
    print("bank_transfers", 0)
    print("status", "PASS" if snapshot.email_ready else "BLOCKED")


if __name__ == "__main__":
    main()
