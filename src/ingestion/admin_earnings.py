from __future__ import annotations

import io
import logging
import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from src.config import Settings
from src.google.exceptions import GoogleIntegrationError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import DriveFile
from src.ingestion.admin_earnings_models import (
    AdminEarningsFileResult,
    AdminEarningsIngestionResult,
    DuplicateDiagnostic,
    IngestionIssue,
    NormalizedAdminEarningsRow,
)
from src.ingestion.exceptions import (
    SourceFileTooLargeError,
    SourceParseError,
    UnsupportedSourceFormatError,
)
from src.models.enums import AuditLevel, DuplicateKind, IngestionStatus

LOGGER = logging.getLogger(__name__)
CANONICAL_ALIASES: dict[str, frozenset[str]] = {
    "order_id": frozenset(
        {"order id", "orderid", "id order", "commande id", "id commande"}
    ),
    "restaurant_id": frozenset(
        {"restaurant id", "restaurantid", "rst id", "id restaurant", "partner id"}
    ),
    "restaurant_name": frozenset(
        {"restaurant name", "restaurant", "rst name", "nom restaurant", "partner name"}
    ),
    "order_date": frozenset(
        {"order date", "date order", "created at", "creation date", "date commande"}
    ),
    "gross_amount": frozenset(
        {"gross amount", "order amount", "total amount", "amount", "gmv", "montant"}
    ),
    "operational_status": frozenset(
        {"operational status", "order status", "status", "statut", "original status"}
    ),
    "cancellation_reason": frozenset(
        {
            "cancellation reason",
            "cancel reason",
            "cancellation cause",
            "raison annulation",
        }
    ),
}
REQUIRED_COLUMNS = frozenset(
    {"order_id", "restaurant_id", "order_date", "gross_amount", "operational_status"}
)


def normalize_column_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


class AdminEarningsIngestionService:
    """Reads immutable Admin Earnings files and emits canonical source records only."""

    def __init__(self, drive: ReadOnlyDriveService, settings: Settings) -> None:
        self.drive = drive
        self.settings = settings

    def ingest(self, files: tuple[DriveFile, ...]) -> AdminEarningsIngestionResult:
        started_at = datetime.now(UTC)
        file_results: list[AdminEarningsFileResult] = []
        all_rows: list[NormalizedAdminEarningsRow] = []
        issues: list[IngestionIssue] = []
        if not files:
            issues.append(
                IngestionIssue(
                    level=AuditLevel.BLOCKING,
                    code="NO_SOURCE_FILES",
                    message="No Admin Earnings source files were provided.",
                )
            )
        for file in sorted(
            files, key=lambda item: (item.modified_time, item.name, item.file_id)
        ):
            file_result, records = self._ingest_file(file)
            file_results.append(file_result)
            all_rows.extend(records)
            issues.extend(file_result.issues)

        records, duplicates, duplicate_issues = self._diagnose_duplicates(all_rows)
        issues.extend(duplicate_issues)
        has_blocker = any(issue.level == AuditLevel.BLOCKING for issue in issues)
        status = (
            IngestionStatus.BLOCKED
            if has_blocker
            else IngestionStatus.COMPLETED_WITH_WARNINGS
            if issues
            else IngestionStatus.SUCCESS
        )
        completed_at = datetime.now(UTC)
        LOGGER.info(
            "admin_earnings_ingestion_completed",
            extra={
                "files_found": len(files),
                "rows_read": sum(item.rows_read for item in file_results),
                "valid_records": len(records),
                "status": status.value,
            },
        )
        return AdminEarningsIngestionResult(
            status=status,
            records=records,
            file_results=tuple(file_results),
            duplicates=duplicates,
            issues=tuple(issues),
            started_at=started_at,
            completed_at=completed_at,
        )

    def _ingest_file(
        self, file: DriveFile
    ) -> tuple[AdminEarningsFileResult, tuple[NormalizedAdminEarningsRow, ...]]:
        try:
            max_bytes = self.settings.admin_earnings_max_file_mb * 1024 * 1024
            if file.size is not None and file.size > max_bytes:
                raise SourceFileTooLargeError(
                    f"File exceeds the configured {self.settings.admin_earnings_max_file_mb} MB limit."
                )
            content = self.drive.download_file(file.file_id)
            if len(content) > max_bytes:
                raise SourceFileTooLargeError(
                    f"File exceeds the configured {self.settings.admin_earnings_max_file_mb} MB limit."
                )
            frame = self._read_frame(file, content)
            columns = self._resolve_columns(frame)
        except (
            GoogleIntegrationError,
            SourceParseError,
            UnsupportedSourceFormatError,
            SourceFileTooLargeError,
        ) as exc:
            issue = self._file_issue(file, type(exc).__name__.upper(), str(exc))
            return (
                AdminEarningsFileResult(
                    source_file_id=file.file_id,
                    source_filename=file.name,
                    issues=(issue,),
                ),
                (),
            )

        missing = sorted(REQUIRED_COLUMNS - columns.keys())
        if missing:
            issue = self._file_issue(
                file,
                "MISSING_REQUIRED_COLUMNS",
                f"Missing required canonical column(s): {', '.join(missing)}.",
            )
            return (
                AdminEarningsFileResult(
                    source_file_id=file.file_id,
                    source_filename=file.name,
                    rows_read=len(frame),
                    detected_columns=columns,
                    issues=(issue,),
                ),
                (),
            )

        records: list[NormalizedAdminEarningsRow] = []
        row_issues: list[IngestionIssue] = []
        for position, (_, row) in enumerate(frame.iterrows(), start=2):
            if all(self._blank(value) for value in row.tolist()):
                continue
            try:
                records.append(self._normalize_row(row, columns, file, position))
            except ValueError as exc:
                row_issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="INVALID_ROW",
                        message=str(exc),
                        source_file_id=file.file_id,
                        source_filename=file.name,
                        source_row_number=position,
                    )
                )
        return (
            AdminEarningsFileResult(
                source_file_id=file.file_id,
                source_filename=file.name,
                rows_read=len(frame),
                rows_valid=len(records),
                unique_order_ids=len({record.order_id for record in records}),
                duplicate_rows=len(records)
                - len({record.order_id for record in records}),
                detected_columns=columns,
                issues=tuple(row_issues),
            ),
            tuple(records),
        )

    def _read_frame(self, file: DriveFile, content: bytes) -> pd.DataFrame:
        suffix = file.name.casefold().rsplit(".", maxsplit=1)[-1]
        try:
            if suffix == "csv":
                return pd.read_csv(
                    io.BytesIO(content),
                    dtype=object,
                    keep_default_na=False,
                    encoding=self.settings.admin_earnings_csv_encoding,
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
            LOGGER.warning(
                "admin_earnings_parse_failed", extra={"source_file_id": file.file_id}
            )
            raise SourceParseError(
                "The source file could not be parsed safely."
            ) from exc
        raise UnsupportedSourceFormatError(
            "Only CSV and XLSX Admin Earnings files are supported."
        )

    def _resolve_columns(self, frame: pd.DataFrame) -> dict[str, str]:
        normalized_to_raw: dict[str, list[str]] = defaultdict(list)
        for raw in frame.columns:
            normalized_to_raw[normalize_column_name(raw)].append(str(raw))
        resolved: dict[str, str] = {}
        for (
            canonical,
            configured_raw,
        ) in self.settings.admin_earnings_column_map.items():
            if canonical in CANONICAL_ALIASES and configured_raw in frame.columns:
                resolved[canonical] = configured_raw
        for canonical, aliases in CANONICAL_ALIASES.items():
            if canonical in resolved:
                continue
            matches = [
                raw
                for alias in aliases | {normalize_column_name(canonical)}
                for raw in normalized_to_raw.get(alias, [])
            ]
            if len(set(matches)) == 1:
                resolved[canonical] = matches[0]
        return resolved

    def _normalize_row(
        self,
        row: pd.Series,
        columns: dict[str, str],
        file: DriveFile,
        row_number: int,
    ) -> NormalizedAdminEarningsRow:
        def required(field: str) -> object:
            value = row[columns[field]]
            if self._blank(value):
                raise ValueError(f"{field} is required.")
            return value

        return NormalizedAdminEarningsRow(
            order_id=self._identifier(required("order_id")),
            restaurant_id=self._identifier(required("restaurant_id")),
            restaurant_name=self._optional_text(row, columns, "restaurant_name"),
            order_date=self._date(required("order_date")),
            gross_amount=self._amount(required("gross_amount")),
            operational_status=str(required("operational_status")).strip(),
            cancellation_reason=self._optional_text(
                row, columns, "cancellation_reason"
            ),
            source_file_id=file.file_id,
            source_filename=file.name,
            source_row_number=row_number,
            source_values=tuple(
                (str(column), "" if self._blank(value) else str(value))
                for column, value in row.items()
            ),
        )

    def _diagnose_duplicates(
        self, rows: list[NormalizedAdminEarningsRow]
    ) -> tuple[
        tuple[NormalizedAdminEarningsRow, ...],
        tuple[DuplicateDiagnostic, ...],
        tuple[IngestionIssue, ...],
    ]:
        grouped: dict[str, list[NormalizedAdminEarningsRow]] = defaultdict(list)
        for row in rows:
            grouped[row.order_id].append(row)
        accepted: list[NormalizedAdminEarningsRow] = []
        diagnostics: list[DuplicateDiagnostic] = []
        issues: list[IngestionIssue] = []
        for order_id, occurrences in grouped.items():
            if len(occurrences) == 1:
                accepted.append(occurrences[0])
                continue
            exact = all(
                item.comparison_key() == occurrences[0].comparison_key()
                for item in occurrences[1:]
            )
            kind = DuplicateKind.EXACT if exact else DuplicateKind.CONFLICTING
            diagnostics.append(
                DuplicateDiagnostic(
                    order_id=order_id,
                    kind=kind,
                    occurrences=len(occurrences),
                    source_locations=tuple(
                        f"{item.source_filename}:row {item.source_row_number}"
                        for item in occurrences
                    ),
                )
            )
            if exact:
                accepted.append(occurrences[0])
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.WARNING,
                        code="EXACT_DUPLICATE_ORDER_ID",
                        message=f"Exact duplicate Order ID {order_id} was collapsed deterministically.",
                    )
                )
            else:
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="CONFLICTING_DUPLICATE_ORDER_ID",
                        message=f"Conflicting Order ID {order_id} requires review; no version was accepted.",
                    )
                )
        return tuple(accepted), tuple(diagnostics), tuple(issues)

    @staticmethod
    def _identifier(value: object) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = str(value).strip()
        if not text:
            raise ValueError("Identifier cannot be blank.")
        return text

    def _date(self, value: object) -> datetime:
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parsed = pd.to_datetime(value, unit="D", origin="1899-12-30", utc=True)
            else:
                parsed = pd.to_datetime(
                    value,
                    utc=True,
                    dayfirst=self.settings.admin_earnings_date_day_first,
                    errors="raise",
                )
            return parsed.to_pydatetime()
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("order_date is invalid.") from exc

    @staticmethod
    def _amount(value: object) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("gross_amount is invalid.")  # noqa: TRY004
        if isinstance(value, (int, Decimal)):
            result = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("gross_amount is invalid.")
            result = Decimal(str(value))
        else:
            text = str(value).strip().replace("\u00a0", " ")
            negative = text.startswith("(") and text.endswith(")")
            text = re.sub(r"[^0-9,.-]", "", text.strip("()"))
            if "," in text and "." in text:
                decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
                thousands_separator = "." if decimal_separator == "," else ","
                text = text.replace(thousands_separator, "").replace(
                    decimal_separator, "."
                )
            elif "," in text:
                tail = text.rsplit(",", maxsplit=1)[1]
                text = text.replace(",", "." if len(tail) in {1, 2} else "")
            try:
                result = Decimal(text)
            except InvalidOperation as exc:
                raise ValueError("gross_amount is invalid.") from exc
            if negative:
                result = -result
        if not result.is_finite():
            raise ValueError("gross_amount is invalid.")
        return result

    @staticmethod
    def _optional_text(
        row: pd.Series, columns: dict[str, str], field: str
    ) -> str | None:
        if field not in columns or AdminEarningsIngestionService._blank(
            row[columns[field]]
        ):
            return None
        return str(row[columns[field]]).strip()

    @staticmethod
    def _blank(value: Any) -> bool:
        return (
            value is None
            or (isinstance(value, float) and math.isnan(value))
            or str(value).strip() == ""
        )

    @staticmethod
    def _file_issue(file: DriveFile, code: str, message: str) -> IngestionIssue:
        return IngestionIssue(
            level=AuditLevel.BLOCKING,
            code=code,
            message=message,
            source_file_id=file.file_id,
            source_filename=file.name,
        )
