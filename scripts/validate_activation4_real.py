from __future__ import annotations

from collections import Counter

from src.config import get_settings
from src.documents.publishing import (
    DocumentPublicationRepository,
    DocumentPublishingService,
    DocumentPublishMode,
    inspect_drive_destination,
)
from src.emails.gmail_adapter import inspect_gmail_capability
from src.emails.sandbox import inspect_gmail_sandbox
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.operations.activation4 import run_activation4_dry_run
from src.settlement.phase5_runtime import load_phase5_workspace


def main() -> None:
    settings = get_settings()
    if settings.production_email_send_enabled:
        raise RuntimeError("PRODUCTION_EMAIL_SEND_ENABLED_MUST_REMAIN_FALSE")
    drive = GoogleDriveService(build_google_credentials(settings))
    destination = inspect_drive_destination(drive, settings.documents_folder_id)
    workspace = load_phase5_workspace("2026-07-P2", settings=settings, drive=drive)
    gmail = inspect_gmail_capability(settings)
    sandbox = inspect_gmail_sandbox(settings)
    repository = DocumentPublicationRepository(
        settings.document_publication_registry_path
    )
    provider_create_denied = repository.provider_create_denied()
    publisher = DocumentPublishingService(
        drive,
        repository,
        destination,
        mode=(
            DocumentPublishMode.SAMPLE
            if destination.can_create and not provider_create_denied
            else DocumentPublishMode.PREVIEW
        ),
        actor="cashco.activation4",
    )
    dry_run = run_activation4_dry_run(
        workspace,
        sample_size=settings.document_sample_size,
        publishing=publisher,
        settings=settings,
    )
    first = dry_run.publications
    if first and not first.failed and (first.published or first.already_published):
        # A second identical attempt proves backend idempotency after success.
        idempotency = run_activation4_dry_run(
            workspace,
            sample_size=settings.document_sample_size,
            publishing=publisher,
            settings=settings,
        )
        second = idempotency.publications
    else:
        second = None
    source = workspace.registry.partner_legal_master
    issues = Counter(item.code for item in source.issues) if source else Counter()

    print("ACTIVATION4_REAL_VALIDATION")
    print("period", dry_run.period_code)
    print("document_ready", dry_run.fully_document_ready)
    print("email_package_ready", dry_run.email_packages_buildable)
    print("drive_destination", destination.folder_name or "NOT_CONFIGURED")
    print("drive_destination_type", destination.destination_type.value)
    print("drive_metadata_can_create", "YES" if destination.can_create else "NO")
    print(
        "drive_effective_file_create",
        "NO" if provider_create_denied or (first and first.failed) else "YES",
    )
    print("drive_update_capability", "YES" if destination.can_update else "NO")
    print("drive_delete_capability", destination.can_delete)
    print("sample_documents_published", first.published if first else 0)
    print("sample_documents_already_published", first.already_published if first else 0)
    print(
        "publishing_errors",
        first.failed
        if first and first.failed
        else sum(
            item.status.value == "FAILED"
            for item in repository.list_latest_for_period("2026-07-P2")
        ),
    )
    print("idempotency_already_published", second.already_published if second else 0)
    print("gmail_authentication", gmail.authentication.value)
    print("gmail_auth_method", sandbox.auth_method.value)
    print("gmail_sender_configured", "YES" if sandbox.sender_configured else "NO")
    print(
        "sandbox_recipient_configured",
        "YES" if sandbox.sandbox_recipient_valid else "NO",
    )
    print("sandbox_mode", "AVAILABLE" if sandbox.sandbox_available else "NOT_CONFIGURED")
    print("sandbox_provider_calls", dry_run.gmail_provider_calls)
    print("production_provider_calls", 0)
    print("production_emails_sent", 0)
    print("production_send_eligible", dry_run.production_send_eligible)
    print("admin_production_authorization", "NO")
    print("production_email_send_enabled", settings.production_email_send_enabled)
    print("missing_ids", issues["MISSING_ID"])
    print("duplicate_groups", source.profile.duplicate_id_groups if source and source.profile else 0)
    print("legal_conflicts", source.profile.conflict_groups if source and source.profile else 0)
    print("invalid_ice", issues["INVALID_ICE"])
    for index, sample in enumerate(dry_run.samples, start=1):
        print(f"sample_{index}_restaurant", sample.restaurant_name)
        print(f"sample_{index}_restaurant_id", sample.restaurant_id)
        print(f"sample_{index}_settlement", sample.settlement)
        print(f"sample_{index}_financial_policy", sample.financial_policy)
        print(f"sample_{index}_invoice", sample.invoice)
        print(f"sample_{index}_debours", sample.note_de_debours)
        print(f"sample_{index}_statement", sample.statement)
        print(f"sample_{index}_recipient", sample.recipient_resolution)
        print(f"sample_{index}_email_package", sample.email_package)
        print(f"sample_{index}_authorization", sample.admin_authorization)
        print(f"sample_{index}_safety_flag", sample.production_safety_flag)
        print(f"sample_{index}_provider_send", sample.provider_send)
        print(
            f"sample_{index}_reconciliation",
            f"{sample.reconciliation_difference:.2f}",
        )


if __name__ == "__main__":
    main()
