"""LEGACY / DEPRECATED / NOT USED by the active CashCo V2 runtime."""

from __future__ import annotations

import hashlib
import io
import logging
import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime

import pandas as pd

from src.config import Settings
from src.google.exceptions import GoogleIntegrationError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import DriveFile
from src.ingestion.admin_earnings_models import (
    AdminEarningsIngestionResult,
    IngestionIssue,
)
from src.ingestion.exceptions import (
    SourceFileTooLargeError,
    SourceParseError,
    UnsupportedSourceFormatError,
)
from src.ingestion.payment_scope_models import (
    EligibilityRecord,
    EligibilityResult,
    PaymentScopeEntry,
    PaymentScopeIngestionResult,
    PaymentScopeSnapshot,
)
from src.models.enums import (
    AuditLevel,
    EligibilityState,
    IngestionStatus,
)

SCOPE_ALIASES = {
    "restaurant_id": frozenset(
        {"restaurant id", "restaurantid", "rst id", "id restaurant", "partner id"}
    ),
    "restaurant_name": frozenset(
        {"restaurant name", "restaurant", "rst name", "nom restaurant", "partner name"}
    ),
}
LOGGER = logging.getLogger(__name__)


def normalize_heading(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


class PaymentScopeIngestionService:
    """Creates a period-specific eligibility snapshot from one read-only source file."""

    def __init__(self, drive: ReadOnlyDriveService, settings: Settings) -> None:
        self.drive = drive
        self.settings = settings

    def ingest(self, file: DriveFile, period_id: str) -> PaymentScopeIngestionResult:
        try:
            max_bytes = self.settings.payment_scope_max_file_mb * 1024 * 1024
            if file.size is not None and file.size > max_bytes:
                raise SourceFileTooLargeError(
                    f"Payment Scope exceeds the configured {self.settings.payment_scope_max_file_mb} MB limit."
                )
            content = self.drive.download_file(file.file_id)
            if len(content) > max_bytes:
                raise SourceFileTooLargeError(
                    f"Payment Scope exceeds the configured {self.settings.payment_scope_max_file_mb} MB limit."
                )
            frame = self._read_frame(file, content)
        except (
            GoogleIntegrationError,
            SourceFileTooLargeError,
            SourceParseError,
            UnsupportedSourceFormatError,
        ) as exc:
            return self._blocked(period_id, file, type(exc).__name__.upper(), str(exc))

        columns = self._resolve_columns(frame)
        if "restaurant_id" not in columns:
            return self._blocked(
                period_id,
                file,
                "MISSING_RESTAURANT_ID",
                "Payment Scope must contain a Restaurant ID column.",
                rows_read=len(frame),
            )

        entries: list[PaymentScopeEntry] = []
        issues: list[IngestionIssue] = []
        for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
            raw_id = row[columns["restaurant_id"]]
            if self._blank(raw_id):
                if all(self._blank(value) for value in row.tolist()):
                    continue
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="MISSING_RESTAURANT_ID",
                        message="Restaurant ID is required for every Payment Scope row.",
                        source_file_id=file.file_id,
                        source_filename=file.name,
                        source_row_number=row_number,
                        field="restaurant_id",
                    )
                )
                continue
            restaurant_id = self._identifier(raw_id)
            restaurant_name = (
                str(row[columns["restaurant_name"]]).strip()
                if "restaurant_name" in columns
                and not self._blank(row[columns["restaurant_name"]])
                else None
            )
            entries.append(
                PaymentScopeEntry(
                    restaurant_id=restaurant_id,
                    restaurant_name=restaurant_name,
                    source_row_number=row_number,
                    source_values=tuple(
                        (str(column), "" if self._blank(value) else str(value))
                        for column, value in row.items()
                    ),
                )
            )

        grouped: dict[str, list[PaymentScopeEntry]] = defaultdict(list)
        for entry in entries:
            grouped[entry.restaurant_id].append(entry)
        duplicates = tuple(
            sorted(key for key, values in grouped.items() if len(values) > 1)
        )
        if duplicates:
            issues.append(
                IngestionIssue(
                    level=AuditLevel.WARNING,
                    code="DUPLICATE_RESTAURANT_ID",
                    message=(
                        f"{len(duplicates)} duplicate Restaurant ID(s) were collapsed; "
                        "membership remains based on Restaurant ID only."
                    ),
                    source_file_id=file.file_id,
                    source_filename=file.name,
                )
            )
        unique_entries = tuple(grouped[key][0] for key in sorted(grouped))
        if not unique_entries:
            issues.append(
                IngestionIssue(
                    level=AuditLevel.BLOCKING,
                    code="EMPTY_PAYMENT_SCOPE",
                    message="Payment Scope contains no usable Restaurant IDs.",
                    source_file_id=file.file_id,
                    source_filename=file.name,
                )
            )
        has_blocker = any(issue.level == AuditLevel.BLOCKING for issue in issues)
        if has_blocker:
            return PaymentScopeIngestionResult(
                status=IngestionStatus.BLOCKED,
                period_id=period_id,
                entries=unique_entries,
                rows_read=len(frame),
                duplicate_restaurant_ids=duplicates,
                issues=tuple(issues),
            )

        content_hash = hashlib.sha256(content).hexdigest()
        snapshot_at = datetime.now(UTC)
        snapshot_key = f"{period_id}|{file.file_id}|{content_hash}".encode()
        snapshot = PaymentScopeSnapshot(
            snapshot_id=hashlib.sha256(snapshot_key).hexdigest(),
            period_id=period_id,
            drive_file_id=file.file_id,
            filename=file.name,
            drive_modified_at=file.modified_time,
            drive_checksum=file.md5_checksum,
            content_hash=content_hash,
            snapshot_at=snapshot_at,
            restaurant_ids=tuple(entry.restaurant_id for entry in unique_entries),
        )
        LOGGER.info(
            "payment_scope_validated",
            extra={
                "period": period_id,
                "restaurant_count": snapshot.restaurant_count,
                "status": (
                    IngestionStatus.COMPLETED_WITH_WARNINGS.value
                    if issues
                    else IngestionStatus.SUCCESS.value
                ),
            },
        )
        return PaymentScopeIngestionResult(
            status=(
                IngestionStatus.COMPLETED_WITH_WARNINGS
                if issues
                else IngestionStatus.SUCCESS
            ),
            period_id=period_id,
            entries=unique_entries,
            snapshot=snapshot,
            rows_read=len(frame),
            duplicate_restaurant_ids=duplicates,
            issues=tuple(issues),
        )

    def _read_frame(self, file: DriveFile, content: bytes) -> pd.DataFrame:
        suffix = file.name.casefold().rsplit(".", maxsplit=1)[-1]
        try:
            if suffix == "csv":
                return pd.read_csv(
                    io.BytesIO(content),
                    dtype=object,
                    keep_default_na=False,
                    encoding=self.settings.payment_scope_csv_encoding,
                    sep=None,
                    engine="python",
                )
            if suffix == "xlsx":
                return pd.read_excel(
                    io.BytesIO(content),
                    dtype=object,
                    keep_default_na=False,
                    engine="openpyxl",
                )
        except Exception as exc:
            raise SourceParseError("Payment Scope could not be parsed safely.") from exc
        raise UnsupportedSourceFormatError("Payment Scope must be CSV or XLSX.")

    def _resolve_columns(self, frame: pd.DataFrame) -> dict[str, str]:
        by_normalized: dict[str, list[str]] = defaultdict(list)
        for column in frame.columns:
            by_normalized[normalize_heading(column)].append(str(column))
        resolved: dict[str, str] = {}
        for canonical, configured in self.settings.payment_scope_column_map.items():
            if canonical in SCOPE_ALIASES and configured in frame.columns:
                resolved[canonical] = configured
        for canonical, aliases in SCOPE_ALIASES.items():
            if canonical in resolved:
                continue
            matches = {
                raw
                for alias in aliases | {normalize_heading(canonical)}
                for raw in by_normalized.get(alias, [])
            }
            if len(matches) == 1:
                resolved[canonical] = matches.pop()
        return resolved

    @staticmethod
    def _identifier(value: object) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @staticmethod
    def _blank(value: object) -> bool:
        return (
            value is None
            or (isinstance(value, float) and math.isnan(value))
            or str(value).strip() == ""
        )

    @staticmethod
    def _blocked(
        period_id: str,
        file: DriveFile,
        code: str,
        message: str,
        rows_read: int = 0,
    ) -> PaymentScopeIngestionResult:
        return PaymentScopeIngestionResult(
            status=IngestionStatus.BLOCKED,
            period_id=period_id,
            rows_read=rows_read,
            issues=(
                IngestionIssue(
                    level=AuditLevel.BLOCKING,
                    code=code,
                    message=message,
                    source_file_id=file.file_id,
                    source_filename=file.name,
                ),
            ),
        )


class PaymentScopeEligibilityService:
    """Filters by exact Restaurant ID membership; enrichment data is not an input."""

    def apply(
        self,
        admin_earnings: AdminEarningsIngestionResult,
        payment_scope: PaymentScopeIngestionResult,
    ) -> EligibilityResult:
        issues = admin_earnings.issues + payment_scope.issues
        if (
            admin_earnings.status == IngestionStatus.BLOCKED
            or payment_scope.status == IngestionStatus.BLOCKED
            or payment_scope.snapshot is None
        ):
            return EligibilityResult(
                status=IngestionStatus.BLOCKED,
                period_id=payment_scope.period_id,
                scope_snapshot=payment_scope.snapshot,
                issues=issues,
            )
        allowed = set(payment_scope.snapshot.restaurant_ids)
        eligible: list[EligibilityRecord] = []
        excluded: list[EligibilityRecord] = []
        for order in admin_earnings.records:
            if order.restaurant_id in allowed:
                eligible.append(
                    EligibilityRecord(
                        order=order,
                        state=EligibilityState.ELIGIBLE,
                        reason="RESTAURANT_ID_IN_PAYMENT_SCOPE",
                    )
                )
            else:
                excluded.append(
                    EligibilityRecord(
                        order=order,
                        state=EligibilityState.OUT_OF_SCOPE,
                        reason="RESTAURANT_ID_NOT_IN_PAYMENT_SCOPE",
                    )
                )
        status = (
            IngestionStatus.COMPLETED_WITH_WARNINGS
            if issues
            else IngestionStatus.SUCCESS
        )
        LOGGER.info(
            "payment_scope_eligibility_completed",
            extra={
                "period": payment_scope.period_id,
                "eligible_orders": len(eligible),
                "out_of_scope_orders": len(excluded),
                "status": status.value,
            },
        )
        return EligibilityResult(
            status=status,
            period_id=payment_scope.period_id,
            scope_snapshot=payment_scope.snapshot,
            eligible_orders=tuple(eligible),
            out_of_scope_orders=tuple(excluded),
            issues=issues,
        )
