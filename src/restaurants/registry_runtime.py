from __future__ import annotations

import io
import logging

import pandas as pd

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import SourceDiscoveryError
from src.google.interfaces import ReadOnlyDriveService
from src.ingestion.admin_earnings_normalizer import normalize_identifier
from src.restaurants.registry_models import RestaurantRegistryResult
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader

LOGGER = logging.getLogger(__name__)
CANONICAL_ORDERS_NAME = "canonical_orders.parquet"


def run_restaurant_registry(
    settings: Settings | None = None,
    drive: ReadOnlyDriveService | None = None,
) -> RestaurantRegistryResult:
    settings = settings or get_settings()
    if not settings.invoice_scope_file_id:
        raise SourceDiscoveryError("Invoice Scope is not configured.")
    if not settings.rst_list_file_id:
        raise SourceDiscoveryError("RST List is not configured.")
    active_drive = drive or GoogleDriveService(build_google_credentials(settings))
    reader = RestaurantSourceReader(active_drive)
    invoice = reader.read_invoice_scope(
        settings.invoice_scope_file_id,
        active_worksheet=settings.invoice_scope_worksheet,
    )
    rst = reader.read_rst(settings.rst_list_file_id)
    order_counts = _load_canonical_order_counts(active_drive, settings)
    result = RestaurantRegistryBuilder().build(
        invoice.frame,
        rst.frame,
        invoice_scope_profile=invoice.profile,
        rst_profile=rst.profile,
        invoice_scope_column_map=settings.invoice_scope_column_map,
        rst_column_map=settings.rst_column_map,
        alias_map=settings.invoice_scope_alias_map,
        canonical_order_counts=order_counts,
    )
    LOGGER.info(
        "restaurant_registry_built",
        extra={
            "scope_rows": result.scope_rows,
            "registered": len(result.restaurants),
            "blocking": sum(item.severity == "BLOCKING" for item in result.issues),
        },
    )
    return result


def _load_canonical_order_counts(
    drive: ReadOnlyDriveService, settings: Settings
) -> dict[str, int]:
    if not settings.processed_folder_id:
        return {}
    matches = tuple(
        item
        for item in drive.list_files(settings.processed_folder_id)
        if item.name == CANONICAL_ORDERS_NAME
    )
    if len(matches) != 1:
        raise SourceDiscoveryError(
            "Exactly one canonical_orders.parquet artifact is required for diagnostics."
        )
    frame = pd.read_parquet(io.BytesIO(drive.download_file(matches[0].file_id)))
    if "restaurant_id" not in frame.columns:
        return {}
    normalized = frame["restaurant_id"].map(normalize_identifier).dropna()
    return {str(key): int(value) for key, value in normalized.value_counts().items()}
