from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import SourceDiscoveryError
from src.google.interfaces import ReadOnlyDriveService
from src.restaurants.registry_models import RestaurantRegistryResult
from src.restaurants.registry_runtime import run_restaurant_registry
from src.settlement.overrides import FinancialOverrideRepository
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import SettlementSummary
from src.settlement.phase5_service import Phase5SettlementService

CANONICAL_ORDERS_NAME = "canonical_orders.parquet"
INGESTION_ISSUES_NAME = "ingestion_issues.parquet"


@dataclass(frozen=True)
class Phase5Workspace:
    summary: SettlementSummary
    registry: RestaurantRegistryResult


def run_phase5_settlement(
    period_code: str,
    settings: Settings | None = None,
    drive: ReadOnlyDriveService | None = None,
) -> SettlementSummary:
    return load_phase5_workspace(period_code, settings=settings, drive=drive).summary


def load_phase5_workspace(
    period_code: str,
    settings: Settings | None = None,
    drive: ReadOnlyDriveService | None = None,
) -> Phase5Workspace:
    active_settings = settings or get_settings()
    active_drive = drive or GoogleDriveService(
        build_google_credentials(active_settings)
    )
    canonical, ingestion_issues = load_phase5_processed_inputs(
        active_drive,
        active_settings,
    )
    registry = run_restaurant_registry(
        settings=active_settings,
        drive=active_drive,
        canonical_orders_frame=canonical,
    )
    period = SettlementPeriodService(active_settings.timezone).get(period_code)
    overrides = FinancialOverrideRepository(
        active_settings.financial_override_registry_path
    ).list_for_period(period_code)
    summary = Phase5SettlementService().evaluate(
        period,
        canonical,
        registry,
        invalid_financial_issues=ingestion_issues,
        overrides=overrides,
    )
    return Phase5Workspace(summary=summary, registry=registry)


def load_phase5_processed_inputs(
    drive: ReadOnlyDriveService,
    settings: Settings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not settings.processed_folder_id:
        raise SourceDiscoveryError("Processed workspace is not configured.")
    files = drive.list_files(settings.processed_folder_id)
    resolved = {}
    for name in (CANONICAL_ORDERS_NAME, INGESTION_ISSUES_NAME):
        matches = tuple(item for item in files if item.name == name)
        if len(matches) != 1:
            raise SourceDiscoveryError(
                f"Exactly one {name} artifact is required for settlement evaluation."
            )
        resolved[name] = matches[0]
    canonical = pd.read_parquet(
        io.BytesIO(drive.download_file(resolved[CANONICAL_ORDERS_NAME].file_id))
    )
    issues = pd.read_parquet(
        io.BytesIO(drive.download_file(resolved[INGESTION_ISSUES_NAME].file_id))
    )
    return canonical, issues
