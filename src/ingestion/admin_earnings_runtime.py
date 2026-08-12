from __future__ import annotations

from datetime import UTC, datetime

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError
from src.ingestion.admin_earnings import AdminEarningsIngestionService
from src.ingestion.admin_earnings_models import (
    AdminEarningsIngestionResult,
    IngestionIssue,
)
from src.ingestion.registry import SourceManifestRegistry
from src.ingestion.source_discovery import is_supported_source
from src.models.enums import AuditLevel, IngestionStatus, SourceType


def run_configured_admin_earnings_ingestion(
    settings: Settings | None = None,
) -> AdminEarningsIngestionResult:
    """Explicit runtime entry point. Nothing invokes it on a schedule or page render."""
    settings = settings or get_settings()
    started_at = datetime.now(UTC)
    if settings.google_auth_mode in {"NOT_CONFIGURED", "MOCK"}:
        return _blocked(
            started_at,
            "DRIVE_NOT_CONNECTED",
            "A connected Google Drive configuration is required to validate Admin Earnings.",
        )
    if not settings.admin_earnings_folder_id:
        return _blocked(
            started_at,
            "ADMIN_EARNINGS_FOLDER_NOT_CONFIGURED",
            "The Admin Earnings folder is not configured.",
        )
    try:
        drive = GoogleDriveService(build_google_credentials(settings))
        files = tuple(
            file
            for file in drive.list_files(settings.admin_earnings_folder_id)
            if is_supported_source(file)
        )
        registry = SourceManifestRegistry(settings.source_registry_path)
        checked_at = datetime.now(UTC)
        for file in files:
            registry.register(SourceType.ADMIN_EARNINGS, file, checked_at=checked_at)
        result = AdminEarningsIngestionService(drive, settings).ingest(files)
        for file_result in result.file_results:
            registry.record_ingestion(
                file_result.source_file_id,
                rows=file_result.rows_read,
                unique_order_ids=file_result.unique_order_ids,
                duplicates=file_result.duplicate_rows,
                imported_at=result.completed_at,
                import_result=(
                    "BLOCKED"
                    if any(
                        issue.level == AuditLevel.BLOCKING
                        for issue in file_result.issues
                    )
                    else "VALIDATED"
                ),
            )
        return result
    except GoogleIntegrationError:
        return _blocked(
            started_at,
            "DRIVE_READ_FAILED",
            "Admin Earnings could not be read. Check credentials and Drive permissions.",
        )


def _blocked(
    started_at: datetime, code: str, message: str
) -> AdminEarningsIngestionResult:
    return AdminEarningsIngestionResult(
        status=IngestionStatus.BLOCKED,
        issues=(IngestionIssue(level=AuditLevel.BLOCKING, code=code, message=message),),
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
