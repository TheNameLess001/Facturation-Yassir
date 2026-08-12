"""LEGACY source discovery retained for historical compatibility only.

Active CashCo V2 source discovery is ``phase2_discovery`` and uses Invoice Scope.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime

from src.config import Settings
from src.google.exceptions import GoogleIntegrationError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import SUPPORTED_SOURCE_MIME_TYPES, DriveFile
from src.ingestion.models import DiscoveredSource, SourceDiscoveryResult, SourceIssue
from src.ingestion.registry import SourceManifestRegistry
from src.models.enums import (
    ChangeState,
    ConnectionState,
    HealthState,
    SourceStatus,
    SourceType,
)

LOGGER = logging.getLogger(__name__)
PERIOD_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})[-_ ](0[1-9]|1[0-2])[-_ ]?(P[12])(?!\w)", re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"^20\d{2}$")
SUPPORTED_EXTENSIONS = (".csv", ".xlsx")


def is_supported_source(file: DriveFile) -> bool:
    return (
        file.mime_type in SUPPORTED_SOURCE_MIME_TYPES
        or file.name.casefold().endswith(SUPPORTED_EXTENSIONS)
    )


def period_from_text(value: str) -> str | None:
    match = PERIOD_PATTERN.search(value)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3).upper()}"


class SourceDiscoveryService:
    """Discovers metadata only; never downloads or mutates source files."""

    def __init__(
        self,
        drive: ReadOnlyDriveService,
        settings: Settings,
        registry: SourceManifestRegistry,
    ) -> None:
        self.drive = drive
        self.settings = settings
        self.registry = registry

    def discover(self, *, selected_period: str | None = None) -> SourceDiscoveryResult:
        checked_at = datetime.now(UTC)
        warnings: list[SourceIssue] = []
        blocking: list[SourceIssue] = []
        try:
            if not self.settings.drive_root_folder_id:
                return self.not_configured_result(checked_at)
            connection = self.drive.test_connection(self.settings.drive_root_folder_id)
            admin = self._discover_admin_earnings(checked_at, warnings, blocking)
            payment = self._discover_payment_scope(
                checked_at, selected_period, warnings, blocking
            )
            rst = self._discover_rst_list(checked_at, warnings, blocking)
            return SourceDiscoveryResult(
                connection_state=ConnectionState.CONNECTED,
                root_name=connection.root_name,
                admin_earnings_files=admin,
                payment_scope_files=payment,
                rst_list_file=rst,
                warnings=tuple(warnings),
                blocking_errors=tuple(blocking),
                last_checked_at=checked_at,
            )
        except GoogleIntegrationError:
            LOGGER.exception("source_discovery_connection_failed")
            issue = SourceIssue(
                source_type=SourceType.ADMIN_EARNINGS,
                health=HealthState.BLOCKING,
                message="Google Drive connection failed. Check credentials and Drive permissions.",
            )
            return SourceDiscoveryResult(
                connection_state=ConnectionState.ERROR,
                blocking_errors=(issue,),
                last_checked_at=checked_at,
            )

    @staticmethod
    def not_configured_result(
        checked_at: datetime | None = None,
    ) -> SourceDiscoveryResult:
        checked_at = checked_at or datetime.now(UTC)
        issues = tuple(
            SourceIssue(
                source_type=source_type,
                health=HealthState.UNKNOWN,
                message="Google Drive is not configured.",
            )
            for source_type in SourceType
        )
        return SourceDiscoveryResult(
            connection_state=ConnectionState.NOT_CONFIGURED,
            warnings=issues,
            last_checked_at=checked_at,
        )

    def _discover_admin_earnings(
        self,
        checked_at: datetime,
        warnings: list[SourceIssue],
        blocking: list[SourceIssue],
    ) -> tuple[DiscoveredSource, ...]:
        folder_id = self.settings.admin_earnings_folder_id
        if not folder_id:
            blocking.append(
                self._issue(
                    SourceType.ADMIN_EARNINGS,
                    "Admin Earnings folder is not configured.",
                )
            )
            return ()
        files = self.drive.list_files(folder_id)
        supported = tuple(file for file in files if is_supported_source(file))
        invalid = len(files) - len(supported)
        if invalid:
            warnings.append(
                self._issue(
                    SourceType.ADMIN_EARNINGS,
                    f"{invalid} unsupported Admin Earnings file(s) were ignored.",
                    HealthState.WARNING,
                )
            )
        if not supported:
            blocking.append(
                self._issue(
                    SourceType.ADMIN_EARNINGS,
                    "No supported Admin Earnings files found.",
                )
            )
        result = self._register(SourceType.ADMIN_EARNINGS, supported, checked_at)
        missing = self.registry.mark_missing(
            SourceType.ADMIN_EARNINGS,
            {file.file_id for file in supported},
            checked_at=checked_at,
        )
        if missing:
            warnings.append(
                self._issue(
                    SourceType.ADMIN_EARNINGS,
                    f"{len(missing)} previously registered Admin Earnings file(s) are now missing.",
                    HealthState.WARNING,
                )
            )
        if any(item.change_state == ChangeState.MODIFIED for item in result):
            warnings.append(
                self._issue(
                    SourceType.ADMIN_EARNINGS,
                    "An immutable Admin Earnings source changed after first discovery.",
                    HealthState.WARNING,
                )
            )
        LOGGER.info("admin_earnings_scan", extra={"files_found": len(result)})
        return result

    def _discover_payment_scope(
        self,
        checked_at: datetime,
        selected_period: str | None,
        warnings: list[SourceIssue],
        blocking: list[SourceIssue],
    ) -> tuple[DiscoveredSource, ...]:
        folder_id = self.settings.payment_scope_folder_id
        if not folder_id:
            blocking.append(
                self._issue(
                    SourceType.PAYMENT_SCOPE, "Payment Scope folder is not configured."
                )
            )
            return ()
        candidates: list[tuple[DriveFile, str | None]] = []
        candidates.extend((file, None) for file in self.drive.list_files(folder_id))
        for folder in self.drive.list_child_folders(folder_id):
            if YEAR_PATTERN.fullmatch(folder.name):
                for period_folder in self.drive.list_child_folders(folder.file_id):
                    folder_period = period_from_text(period_folder.name)
                    candidates.extend(
                        (file, folder_period)
                        for file in self.drive.list_files(period_folder.file_id)
                    )
            else:
                folder_period = period_from_text(folder.name)
                candidates.extend(
                    (file, folder_period)
                    for file in self.drive.list_files(folder.file_id)
                )

        explicit_by_file = {
            file_id: period
            for period, file_id in self.settings.payment_scope_period_map.items()
        }
        mapped: list[tuple[DriveFile, str | None]] = []
        for file, folder_period in candidates:
            period = (
                explicit_by_file.get(file.file_id)
                or folder_period
                or period_from_text(file.name)
            )
            mapped.append((file, period))

        grouped: dict[str, list[DriveFile]] = defaultdict(list)
        sources: list[DiscoveredSource] = []
        for file, period in mapped:
            if not is_supported_source(file):
                sources.append(
                    self._source(
                        SourceType.PAYMENT_SCOPE,
                        file,
                        period,
                        SourceStatus.INVALID_FILE_TYPE,
                        HealthState.WARNING,
                        "Only CSV and XLSX sources are supported.",
                        checked_at,
                    )
                )
            elif period is None:
                sources.append(
                    self._source(
                        SourceType.PAYMENT_SCOPE,
                        file,
                        None,
                        SourceStatus.MANUAL_MAPPING_REQUIRED,
                        HealthState.WARNING,
                        "Period could not be determined confidently.",
                        checked_at,
                    )
                )
                warnings.append(
                    self._issue(
                        SourceType.PAYMENT_SCOPE,
                        f"Manual period mapping required for {file.name}.",
                        HealthState.WARNING,
                    )
                )
            else:
                grouped[period].append(file)

        for period, files in grouped.items():
            ambiguous = len(files) > 1
            if ambiguous:
                blocking.append(
                    self._issue(
                        SourceType.PAYMENT_SCOPE,
                        f"Multiple Payment Scope files map to {period}.",
                        period_id=period,
                    )
                )
            for file in files:
                sources.append(
                    self._source(
                        SourceType.PAYMENT_SCOPE,
                        file,
                        period,
                        SourceStatus.AMBIGUOUS if ambiguous else SourceStatus.FOUND,
                        HealthState.BLOCKING if ambiguous else HealthState.HEALTHY,
                        "Multiple candidates require manual resolution."
                        if ambiguous
                        else None,
                        checked_at,
                    )
                )
        if selected_period and not grouped.get(selected_period):
            blocking.append(
                self._issue(
                    SourceType.PAYMENT_SCOPE,
                    f"Payment Scope is missing for {selected_period}.",
                    period_id=selected_period,
                )
            )
        seen = {item.file.file_id for item in sources}
        self.registry.mark_missing(
            SourceType.PAYMENT_SCOPE, seen, checked_at=checked_at
        )
        LOGGER.info(
            "payment_scope_scan",
            extra={"files_found": len(sources), "period": selected_period},
        )
        return tuple(
            sorted(
                sources,
                key=lambda item: (item.period_id or "", item.file.name.casefold()),
            )
        )

    def _discover_rst_list(
        self,
        checked_at: datetime,
        warnings: list[SourceIssue],
        blocking: list[SourceIssue],
    ) -> DiscoveredSource | None:
        candidates: tuple[DriveFile, ...]
        if self.settings.rst_list_file_id:
            try:
                candidates = (
                    self.drive.get_file_metadata(self.settings.rst_list_file_id),
                )
            except GoogleIntegrationError:
                blocking.append(
                    self._issue(
                        SourceType.RST_LIST, "Configured RST List is inaccessible."
                    )
                )
                return None
        elif self.settings.rst_list_folder_id:
            candidates = tuple(
                file
                for file in self.drive.find_files(
                    self.settings.rst_list_folder_id, name_contains="rst"
                )
                if is_supported_source(file)
            )
        else:
            blocking.append(
                self._issue(SourceType.RST_LIST, "RST List file is not configured.")
            )
            return None

        if not candidates:
            blocking.append(self._issue(SourceType.RST_LIST, "RST List is missing."))
            return None
        if len(candidates) > 1:
            blocking.append(
                self._issue(
                    SourceType.RST_LIST, "Multiple RST List candidates were found."
                )
            )
            return None
        file = candidates[0]
        if not is_supported_source(file):
            blocking.append(
                self._issue(SourceType.RST_LIST, "RST List has an invalid file type.")
            )
            return None
        result = self._source(
            SourceType.RST_LIST,
            file,
            None,
            SourceStatus.FOUND,
            HealthState.HEALTHY,
            None,
            checked_at,
        )
        self.registry.mark_missing(
            SourceType.RST_LIST, {file.file_id}, checked_at=checked_at
        )
        LOGGER.info("rst_list_discovery", extra={"status": "FOUND"})
        return result

    def _register(
        self,
        source_type: SourceType,
        files: tuple[DriveFile, ...],
        checked_at: datetime,
    ) -> tuple[DiscoveredSource, ...]:
        return tuple(
            self._source(
                source_type,
                file,
                None,
                SourceStatus.FOUND,
                HealthState.HEALTHY,
                None,
                checked_at,
            )
            for file in sorted(
                files, key=lambda item: (item.modified_time, item.name, item.file_id)
            )
        )

    def _source(
        self,
        source_type: SourceType,
        file: DriveFile,
        period_id: str | None,
        status: SourceStatus,
        health: HealthState,
        message: str | None,
        checked_at: datetime,
    ) -> DiscoveredSource:
        change = self.registry.register(
            source_type, file, period_id=period_id, checked_at=checked_at
        )
        LOGGER.info(
            "source_discovered",
            extra={"source_type": source_type.value, "change_state": change.value},
        )
        return DiscoveredSource(
            source_type=source_type,
            file=file,
            period_id=period_id,
            source_status=status,
            change_state=change,
            health=health,
            message=message,
        )

    @staticmethod
    def _issue(
        source_type: SourceType,
        message: str,
        health: HealthState = HealthState.BLOCKING,
        period_id: str | None = None,
    ) -> SourceIssue:
        return SourceIssue(
            source_type=source_type,
            health=health,
            message=message,
            period_id=period_id,
        )
