"""LEGACY Payment-Scope enrichment; active registry uses ``scope_registry``."""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from src.config import Settings
from src.google.exceptions import GoogleIntegrationError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import DriveFile
from src.ingestion.admin_earnings_models import IngestionIssue
from src.ingestion.exceptions import SourceFileTooLargeError, SourceParseError
from src.ingestion.payment_scope_models import EligibilityResult
from src.models.domain import Restaurant
from src.models.enums import AuditLevel, IngestionStatus
from src.restaurants.models import (
    EnrichedEligibilityRecord,
    RSTEnrichmentResult,
    RSTListResult,
)

ALIASES = {
    "restaurant_id": {"restaurant id", "rst id", "id restaurant", "partner id"},
    "restaurant_name": {"restaurant name", "restaurant", "nom restaurant"},
    "chain": {"chain", "chaine", "brand"},
    "legal_entity": {"legal entity", "raison sociale", "company name"},
    "ice": {"ice"},
    "tax_id": {"if", "tax id", "identifiant fiscal"},
    "rc": {"rc", "registre commerce"},
    "rib": {"rib", "bank account"},
    "bank": {"bank", "banque"},
    "address": {"address", "adresse"},
    "email": {"email", "partner email"},
    "finance_email": {"finance email", "billing email"},
    "phone": {"phone", "telephone"},
    "city": {"city", "ville"},
    "area": {"area", "zone"},
    "account_manager": {"am", "account manager"},
    "commission_rate": {"commission", "commission rate"},
    "partner_status": {"partner status", "status", "statut partenaire"},
}


def normalize_heading(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


class RSTEnrichmentService:
    def __init__(self, drive: ReadOnlyDriveService, settings: Settings) -> None:
        self.drive = drive
        self.settings = settings

    def ingest_master(self, file: DriveFile) -> RSTListResult:
        try:
            limit = self.settings.rst_max_file_mb * 1024 * 1024
            if file.size is not None and file.size > limit:
                raise SourceFileTooLargeError(
                    "RST List exceeds the configured size limit."
                )
            content = self.drive.download_file(file.file_id)
            if len(content) > limit:
                raise SourceFileTooLargeError(
                    "RST List exceeds the configured size limit."
                )
            frame = self._read(file, content)
        except (
            GoogleIntegrationError,
            SourceFileTooLargeError,
            SourceParseError,
        ) as exc:
            return self._blocked(file, type(exc).__name__.upper(), str(exc))
        columns = self._columns(frame)
        if "restaurant_id" not in columns or "restaurant_name" not in columns:
            return self._blocked(
                file,
                "MISSING_RST_IDENTITY_COLUMNS",
                "RST List requires Restaurant ID and Restaurant Name columns.",
                len(frame),
            )
        grouped: dict[str, list[Restaurant]] = defaultdict(list)
        issues: list[IngestionIssue] = []
        for row_number, (_, row) in enumerate(frame.iterrows(), start=2):
            raw_id = row[columns["restaurant_id"]]
            raw_name = row[columns["restaurant_name"]]
            if self._blank(raw_id) and all(
                self._blank(value) for value in row.tolist()
            ):
                continue
            if self._blank(raw_id) or self._blank(raw_name):
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="INVALID_RST_IDENTITY",
                        message="Restaurant ID and Restaurant Name are required.",
                        source_file_id=file.file_id,
                        source_filename=file.name,
                        source_row_number=row_number,
                    )
                )
                continue
            values = {key: self._text(row, columns, key) for key in ALIASES}
            try:
                restaurant = Restaurant(
                    restaurant_id=self._identifier(raw_id),
                    restaurant_name=str(raw_name).strip(),
                    chain=values["chain"],
                    legal_entity=values["legal_entity"],
                    ice=values["ice"],
                    tax_id=values["tax_id"],
                    rc=values["rc"],
                    rib=values["rib"],
                    bank=values["bank"],
                    address=values["address"],
                    email=values["email"],
                    finance_email=values["finance_email"],
                    phone=values["phone"],
                    city=values["city"],
                    area=values["area"],
                    account_manager=values["account_manager"],
                    commission_rate=self._commission(values["commission_rate"]),
                    partner_status=values["partner_status"],
                )
            except ValueError:
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="INVALID_RST_FINANCIAL_METADATA",
                        message="Commission rate is invalid.",
                        source_file_id=file.file_id,
                        source_filename=file.name,
                        source_row_number=row_number,
                    )
                )
                continue
            grouped[restaurant.restaurant_id].append(restaurant)
        restaurants: list[Restaurant] = []
        for restaurant_id, candidates in grouped.items():
            if any(item != candidates[0] for item in candidates[1:]):
                issues.append(
                    IngestionIssue(
                        level=AuditLevel.BLOCKING,
                        code="CONFLICTING_RST_RESTAURANT_ID",
                        message=f"Conflicting RST records found for Restaurant ID {restaurant_id}.",
                        source_file_id=file.file_id,
                        source_filename=file.name,
                    )
                )
            else:
                restaurants.append(candidates[0])
        has_blocker = any(issue.level == AuditLevel.BLOCKING for issue in issues)
        return RSTListResult(
            status=(
                IngestionStatus.BLOCKED
                if has_blocker
                else IngestionStatus.COMPLETED_WITH_WARNINGS
                if issues
                else IngestionStatus.SUCCESS
            ),
            restaurants=tuple(sorted(restaurants, key=lambda item: item.restaurant_id)),
            rows_read=len(frame),
            source_file_id=file.file_id,
            source_filename=file.name,
            content_hash=hashlib.sha256(content).hexdigest(),
            issues=tuple(issues),
        )

    def enrich(
        self, eligibility: EligibilityResult, rst: RSTListResult
    ) -> RSTEnrichmentResult:
        issues = eligibility.issues + rst.issues
        if (
            eligibility.status == IngestionStatus.BLOCKED
            or rst.status == IngestionStatus.BLOCKED
        ):
            return RSTEnrichmentResult(
                status=IngestionStatus.BLOCKED,
                period_id=eligibility.period_id,
                scope_snapshot=eligibility.scope_snapshot,
                issues=issues,
                completed_at=datetime.now(UTC),
            )
        master = {item.restaurant_id: item for item in rst.restaurants}
        records = tuple(
            EnrichedEligibilityRecord(
                eligibility=item,
                restaurant=master.get(item.order.restaurant_id),
                enrichment_status=(
                    "ENRICHED" if item.order.restaurant_id in master else "RST_MISSING"
                ),
            )
            for item in eligibility.eligible_orders
        )
        missing = tuple(
            sorted(
                {
                    item.eligibility.order.restaurant_id
                    for item in records
                    if item.restaurant is None
                }
            )
        )
        if missing:
            issues += (
                IngestionIssue(
                    level=AuditLevel.BLOCKING,
                    code="ELIGIBLE_RESTAURANT_MISSING_FROM_RST",
                    message=f"{len(missing)} eligible Restaurant ID(s) are missing from RST List.",
                ),
            )
        scoped_ids = (
            set(eligibility.scope_snapshot.restaurant_ids)
            if eligibility.scope_snapshot
            else set()
        )
        restaurants = tuple(
            item for item in rst.restaurants if item.restaurant_id in scoped_ids
        )
        return RSTEnrichmentResult(
            status=IngestionStatus.BLOCKED if missing else IngestionStatus.SUCCESS,
            period_id=eligibility.period_id,
            scope_snapshot=eligibility.scope_snapshot,
            records=records,
            restaurants=restaurants,
            missing_restaurant_ids=missing,
            issues=issues,
            completed_at=datetime.now(UTC),
        )

    def _read(self, file: DriveFile, content: bytes) -> pd.DataFrame:
        try:
            if file.name.casefold().endswith(".csv"):
                return pd.read_csv(
                    io.BytesIO(content),
                    dtype=object,
                    keep_default_na=False,
                    encoding=self.settings.rst_csv_encoding,
                    sep=None,
                    engine="python",
                )
            if file.name.casefold().endswith(".xlsx"):
                return pd.read_excel(
                    io.BytesIO(content),
                    dtype=object,
                    keep_default_na=False,
                    engine="openpyxl",
                )
        except Exception as exc:
            raise SourceParseError("RST List could not be parsed safely.") from exc
        raise SourceParseError("RST List must be CSV or XLSX.")

    def _columns(self, frame: pd.DataFrame) -> dict[str, str]:
        normalized = {
            normalize_heading(column): str(column) for column in frame.columns
        }
        resolved = {
            key: raw
            for key, raw in self.settings.rst_column_map.items()
            if key in ALIASES and raw in frame.columns
        }
        for key, aliases in ALIASES.items():
            matches = {
                normalized[item]
                for item in aliases | {normalize_heading(key)}
                if item in normalized
            }
            if key not in resolved and len(matches) == 1:
                resolved[key] = matches.pop()
        return resolved

    @staticmethod
    def _text(row: pd.Series, columns: dict[str, str], key: str) -> str | None:
        if key not in columns or RSTEnrichmentService._blank(row[columns[key]]):
            return None
        return str(row[columns[key]]).strip()

    @staticmethod
    def _identifier(value: object) -> str:
        return (
            str(int(value))
            if isinstance(value, float) and value.is_integer()
            else str(value).strip()
        )

    @staticmethod
    def _commission(value: str | None) -> Decimal:
        if value is None:
            return Decimal(0)
        text = value.strip().replace("%", "").replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("Invalid commission rate in RST List.") from exc
        return number / 100 if number > 1 else number

    @staticmethod
    def _blank(value: object) -> bool:
        return (
            value is None
            or (isinstance(value, float) and math.isnan(value))
            or str(value).strip() == ""
        )

    @staticmethod
    def _blocked(
        file: DriveFile, code: str, message: str, rows: int = 0
    ) -> RSTListResult:
        return RSTListResult(
            status=IngestionStatus.BLOCKED,
            rows_read=rows,
            source_file_id=file.file_id,
            source_filename=file.name,
            issues=(
                IngestionIssue(level=AuditLevel.BLOCKING, code=code, message=message),
            ),
        )
