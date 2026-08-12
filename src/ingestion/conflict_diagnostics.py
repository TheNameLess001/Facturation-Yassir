from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from statistics import mean, median

import pandas as pd

from src.ingestion.admin_earnings_normalizer import normalize_decimal
from src.ingestion.conflict_diagnostics_models import (
    ConflictCategory,
    ConflictDiagnostics,
    CountMetric,
    FinancialBandMetric,
    FinancialDifferenceMetric,
    InvalidFinancialMetric,
    RestaurantImpactMetric,
    SourceBehavior,
    StatusTransitionMetric,
    WeekPairMetric,
)

OPERATIONAL_FIELDS = frozenset({"operational_status", "cancellation_reason"})
FINANCIAL_FIELDS = frozenset(
    {
        "item_total",
        "subtotal",
        "gross_amount",
        "discount",
        "promo_amount",
        "delivery_fee",
        "commission_amount",
        "commission_rate",
        "currency",
    }
)
IDENTITY_FIELDS = frozenset({"restaurant_id", "restaurant_name", "order_id"})
TECHNICAL_FIELDS = frozenset(
    {
        "source_file_id",
        "source_filename",
        "source_week",
        "source_year",
        "source_modified_at",
        "source_row_number",
        "ingested_at",
        "lineage",
    }
)
# Diagnostic proposal only. Phase 3 canonical comparison remains exact until an
# explicit reconciliation policy is authorized.
CANDIDATE_CURRENCY_TOLERANCE = Decimal("0.005")


def _json(value: object, expected: type) -> object:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, expected):
        raise TypeError("Phase 3 diagnostic artifact has an invalid JSON field")
    return parsed


def _normalized_text(value: object) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = " ".join(text.split()).casefold()
    return text or None


def _semantic_value(field: str, value: object) -> object:
    if field in OPERATIONAL_FIELDS or field in {"restaurant_name", "currency"}:
        return _normalized_text(value)
    if field in FINANCIAL_FIELDS - {"currency"}:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return str(value)
    return value


def _effective_fields(fields: list[str], values: list[dict[str, object]]) -> tuple[str, ...]:
    return tuple(
        field
        for field in fields
        if len({_semantic_value(field, occurrence.get(field)) for occurrence in values}) > 1
    )


def _occurrence_key(value: dict[str, object]) -> tuple[str, int, int]:
    return (
        str(value.get("source_modified_at") or ""),
        int(value.get("source_week") or 0),
        int(value.get("source_row_number") or 0),
    )


def _week_key(value: dict[str, object]) -> tuple[int, int]:
    return int(value.get("source_year") or 0), int(value.get("source_week") or 0)


def _week_label(value: tuple[int, int]) -> str:
    return f"{value[0]}-W{value[1]:02d}"


def _week_distance(first: tuple[int, int], last: tuple[int, int]) -> int:
    if first[0] == last[0]:
        return last[1] - first[1]
    return 53 * (last[0] - first[0]) + last[1] - first[1]


def _difference_band(value: Decimal) -> str:
    if value == 0:
        return "0"
    if value <= 1:
        return "0 < Δ ≤ 1 MAD"
    if value <= 10:
        return "1 < Δ ≤ 10 MAD"
    if value <= 100:
        return "10 < Δ ≤ 100 MAD"
    return "Δ > 100 MAD"


def invalid_numeric_pattern(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "EMPTY"
    if re.fullmatch(r"[-–—]+", text):
        return "DASH"
    if text.startswith("=") or text.upper() in {"#N/A", "#VALUE!", "#REF!", "#DIV/0!"}:
        return "FORMULA_OR_ERROR"
    if text.casefold() in {"n/a", "na", "none", "null", "error", "nan"}:
        return "TEXT_SENTINEL"
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+", text):
        return "SCIENTIFIC_NOTATION"
    if re.search(r"\d", text) and re.search(r"\bmad\b", text, re.IGNORECASE):
        return "CURRENCY_FORMATTED"
    if re.fullmatch(r"[\d\s.,()+-]+", text):
        return "NUMERIC_FORMAT"
    if re.search(r"\d", text):
        return "UNEXPECTED_SYMBOL"
    if re.search(r"[A-Za-z]", text):
        return "TEXT"
    return "OTHER"


def potentially_safe_numeric(value: object) -> bool:
    if invalid_numeric_pattern(value) not in {
        "CURRENCY_FORMATTED",
        "NUMERIC_FORMAT",
        "SCIENTIFIC_NOTATION",
        "UNEXPECTED_SYMBOL",
    }:
        return False
    try:
        if invalid_numeric_pattern(value) == "SCIENTIFIC_NOTATION":
            return Decimal(str(value)).is_finite()
        return normalize_decimal(value) is not None
    except (TypeError, ValueError):
        return False


class ConflictDiagnosticsService:
    """Analyzes existing Phase 3 artifacts; it never resolves or rewrites orders."""

    def analyze(
        self,
        conflicts: pd.DataFrame,
        duplicates: pd.DataFrame,
        issues: pd.DataFrame,
        *,
        restaurant_observations: pd.DataFrame | None = None,
        source_columns: dict[tuple[str, str], str] | None = None,
    ) -> ConflictDiagnostics:
        field_counts: Counter[str] = Counter()
        combinations: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        transitions: Counter[tuple[str, str]] = Counter()
        week_pairs: Counter[tuple[str, str]] = Counter()
        financial_values: dict[str, list[Decimal]] = defaultdict(list)
        financial_bands: Counter[tuple[str, str]] = Counter()
        operational_only = financial = identity = mixed = formatting_only = lifecycle = auto = 0
        technical_lineage = 0
        same_week = adjacent_week = separated_week = 0

        for row in conflicts.itertuples(index=False):
            raw_fields = list(_json(row.conflicting_fields, list))
            values = list(_json(row.values_by_occurrence, list))
            occurrences = list(_json(row.occurrences, list))
            semantic_fields = _effective_fields(raw_fields, values)
            technical_fields = tuple(
                field for field in semantic_fields if field in TECHNICAL_FIELDS
            )
            technical_lineage += bool(technical_fields)
            fields = tuple(
                field for field in semantic_fields if field not in TECHNICAL_FIELDS
            )
            field_counts.update(fields)
            combinations[" + ".join(fields) if fields else "FORMATTING_ONLY"] += 1
            groups = {
                "OPERATIONAL" for field in fields if field in OPERATIONAL_FIELDS
            } | {"FINANCIAL" for field in fields if field in FINANCIAL_FIELDS} | {
                "IDENTITY" for field in fields if field in IDENTITY_FIELDS
            }
            is_operational_only = bool(fields) and groups == {"OPERATIONAL"}
            operational_only += is_operational_only
            financial += any(field in FINANCIAL_FIELDS for field in fields)
            identity += any(field in IDENTITY_FIELDS for field in fields)
            mixed += len(groups) > 1
            formatting_only += not fields and not technical_fields

            ordered = sorted(zip(occurrences, values, strict=True), key=lambda item: _occurrence_key(item[0]))
            weeks = sorted({_week_key(item) for item in occurrences})
            if weeks:
                span = _week_distance(weeks[0], weeks[-1])
                same_week += span == 0
                adjacent_week += span == 1
                separated_week += span >= 2
                week_pairs[(_week_label(weeks[0]), _week_label(weeks[-1]))] += 1

            status_transition = False
            if "operational_status" in fields and ordered:
                previous = _normalized_text(ordered[0][1].get("operational_status"))
                later = _normalized_text(ordered[-1][1].get("operational_status"))
                if previous != later:
                    transitions[((previous or "NULL").upper(), (later or "NULL").upper())] += 1
                    status_transition = True
            cancellation_enrichment = False
            if fields == ("cancellation_reason",) and ordered:
                previous = _normalized_text(ordered[0][1].get("cancellation_reason"))
                later = _normalized_text(ordered[-1][1].get("cancellation_reason"))
                cancellation_enrichment = previous is None and later is not None
            is_lifecycle = is_operational_only and (status_transition or cancellation_enrichment)
            lifecycle += is_lifecycle
            negligible_financial = self._negligible_financial_conflict(
                fields, values
            )
            is_auto = not fields or cancellation_enrichment or negligible_financial
            auto += is_auto

            if not fields or negligible_financial:
                category = ConflictCategory.AUTO_RESOLVABLE
            elif any(field in IDENTITY_FIELDS for field in fields):
                category = ConflictCategory.IDENTITY_CONFLICT
            elif any(field in FINANCIAL_FIELDS for field in fields):
                category = ConflictCategory.FINANCIAL_CONFLICT
            elif is_lifecycle:
                category = (
                    ConflictCategory.AUTO_RESOLVABLE
                    if cancellation_enrichment
                    else ConflictCategory.LIKELY_LIFECYCLE_UPDATE
                )
            elif is_operational_only:
                category = ConflictCategory.NEEDS_REVIEW
            else:
                category = ConflictCategory.SOURCE_DATA_ERROR
            categories[category.value] += 1

            for field in fields:
                if field not in FINANCIAL_FIELDS - {"currency"}:
                    continue
                decimals = []
                for occurrence in values:
                    value = occurrence.get(field)
                    if value is None:
                        continue
                    try:
                        decimals.append(Decimal(str(value)))
                    except InvalidOperation:
                        continue
                if len(decimals) >= 2:
                    difference = max(decimals) - min(decimals)
                    difference = abs(difference)
                    financial_values[field].append(difference)
                    financial_bands[(field, _difference_band(difference))] += 1

        restaurant_metrics = self._restaurant_impact(
            conflicts, restaurant_observations
        )
        invalid_metrics, invalid_total, safe_invalid = self._invalid_financial(
            issues, source_columns or {}
        )
        financial_metrics = tuple(
            FinancialDifferenceMetric(
                field=field,
                conflict_count=field_counts[field],
                measurable_count=len(values),
                median_absolute_difference=float(median(values)) if values else None,
                mean_absolute_difference=float(mean(values)) if values else None,
                max_absolute_difference=float(max(values)) if values else None,
            )
            for field, values in sorted(financial_values.items())
        )
        behavior = self._infer_behavior(*self._duplicate_chronology(duplicates))
        return ConflictDiagnostics(
            total_conflicting_order_ids=len(conflicts),
            operational_only=operational_only,
            financial_conflicts=financial,
            identity_conflicts=identity,
            mixed_conflicts=mixed,
            technical_formatting_only=formatting_only,
            technical_lineage_conflicts=technical_lineage,
            potential_lifecycle_updates=lifecycle,
            potential_auto_resolvable=auto,
            manual_review_required=len(conflicts) - auto,
            restaurants_impacted=len(restaurant_metrics),
            same_week_conflicts=same_week,
            adjacent_week_conflicts=adjacent_week,
            separated_week_conflicts=separated_week,
            invalid_financial_values=invalid_total,
            potentially_safe_invalid_values=safe_invalid,
            still_invalid_values=invalid_total - safe_invalid,
            source_behavior=behavior,
            field_counts=tuple(
                CountMetric(label=field, count=count)
                for field, count in field_counts.most_common()
            ),
            field_combinations=tuple(
                CountMetric(label=label, count=count)
                for label, count in combinations.most_common()
            ),
            category_counts=tuple(
                CountMetric(label=label, count=count)
                for label, count in categories.most_common()
            ),
            status_transitions=tuple(
                StatusTransitionMetric(
                    previous_status=pair[0], later_status=pair[1], count=count
                )
                for pair, count in transitions.most_common()
            ),
            week_pairs=tuple(
                WeekPairMetric(earlier_week=pair[0], later_week=pair[1], count=count)
                for pair, count in week_pairs.most_common()
            ),
            financial_differences=financial_metrics,
            financial_bands=tuple(
                FinancialBandMetric(field=key[0], band=key[1], count=count)
                for key, count in sorted(financial_bands.items())
            ),
            restaurant_impact=restaurant_metrics,
            invalid_financial_patterns=invalid_metrics,
        )

    @staticmethod
    def _infer_behavior(same: int, adjacent: int, separated: int) -> SourceBehavior:
        total = same + adjacent + separated
        if not total:
            return SourceBehavior.INDEPENDENT
        if same / total >= 0.2 and (adjacent + separated) / total >= 0.2:
            return SourceBehavior.MIXED
        if separated / total >= 0.5:
            return SourceBehavior.CUMULATIVE
        if adjacent / total >= 0.5:
            return SourceBehavior.ROLLING
        return SourceBehavior.INDEPENDENT

    @staticmethod
    def _negligible_financial_conflict(
        fields: tuple[str, ...], values: list[dict[str, object]]
    ) -> bool:
        if not fields or not set(fields).issubset(FINANCIAL_FIELDS - {"currency"}):
            return False
        for field in fields:
            decimals: list[Decimal] = []
            for occurrence in values:
                value = occurrence.get(field)
                if value is None:
                    return False
                try:
                    decimals.append(Decimal(str(value)))
                except InvalidOperation:
                    return False
            if (
                len(decimals) < 2
                or abs(max(decimals) - min(decimals))
                > CANDIDATE_CURRENCY_TOLERANCE
            ):
                return False
        return True

    @staticmethod
    def _duplicate_chronology(duplicates: pd.DataFrame) -> tuple[int, int, int]:
        same = adjacent = separated = 0
        for _, group in duplicates.groupby("order_id"):
            occurrences = [
                _json(value, dict) for value in group["occurrence"].tolist()
            ]
            weeks = sorted({_week_key(item) for item in occurrences})
            if not weeks:
                continue
            span = _week_distance(weeks[0], weeks[-1])
            if span == 0:
                same += 1
            elif span == 1:
                adjacent += 1
            else:
                separated += 1
        return same, adjacent, separated

    @staticmethod
    def _restaurant_impact(
        conflicts: pd.DataFrame, observations: pd.DataFrame | None
    ) -> tuple[RestaurantImpactMetric, ...]:
        if observations is None or observations.empty:
            return ()
        conflict_ids = set(conflicts["order_id"].astype(str))
        values = observations.copy()
        values["order_id"] = values["order_id"].astype(str)
        values["restaurant_id"] = values["restaurant_id"].astype(str)
        values = values.drop_duplicates(["order_id", "restaurant_id"])
        totals = values.groupby("restaurant_id")["order_id"].nunique()
        affected = values[values["order_id"].isin(conflict_ids)]
        counts = affected.groupby("restaurant_id")["order_id"].nunique()
        names = (
            affected.dropna(subset=["restaurant_name"])
            .drop_duplicates("restaurant_id")
            .set_index("restaurant_id")["restaurant_name"]
            .to_dict()
        )
        return tuple(
            RestaurantImpactMetric(
                restaurant_id=restaurant_id,
                restaurant_name=names.get(restaurant_id),
                conflicting_order_ids=int(count),
                total_observed_order_ids=int(totals[restaurant_id]),
                conflict_rate=float(count / totals[restaurant_id]),
            )
            for restaurant_id, count in counts.sort_values(ascending=False).items()
        )

    @staticmethod
    def _invalid_financial(
        issues: pd.DataFrame, source_columns: dict[tuple[str, str], str]
    ) -> tuple[tuple[InvalidFinancialMetric, ...], int, int]:
        invalid = issues[issues["category"] == "INVALID_FINANCIAL_VALUE"].copy()
        groups: Counter[tuple[str, str | None, str | None, int | None, str, bool]] = Counter()
        for row in invalid.itertuples(index=False):
            occurrence = _json(row.occurrence, dict) if row.occurrence else {}
            filename = occurrence.get("source_filename")
            week = occurrence.get("source_week")
            pattern = invalid_numeric_pattern(row.raw_value)
            safe = potentially_safe_numeric(row.raw_value)
            source_column = source_columns.get((str(filename), str(row.field)))
            groups[(str(row.field), source_column, filename, week, pattern, safe)] += 1
        metrics = tuple(
            InvalidFinancialMetric(
                canonical_field=key[0],
                source_column=key[1],
                source_filename=key[2],
                source_week=key[3],
                pattern=key[4],
                count=count,
                potentially_safe=count if key[5] else 0,
            )
            for key, count in groups.most_common()
        )
        safe_total = sum(item.potentially_safe for item in metrics)
        return metrics, len(invalid), safe_total
