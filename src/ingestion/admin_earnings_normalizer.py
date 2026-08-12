from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from src.google.models import DriveFile
from src.ingestion.phase3_models import (
    CanonicalAdminOrder,
    IngestionIssueRecord,
    IssueSeverity,
    SourceOccurrence,
)


def normalize_identifier(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return text.split(".", 1)[0].lstrip("+")
    return text


def normalize_decimal(value: object) -> Decimal | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        raise TypeError("Boolean is not a financial value")
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Financial value is not finite")
        return result
    text = str(value).strip().replace("\u00a0", " ")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9,.-]", "", text.strip("()"))
    if not cleaned or cleaned in {"-", ".", ","}:
        raise ValueError("Financial value is invalid")
    if "," in cleaned and "." in cleaned:
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal, ".")
    elif "," in cleaned:
        tail = cleaned.rsplit(",", 1)[1]
        cleaned = cleaned.replace(",", "." if len(tail) in {1, 2} else "")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Financial value is invalid") from exc
    if negative:
        result = -result
    if not result.is_finite():
        raise ValueError("Financial value is not finite")
    return result


def normalize_datetime(value: object) -> tuple[datetime | None, str | None]:
    if value is None or not str(value).strip():
        return None, None
    original = str(value).strip()
    try:
        parsed = pd.to_datetime(value, errors="raise")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Date value is invalid") from exc
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(UTC)
    return timestamp.to_pydatetime(), original


class AdminEarningsNormalizer:
    financial_fields = (
        "item_total",
        "subtotal",
        "gross_amount",
        "discount",
        "promo_amount",
        "delivery_fee",
        "commission_amount",
        "commission_rate",
    )
    retained_extra_headers = frozenset(
        {
            # Both columns compete for canonical ``discount`` in the real schema.
            # Their raw values remain available without guessing which one wins.
            "total discount amount",
            "discount amount",
        }
    )

    def normalize_frame(
        self,
        frame: pd.DataFrame,
        mapping: dict[str, str],
        file: DriveFile,
        week: int,
        year: int,
        ingested_at: datetime,
    ) -> tuple[list[CanonicalAdminOrder], list[IngestionIssueRecord]]:
        records: list[CanonicalAdminOrder] = []
        issues: list[IngestionIssueRecord] = []
        mapped_columns = set(mapping.values())
        for row_index, (_, row) in enumerate(frame.iterrows(), start=2):
            if all(self._blank(item) for item in row.tolist()):
                continue
            occurrence = SourceOccurrence(
                source_file_id=file.file_id,
                source_filename=file.name,
                source_week=week,
                source_year=year,
                source_modified_at=file.modified_time,
                source_row_number=row_index,
            )
            order_id = normalize_identifier(self._value(row, mapping, "order_id"))
            if order_id is None:
                issues.append(
                    IngestionIssueRecord(
                        category="MISSING_ORDER_ID",
                        severity=IssueSeverity.BLOCKING,
                        message="Row has no usable Order ID.",
                        occurrence=occurrence,
                    )
                )
                continue
            parsed_date = None
            original_date = None
            date_value = self._value(row, mapping, "order_date")
            if not self._blank(date_value):
                try:
                    parsed_date, original_date = normalize_datetime(date_value)
                except (TypeError, ValueError):
                    issues.append(
                        IngestionIssueRecord(
                            category="INVALID_DATE",
                            severity=IssueSeverity.BLOCKING,
                            message="Order date could not be parsed.",
                            occurrence=occurrence,
                            order_id=order_id,
                            field="order_date",
                            raw_value=str(date_value)[:200],
                        )
                    )
            values: dict[str, Decimal | None] = {}
            for field in self.financial_fields:
                raw = self._value(row, mapping, field)
                try:
                    values[field] = normalize_decimal(raw)
                except ValueError:
                    values[field] = None
                    issues.append(
                        IngestionIssueRecord(
                            category="INVALID_FINANCIAL_VALUE",
                            severity=IssueSeverity.BLOCKING,
                            message=f"{field} is invalid and was retained as null.",
                            occurrence=occurrence,
                            order_id=order_id,
                            field=field,
                            raw_value=str(raw)[:200],
                        )
                    )
            created_at = None
            created_value = self._value(row, mapping, "order_created_at")
            if not self._blank(created_value):
                try:
                    created_at, _ = normalize_datetime(created_value)
                except ValueError:
                    issues.append(
                        IngestionIssueRecord(
                            category="INVALID_DATE",
                            severity=IssueSeverity.WARNING,
                            message="Order creation timestamp could not be parsed.",
                            occurrence=occurrence,
                            order_id=order_id,
                            field="order_created_at",
                            raw_value=str(created_value)[:200],
                        )
                    )
            records.append(
                CanonicalAdminOrder(
                    order_id=order_id,
                    restaurant_id=normalize_identifier(self._value(row, mapping, "restaurant_id")),
                    restaurant_name=self._text(self._value(row, mapping, "restaurant_name")),
                    order_created_at=created_at,
                    order_date=parsed_date.date() if parsed_date else None,
                    original_order_timestamp=original_date,
                    operational_status=self._text(self._value(row, mapping, "operational_status")),
                    cancellation_reason=self._text(self._value(row, mapping, "cancellation_reason")),
                    currency=self._text(self._value(row, mapping, "currency")),
                    lineage=(occurrence,),
                    ingested_at=ingested_at,
                    raw_extra={
                        str(column): None if self._blank(value) else str(value)
                        for column, value in row.items()
                        if str(column) not in mapped_columns
                        and re.sub(r"[^a-z0-9]+", " ", str(column).casefold()).strip()
                        in self.retained_extra_headers
                    },
                    **values,
                )
            )
        return records, issues

    @staticmethod
    def _value(row: pd.Series, mapping: dict[str, str], field: str) -> object:
        column = mapping.get(field)
        return row[column] if column else None

    @staticmethod
    def _text(value: object) -> str | None:
        return None if AdminEarningsNormalizer._blank(value) else str(value).strip()

    @staticmethod
    def _blank(value: object) -> bool:
        return value is None or (isinstance(value, float) and math.isnan(value)) or not str(value).strip()
