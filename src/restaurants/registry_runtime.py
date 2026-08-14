from __future__ import annotations

import io
import logging
from collections import Counter, defaultdict

import pandas as pd

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import SourceDiscoveryError
from src.google.interfaces import ReadOnlyDriveService
from src.ingestion.admin_earnings_normalizer import normalize_identifier
from src.restaurants.legal_master import (
    PartnerLegalMasterCache,
    PartnerLegalMasterSource,
    PartnerLegalRegistryEnricher,
)
from src.restaurants.registry_models import (
    LegalMasterSyncStatus,
    PartnerLegalMasterSnapshot,
    RestaurantRegistryResult,
)
from src.restaurants.scope_registry import (
    RST_ALIASES,
    RestaurantRegistryBuilder,
    resolve_columns,
)
from src.restaurants.source_reader import RestaurantSourceReader

LOGGER = logging.getLogger(__name__)
CANONICAL_ORDERS_NAME = "canonical_orders.parquet"
LEGAL_MASTER_CACHE = PartnerLegalMasterCache()


def run_restaurant_registry(
    settings: Settings | None = None,
    drive: ReadOnlyDriveService | None = None,
    canonical_orders_frame: pd.DataFrame | None = None,
    force_legal_master_refresh: bool = False,
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
    order_counts, order_names = (
        _canonical_order_diagnostics(canonical_orders_frame)
        if canonical_orders_frame is not None
        else _load_canonical_order_diagnostics(active_drive, settings)
    )
    result = RestaurantRegistryBuilder().build(
        invoice.frame,
        rst.frame,
        invoice_scope_profile=invoice.profile,
        rst_profile=rst.profile,
        invoice_scope_column_map=settings.invoice_scope_column_map,
        rst_column_map=settings.rst_column_map,
        canonical_order_counts=order_counts,
        canonical_order_names=order_names,
    )
    legal_snapshot = load_partner_legal_master(
        settings,
        active_drive,
        force=force_legal_master_refresh,
    )
    rst_mapping = resolve_columns(
        rst.frame.columns,
        RST_ALIASES,
        settings.rst_column_map,
    )
    rst_id_column = rst_mapping.get("restaurant_id")
    rst_ids = (
        {
            normalized
            for value in rst.frame[rst_id_column]
            if (normalized := normalize_identifier(value)) is not None
        }
        if rst_id_column
        else set()
    )
    result = PartnerLegalRegistryEnricher().enrich(
        result,
        legal_snapshot,
        rst_ids=rst_ids,
    )
    LOGGER.info(
        "restaurant_registry_built",
        extra={
            "scope_rows": result.scope_rows,
            "registered": len(result.restaurants),
            "blocking": sum(item.severity == "BLOCKING" for item in result.issues),
            "legal_master_status": legal_snapshot.status.value,
            "legal_master_rows": (
                legal_snapshot.profile.row_count if legal_snapshot.profile else 0
            ),
        },
    )
    return result


def load_partner_legal_master(
    settings: Settings,
    drive: ReadOnlyDriveService,
    *,
    force: bool = False,
) -> PartnerLegalMasterSnapshot:
    if not settings.partner_legal_master_file_id:
        return PartnerLegalMasterSnapshot(status=LegalMasterSyncStatus.NOT_CONFIGURED)
    key = (
        f"{settings.partner_legal_master_file_id}:"
        f"{settings.partner_legal_master_worksheet}"
    )
    source = PartnerLegalMasterSource(drive)
    return LEGAL_MASTER_CACHE.load(
        key,
        lambda: source.fetch(
            settings.partner_legal_master_file_id or "",
            settings.partner_legal_master_worksheet,
            column_map=settings.partner_legal_master_column_map,
        ),
        ttl_seconds=settings.partner_legal_master_cache_ttl_seconds,
        force=force,
    )


def expire_partner_legal_master_cache(settings: Settings | None = None) -> None:
    active = settings or get_settings()
    if active.partner_legal_master_file_id:
        LEGAL_MASTER_CACHE.expire(
            f"{active.partner_legal_master_file_id}:"
            f"{active.partner_legal_master_worksheet}"
        )


def _load_canonical_order_diagnostics(
    drive: ReadOnlyDriveService, settings: Settings
) -> tuple[dict[str, int], dict[str, str]]:
    if not settings.processed_folder_id:
        return {}, {}
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
        return {}, {}
    return _canonical_order_diagnostics(frame)


def _canonical_order_diagnostics(
    frame: pd.DataFrame,
) -> tuple[dict[str, int], dict[str, str]]:
    if "restaurant_id" not in frame.columns:
        return {}, {}
    normalized = frame["restaurant_id"].map(normalize_identifier)
    counts = {
        str(key): int(value)
        for key, value in normalized.dropna().value_counts().items()
    }
    if "restaurant_name" not in frame.columns:
        return counts, {}
    observed_names: dict[str, Counter[str]] = defaultdict(Counter)
    for restaurant_id, restaurant_name in zip(
        normalized,
        frame["restaurant_name"],
        strict=True,
    ):
        if restaurant_id is None or pd.isna(restaurant_name):
            continue
        name = str(restaurant_name).strip()
        if name:
            observed_names[str(restaurant_id)][name] += 1
    names = {
        restaurant_id: min(
            frequencies.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )[0]
        for restaurant_id, frequencies in observed_names.items()
    }
    return counts, names
