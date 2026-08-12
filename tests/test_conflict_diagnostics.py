from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from src.ingestion.conflict_diagnostics import (
    ConflictDiagnosticsService,
    invalid_numeric_pattern,
    potentially_safe_numeric,
)
from src.ingestion.conflict_diagnostics_models import ConflictCategory
from src.ingestion.deduplication import deduplicate_orders
from src.ingestion.phase3_models import CanonicalAdminOrder, SourceOccurrence


def occurrence(week: int, modified_day: int) -> dict[str, object]:
    return {
        "source_file_id": f"week-{week}",
        "source_filename": f"data week {week}_2026.csv",
        "source_week": week,
        "source_year": 2026,
        "source_modified_at": f"2026-08-{modified_day:02d}T00:00:00Z",
        "source_row_number": 2,
    }


def conflict(
    order_id: str,
    fields: list[str],
    first: dict[str, object],
    later: dict[str, object],
    *,
    first_week: int = 1,
    later_week: int = 2,
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "conflicting_fields": json.dumps(fields),
        "values_by_occurrence": json.dumps([first, later]),
        "occurrences": json.dumps(
            [occurrence(first_week, 1), occurrence(later_week, 2)]
        ),
    }


def duplicate_rows(order_id: str = "O-1", first_week: int = 1, later_week: int = 2):
    return pd.DataFrame(
        [
            {"order_id": order_id, "occurrence": json.dumps(occurrence(first_week, 1))},
            {"order_id": order_id, "occurrence": json.dumps(occurrence(later_week, 2))},
        ]
    )


def empty_issues() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["category", "occurrence", "field", "raw_value"]
    )


def analyze(*rows: dict[str, object]):
    return ConflictDiagnosticsService().analyze(
        pd.DataFrame(rows), duplicate_rows(), empty_issues()
    )


def category_count(result, category: ConflictCategory) -> int:
    return next(
        (item.count for item in result.category_counts if item.label == category.value),
        0,
    )


def test_technical_lineage_does_not_create_deduplication_conflict() -> None:
    first_occurrence = SourceOccurrence.model_validate(occurrence(1, 1))
    later_occurrence = SourceOccurrence.model_validate(occurrence(2, 2))
    first = CanonicalAdminOrder(
        order_id="O-1",
        restaurant_id="R-1",
        lineage=(first_occurrence,),
        ingested_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    later = first.model_copy(
        update={
            "lineage": (later_occurrence,),
            "ingested_at": datetime(2026, 8, 2, tzinfo=UTC),
        }
    )
    canonical, _, conflicts, _ = deduplicate_orders([first, later])
    assert len(canonical) == 1
    assert not conflicts


def test_status_only_conflict_is_lifecycle_and_transition_is_detected() -> None:
    result = analyze(
        conflict(
            "O-1",
            ["operational_status"],
            {"operational_status": "PENDING"},
            {"operational_status": "DELIVERED"},
        )
    )
    assert result.operational_only == 1
    assert result.potential_lifecycle_updates == 1
    assert category_count(result, ConflictCategory.LIKELY_LIFECYCLE_UPDATE) == 1
    assert result.status_transitions[0].previous_status == "PENDING"
    assert result.status_transitions[0].later_status == "DELIVERED"
    assert result.week_pairs[0].earlier_week == "2026-W01"


def test_cancellation_reason_enrichment_is_only_potentially_auto_resolvable() -> None:
    result = analyze(
        conflict(
            "O-1",
            ["cancellation_reason"],
            {"cancellation_reason": None},
            {"cancellation_reason": "Restaurant closed"},
        )
    )
    assert result.potential_auto_resolvable == 1
    assert category_count(result, ConflictCategory.AUTO_RESOLVABLE) == 1
    assert result.total_conflicting_order_ids == 1


def test_financial_identity_and_mixed_conflicts_are_classified() -> None:
    result = analyze(
        conflict("F", ["item_total"], {"item_total": "10"}, {"item_total": "12"}),
        conflict(
            "I",
            ["restaurant_id"],
            {"restaurant_id": "R1"},
            {"restaurant_id": "R2"},
        ),
        conflict(
            "M",
            ["operational_status", "item_total"],
            {"operational_status": "PENDING", "item_total": "10"},
            {"operational_status": "DELIVERED", "item_total": "20"},
        ),
    )
    assert result.financial_conflicts == 2
    assert result.identity_conflicts == 1
    assert result.mixed_conflicts == 1
    metric = next(item for item in result.financial_differences if item.field == "item_total")
    assert metric.max_absolute_difference == 10
    assert any(item.band == "1 < Δ ≤ 10 MAD" for item in result.financial_bands)
    assert result.potential_auto_resolvable == 0


def test_sub_cent_financial_difference_is_only_a_potential_resolution() -> None:
    result = analyze(
        conflict(
            "F",
            ["commission_amount"],
            {"commission_amount": "10.000000000000001"},
            {"commission_amount": "10.000000000000002"},
        )
    )
    assert result.financial_conflicts == 1
    assert result.potential_auto_resolvable == 1
    assert result.manual_review_required == 0
    assert category_count(result, ConflictCategory.AUTO_RESOLVABLE) == 1


def test_formatting_only_status_is_reported_but_not_resolved() -> None:
    result = analyze(
        conflict(
            "O-1",
            ["operational_status"],
            {"operational_status": " Delivered "},
            {"operational_status": "DELIVERED"},
        )
    )
    assert result.technical_formatting_only == 1
    assert result.potential_auto_resolvable == 1
    assert result.total_conflicting_order_ids == 1


def test_invalid_numeric_pattern_grouping_and_safe_formatting() -> None:
    assert invalid_numeric_pattern("") == "EMPTY"
    assert invalid_numeric_pattern("—") == "DASH"
    assert invalid_numeric_pattern("#VALUE!") == "FORMULA_OR_ERROR"
    assert invalid_numeric_pattern("MAD 125.00") == "CURRENCY_FORMATTED"
    assert invalid_numeric_pattern("1.25e-14") == "SCIENTIFIC_NOTATION"
    assert potentially_safe_numeric("MAD 125.00") is True
    assert potentially_safe_numeric("1.25e-14") is True
    assert potentially_safe_numeric("error") is False
