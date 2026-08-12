from __future__ import annotations

from datetime import UTC, datetime

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleAuthenticationError
from src.ingestion.models import SourceDiscoveryResult, SourceIssue
from src.ingestion.registry import SourceManifestRegistry
from src.ingestion.source_discovery import SourceDiscoveryService
from src.models.enums import ConnectionState, HealthState, SourceType


def discover_configured_sources(
    selected_period: str = "2026-08-P1", settings: Settings | None = None
) -> SourceDiscoveryResult:
    settings = settings or get_settings()
    if settings.google_auth_mode == "MOCK":
        return SourceDiscoveryResult(
            connection_state=ConnectionState.MOCK,
            warnings=tuple(
                SourceIssue(
                    source_type=source,
                    health=HealthState.UNKNOWN,
                    message="Mock mode: no Google Drive request was made.",
                )
                for source in SourceType
            ),
            last_checked_at=datetime.now(UTC),
        )
    if settings.google_auth_mode == "NOT_CONFIGURED":
        return SourceDiscoveryService.not_configured_result()
    try:
        credentials = build_google_credentials(settings)
    except GoogleAuthenticationError:
        return SourceDiscoveryResult(
            connection_state=ConnectionState.ERROR,
            blocking_errors=(
                SourceIssue(
                    source_type=SourceType.ADMIN_EARNINGS,
                    health=HealthState.BLOCKING,
                    message="Google Drive authentication failed. Check application configuration.",
                ),
            ),
            last_checked_at=datetime.now(UTC),
        )
    drive = GoogleDriveService(credentials)
    registry = SourceManifestRegistry(settings.source_registry_path)
    return SourceDiscoveryService(drive, settings, registry).discover(
        selected_period=selected_period
    )
