"""LEGACY / DEPRECATED / NOT USED by the active CashCo V2 runtime."""

from __future__ import annotations

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError
from src.ingestion.admin_earnings import AdminEarningsIngestionService
from src.ingestion.admin_earnings_models import IngestionIssue
from src.ingestion.payment_scope import (
    PaymentScopeEligibilityService,
    PaymentScopeIngestionService,
)
from src.ingestion.payment_scope_models import EligibilityResult
from src.ingestion.payment_scope_registry import PaymentScopeSnapshotRegistry
from src.ingestion.registry import SourceManifestRegistry
from src.ingestion.source_discovery import SourceDiscoveryService
from src.models.enums import AuditLevel, ConnectionState, IngestionStatus, SourceStatus


def run_configured_eligibility(
    period_id: str, settings: Settings | None = None
) -> EligibilityResult:
    """Explicit Phase 4 orchestration; never called automatically during rendering."""
    settings = settings or get_settings()
    if settings.google_auth_mode in {"NOT_CONFIGURED", "MOCK"}:
        return _blocked(
            period_id,
            "DRIVE_NOT_CONNECTED",
            "A connected Google Drive configuration is required.",
        )
    try:
        drive = GoogleDriveService(build_google_credentials(settings))
        source_registry = SourceManifestRegistry(settings.source_registry_path)
        discovery = SourceDiscoveryService(drive, settings, source_registry).discover(
            selected_period=period_id
        )
        if discovery.connection_state != ConnectionState.CONNECTED:
            return _blocked(
                period_id,
                "SOURCE_DISCOVERY_FAILED",
                "Source discovery did not complete successfully.",
            )
        candidates = tuple(
            item
            for item in discovery.payment_scope_files
            if item.period_id == period_id and item.source_status == SourceStatus.FOUND
        )
        if len(candidates) != 1:
            return _blocked(
                period_id,
                "PAYMENT_SCOPE_NOT_UNIQUE",
                "Exactly one unambiguous Payment Scope file is required for the selected period.",
            )
        admin_result = AdminEarningsIngestionService(drive, settings).ingest(
            tuple(item.file for item in discovery.admin_earnings_files)
        )
        scope_result = PaymentScopeIngestionService(drive, settings).ingest(
            candidates[0].file, period_id
        )
        if scope_result.snapshot is not None:
            stored = PaymentScopeSnapshotRegistry(
                settings.payment_scope_registry_path
            ).save(scope_result.snapshot)
            scope_result = scope_result.model_copy(update={"snapshot": stored})
        return PaymentScopeEligibilityService().apply(admin_result, scope_result)
    except GoogleIntegrationError:
        return _blocked(
            period_id,
            "DRIVE_READ_FAILED",
            "Eligibility sources could not be read. Check credentials and Drive permissions.",
        )


def _blocked(period_id: str, code: str, message: str) -> EligibilityResult:
    return EligibilityResult(
        status=IngestionStatus.BLOCKED,
        period_id=period_id,
        issues=(IngestionIssue(level=AuditLevel.BLOCKING, code=code, message=message),),
    )
