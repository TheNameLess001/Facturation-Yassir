from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import PurePath

from src.config import Settings
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import (
    SUPPORTED_SOURCE_MIME_TYPES,
    AccessLevel,
    DriveAccessResult,
)
from src.ingestion.admin_earnings_filename import parse_admin_earnings_filename
from src.ingestion.phase2_models import (
    AdminEarningsSourceFile,
    IgnoredAdminFile,
    IgnoredFileReason,
    Phase2DiscoveryResult,
    ReadinessState,
    SourceHealth,
)
from src.ingestion.registry import SourceManifestRegistry
from src.models.enums import ConnectionState, HealthState, SourceType

LOGGER = logging.getLogger(__name__)
CSV_MIME_TYPES = frozenset({"text/csv", "application/csv"})
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
SUPPORTED_ADMIN_MIME_TYPES = CSV_MIME_TYPES | {XLSX_MIME_TYPE, GOOGLE_SHEETS_MIME_TYPE}


class Phase2SourceDiscoveryService:
    """Metadata-only source discovery. It never downloads or mutates Drive content."""

    def __init__(
        self,
        drive: ReadOnlyDriveService,
        settings: Settings,
        registry: SourceManifestRegistry,
    ) -> None:
        self.drive = drive
        self.settings = settings
        self.registry = registry

    def discover(self) -> Phase2DiscoveryResult:
        checked_at = datetime.now(UTC)
        access = self._check_locations()
        admin_access = self._by_location(access, "Admin Earnings")
        valid: tuple[AdminEarningsSourceFile, ...] = ()
        ignored: tuple[IgnoredAdminFile, ...] = ()
        missing_count = 0
        if admin_access.readable and self.settings.admin_earnings_folder_id:
            files = self.drive.list_files(self.settings.admin_earnings_folder_id)
            valid, ignored = self._inventory(files, checked_at)
            missing_count = len(
                self.registry.mark_missing(
                    SourceType.ADMIN_EARNINGS,
                    {item.file_id for item in valid},
                    checked_at=checked_at,
                )
            )
            LOGGER.info(
                "admin_earnings_discovery",
                extra={"total": len(files), "valid": len(valid), "ignored": len(ignored)},
            )
        elif self.settings.admin_earnings_folder_id:
            self.registry.mark_inaccessible(
                SourceType.ADMIN_EARNINGS, checked_at=checked_at
            )

        rst_access = self._by_location(access, "RST List")
        invoice_scope_access = self._by_location(access, "Invoice Scope")
        rst = rst_access.object if self._supported_file(rst_access) else None
        invoice_scope = (
            invoice_scope_access.object
            if self._supported_file(invoice_scope_access)
            else None
        )
        health = self._health(
            access, valid, missing_count, rst is not None, invoice_scope is not None
        )
        return Phase2DiscoveryResult(
            connection_state=ConnectionState.CONNECTED,
            valid_admin_files=valid,
            ignored_admin_files=ignored,
            missing_admin_files=missing_count,
            rst_list=rst,
            invoice_scope=invoice_scope,
            access=access,
            health=health,
            last_checked_at=checked_at,
        )

    def _check_locations(self) -> tuple[DriveAccessResult, ...]:
        locations = (
            ("Admin Earnings", self.settings.admin_earnings_folder_id, True, False),
            ("Invoice Scope", self.settings.invoice_scope_file_id, False, False),
            ("RST List", self.settings.rst_list_file_id, False, False),
            ("Config", self.settings.config_folder_id, True, True),
            ("Processed", self.settings.processed_folder_id, True, True),
            ("Partners", self.settings.partners_folder_id, True, True),
            ("Documents", self.settings.documents_folder_id, True, True),
            ("Audit", self.settings.audit_folder_id, True, True),
        )
        results = tuple(
            self.drive.check_access(
                object_id, location=location, folder=folder, require_write=require_write
            )
            for location, object_id, folder, require_write in locations
        )
        LOGGER.info(
            "drive_locations_checked",
            extra={"locations": len(results), "readable": sum(x.readable for x in results)},
        )
        return results

    def _inventory(self, files, checked_at):
        valid: list[AdminEarningsSourceFile] = []
        ignored: list[IgnoredAdminFile] = []
        for file in files:
            parsed = parse_admin_earnings_filename(file.name)
            if parsed is None:
                ignored.append(
                    IgnoredAdminFile(
                        file_id=file.file_id,
                        filename=file.name,
                        reason=self._ignored_reason(file.name),
                    )
                )
                continue
            if file.mime_type not in SUPPORTED_ADMIN_MIME_TYPES:
                ignored.append(
                    IgnoredAdminFile(
                        file_id=file.file_id,
                        filename=file.name,
                        reason=IgnoredFileReason.UNSUPPORTED_CONTENT_TYPE,
                    )
                )
                continue
            state = self.registry.register(
                SourceType.ADMIN_EARNINGS, file, checked_at=checked_at
            )
            valid.append(
                AdminEarningsSourceFile(
                    file_id=file.file_id,
                    filename=file.name,
                    week_number=parsed.week,
                    year=parsed.year,
                    extension=parsed.extension,
                    mime_type=file.mime_type,
                    modified_at=file.modified_time,
                    size=file.size,
                    checksum=file.md5_checksum,
                    change_state=state,
                    health=HealthState.WARNING
                    if state.name in {"MODIFIED", "INACCESSIBLE"}
                    else HealthState.HEALTHY,
                )
            )
        return tuple(sorted(valid, key=lambda x: (x.year, x.week_number, x.filename))), tuple(ignored)

    @staticmethod
    def _ignored_reason(filename: str) -> IgnoredFileReason:
        stem = PurePath(filename).stem
        if stem.casefold().startswith("data week "):
            return IgnoredFileReason.MALFORMED_ADMIN_FILENAME
        return IgnoredFileReason.INVALID_FILENAME

    @staticmethod
    def _supported_file(access: DriveAccessResult) -> bool:
        return bool(
            access.readable
            and access.object
            and (
                access.object.mime_type in SUPPORTED_SOURCE_MIME_TYPES
                or PurePath(access.object.name).suffix.casefold() in {".csv", ".xlsx"}
            )
        )

    @staticmethod
    def _by_location(access, location):
        return next(item for item in access if item.location == location)

    @staticmethod
    def _health(access, valid, missing_count, rst_ok, invoice_scope_ok) -> SourceHealth:
        admin_access = next(x for x in access if x.location == "Admin Earnings")
        workspace_items = [x for x in access if x.location in {"Config", "Processed", "Partners", "Documents", "Audit"}]
        admin = HealthState.BLOCKING if not admin_access.readable or not valid else (HealthState.WARNING if missing_count else HealthState.HEALTHY)
        rst = HealthState.HEALTHY if rst_ok else HealthState.BLOCKING
        invoice_scope = (
            HealthState.HEALTHY if invoice_scope_ok else HealthState.BLOCKING
        )
        workspace = HealthState.HEALTHY if all(x.access == AccessLevel.READ_WRITE for x in workspace_items) else HealthState.BLOCKING
        states = (admin, invoice_scope, rst, workspace)
        overall = ReadinessState.READY_FOR_INGESTION if all(x == HealthState.HEALTHY for x in states) else ReadinessState.BLOCKING
        return SourceHealth(
            google_connection=HealthState.HEALTHY,
            admin_earnings=admin,
            invoice_scope=invoice_scope,
            rst_list=rst,
            workspace=workspace,
            overall=overall,
        )


# Phase 3 contract: identical normalized financial records for one Order ID are
# retained once. Conflicting financial values are CONFLICTING_DUPLICATE,
# BLOCKING, and routed to REVIEW_QUEUE; no last/latest record wins silently.
