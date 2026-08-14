from __future__ import annotations

from datetime import UTC, datetime

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials, parse_service_account_json
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import (
    GoogleAuthenticationError,
    GoogleConfigurationError,
    GoogleIntegrationError,
)
from src.ingestion.phase2_discovery import Phase2SourceDiscoveryService
from src.ingestion.phase2_models import (
    Phase2DiscoveryResult,
    ReadinessState,
    SourceHealth,
)
from src.ingestion.registry import SourceManifestRegistry
from src.models.enums import ConnectionState, HealthState


def _unavailable_result(
    state: ConnectionState, message: str, readiness: ReadinessState
) -> Phase2DiscoveryResult:
    health_state = (
        HealthState.UNKNOWN
        if state in {ConnectionState.NOT_CONFIGURED, ConnectionState.CONFIG_ERROR}
        else HealthState.BLOCKING
    )
    return Phase2DiscoveryResult(
        connection_state=state,
        health=SourceHealth(
            google_connection=health_state,
            admin_earnings=health_state,
            invoice_scope=health_state,
            partner_legal_master=health_state,
            rst_list=health_state,
            workspace=health_state,
            overall=readiness,
        ),
        last_checked_at=datetime.now(UTC),
        message=message,
    )


def discover_phase2_sources(
    settings: Settings | None = None, drive: GoogleDriveService | None = None
) -> Phase2DiscoveryResult:
    settings = settings or get_settings()
    if not settings.google_credentials_configured:
        return _unavailable_result(
            ConnectionState.NOT_CONFIGURED,
            "Google credentials are not configured.",
            ReadinessState.NOT_CONFIGURED,
        )
    try:
        parse_service_account_json(settings)
        credentials = None if drive else build_google_credentials(settings)
    except GoogleConfigurationError:
        return _unavailable_result(
            ConnectionState.CONFIG_ERROR,
            "Google credential configuration is malformed.",
            ReadinessState.BLOCKING,
        )
    try:
        drive = drive or GoogleDriveService(credentials)
        registry = SourceManifestRegistry(settings.source_registry_path)
        return Phase2SourceDiscoveryService(drive, settings, registry).discover()
    except GoogleAuthenticationError:
        return _unavailable_result(
            ConnectionState.AUTH_ERROR,
            "Google authentication failed. Verify the service-account key is active.",
            ReadinessState.AUTH_ERROR,
        )
    except GoogleIntegrationError:
        return _unavailable_result(
            ConnectionState.ERROR,
            "Google Drive could not be reached. Try refreshing later.",
            ReadinessState.BLOCKING,
        )
