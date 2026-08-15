from __future__ import annotations

from collections import Counter

from src.config import get_settings
from src.documents.legal_readiness import CashCoDocumentType
from src.documents.phase8 import Phase8DocumentEngine
from src.documents.publishing import (
    DocumentPublicationRepository,
    DocumentPublishingService,
    DocumentPublishMode,
    DocumentStorageMode,
    inspect_drive_destination,
    validate_document_storage_write,
)
from src.documents.storage import build_document_storage_service
from src.emails.gmail_adapter import (
    ProductionGmailAdapter,
    validate_gmail_capability,
)
from src.emails.phase10_models import GmailAuthenticationStatus
from src.emails.runtime import build_email_center_snapshot
from src.emails.sandbox import (
    GmailSandboxDraftService,
    SandboxDraftStatus,
    inspect_gmail_sandbox,
)
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.gmail_auth import build_gmail_api
from src.google.gmail_service import GoogleGmailService
from src.models.domain import AuditEvent
from src.operations.activation4 import run_activation4_dry_run
from src.operations.go_live_runtime import build_go_live_snapshot
from src.settlement.phase5_runtime import load_phase5_workspace


def main() -> None:
    settings = get_settings()
    if settings.production_email_send_enabled:
        raise RuntimeError("PRODUCTION_EMAIL_SEND_ENABLED_MUST_REMAIN_FALSE")
    source_drive = GoogleDriveService(build_google_credentials(settings))
    workspace = load_phase5_workspace(
        "2026-07-P2", settings=settings, drive=source_drive
    )
    email = build_email_center_snapshot(workspace, settings=settings)
    workflow = EmailWorkflowRepository(settings.email_workflow_registry_path)
    storage_mode = DocumentStorageMode(settings.document_storage_mode)
    storage_drive = (
        build_document_storage_service(settings)
        if storage_mode != DocumentStorageMode.DISABLED
        else source_drive
    )
    destination = inspect_drive_destination(
        storage_drive,
        settings.documents_folder_id,
        storage_mode=storage_mode,
        shared_drive_id=settings.documents_shared_drive_id,
    )
    drive_validation = validate_document_storage_write(storage_drive, destination)
    _audit(
        workflow,
        "DRIVE_CAPABILITY_CHECKED",
        details={
            "storage_mode": storage_mode.value,
            "destination_type": destination.destination_type.value,
            "can_create": destination.can_create,
            "validation_status": drive_validation.status.value,
        },
    )
    if drive_validation.created:
        _audit(
            workflow,
            "DRIVE_VALIDATION_FILE_CREATED",
            details={"provider_file_id": drive_validation.provider_file_id},
        )

    publication = None
    idempotency = None
    if drive_validation.status.value in {"PASS", "ALREADY_VALIDATED"}:
        publisher = DocumentPublishingService(
            storage_drive,
            DocumentPublicationRepository(
                settings.document_publication_registry_path
            ),
            destination,
            mode=DocumentPublishMode.SAMPLE,
            actor="cashco.activation5",
        )
        dry_run = run_activation4_dry_run(
            workspace,
            sample_size=1,
            publishing=publisher,
            settings=settings,
        )
        publication = dry_run.publications
        if publication and not publication.failed:
            idempotency = run_activation4_dry_run(
                workspace,
                sample_size=1,
                publishing=publisher,
                settings=settings,
            ).publications
        if publication and publication.published:
            _audit(
                workflow,
                "DOCUMENT_SAMPLE_PUBLISHED",
                details={"documents": publication.published, "restaurants": 1},
            )

    gmail = validate_gmail_capability(settings)
    sandbox = inspect_gmail_sandbox(settings)
    _audit(
        workflow,
        "GMAIL_AUTH_VALIDATED",
        details={
            "auth_method": sandbox.auth_method.value,
            "authentication": gmail.authentication.value,
            "sender_configured": sandbox.sender_configured,
        },
    )
    sandbox_draft = None
    if (
        gmail.authentication == GmailAuthenticationStatus.PASS
        and sandbox.draft_execution_allowed
    ):
        ready_rows = sorted(
            (row for row in email.rows if row.preauthorization_ready),
            key=lambda row: row.restaurant_id,
        )
        if ready_rows:
            selected = ready_rows[0]
            restaurant = next(
                item
                for item in workspace.registry.restaurants
                if item.restaurant_id == selected.restaurant_id
            )
            settlement = next(
                item
                for item in workspace.summary.restaurants
                if item.restaurant_id == selected.restaurant_id
            )
            engine = Phase8DocumentEngine()
            attachments = tuple(
                engine.render_production_document(
                    engine.production_candidate(kind, restaurant, settlement)
                ).content
                for kind in CashCoDocumentType
            )
            drafts = GmailSandboxDraftService(
                settings,
                workflow,
                ProductionGmailAdapter(
                    GoogleGmailService(build_gmail_api(settings))
                ),
                actor_id="cashco.activation5",
            )
            sandbox_draft = drafts.create_draft(selected.package, attachments)
            if sandbox_draft.status == SandboxDraftStatus.CREATED:
                sandbox_draft = drafts.create_draft(selected.package, attachments)

    go_live = build_go_live_snapshot(
        workspace,
        settings=settings,
        destination=destination,
        drive_validation=drive_validation,
        gmail=gmail,
        sandbox=sandbox,
        sandbox_draft=sandbox_draft,
    )
    _audit(
        workflow,
        "GO_LIVE_READINESS_EVALUATED",
        details={
            "status": go_live.readiness.status.value,
            "blockers": [item.value for item in go_live.readiness.blockers],
        },
    )
    legal = workspace.registry.partner_legal_master
    issues = Counter(item.code for item in legal.issues) if legal else Counter()

    print("ACTIVATION5_REAL_VALIDATION")
    print("period", go_live.period_code)
    print("document_ready", go_live.document_ready)
    print("email_package_ready", go_live.email_package_ready)
    print("storage_mode", storage_mode.value)
    print("drive_destination", destination.folder_name or "NOT_CONFIGURED")
    print("drive_destination_type", destination.destination_type.value)
    print("drive_can_read", destination.can_read)
    print("drive_can_create_folder", destination.can_create)
    print("drive_can_create_file", drive_validation.status.value)
    print("drive_can_update", destination.can_update)
    print("drive_can_list", destination.can_list)
    print("drive_can_retrieve_metadata", destination.can_retrieve_metadata)
    print("drive_validation_write", drive_validation.status.value)
    print("sample_documents_published", publication.published if publication else 0)
    print("sample_publish_failures", publication.failed if publication else 0)
    print(
        "document_idempotency",
        idempotency.already_published if idempotency else 0,
    )
    print("gmail_auth_method", sandbox.auth_method.value)
    print("gmail_authentication", gmail.authentication.value)
    print("gmail_sender_configured", sandbox.sender_configured)
    print("sandbox_recipient_configured", sandbox.sandbox_recipient_valid)
    print(
        "sandbox_draft",
        sandbox_draft.status.value if sandbox_draft else "NOT_RUN",
    )
    print("sandbox_sends", 0)
    print("production_sends", 0)
    print("admin_authorization", "NO")
    print("production_email_send_enabled", settings.production_email_send_enabled)
    print("go_live_readiness", go_live.readiness.status.value)
    print(
        "go_live_blockers",
        "|".join(item.value for item in go_live.readiness.blockers) or "NONE",
    )
    print("ready_candidates_affected_by_legal_issues", go_live.ready_candidates_affected_by_legal_master_issues)
    print("missing_ids", issues["MISSING_ID"])
    print("duplicate_groups", legal.profile.duplicate_id_groups if legal and legal.profile else 0)
    print("legal_conflicts", legal.profile.conflict_groups if legal and legal.profile else 0)
    print("invalid_ice", issues["INVALID_ICE"])


def _audit(
    repository: EmailWorkflowRepository,
    event_type: str,
    *,
    details: dict[str, object],
) -> None:
    repository.append_audit(
        AuditEvent(
            event_type=event_type,
            actor_id="cashco.activation5",
            period_id="2026-07-P2",
            entity_type="GO_LIVE_VALIDATION",
            entity_id="2026-07-P2",
            details=details,
        )
    )


if __name__ == "__main__":
    main()
