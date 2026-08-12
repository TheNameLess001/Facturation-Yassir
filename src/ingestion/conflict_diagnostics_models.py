from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConflictCategory(StrEnum):
    AUTO_RESOLVABLE = "AUTO_RESOLVABLE"
    LIKELY_LIFECYCLE_UPDATE = "LIKELY_LIFECYCLE_UPDATE"
    FINANCIAL_CONFLICT = "FINANCIAL_CONFLICT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    SOURCE_DATA_ERROR = "SOURCE_DATA_ERROR"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class SourceBehavior(StrEnum):
    INDEPENDENT = "INDEPENDENT"
    CUMULATIVE = "CUMULATIVE"
    ROLLING = "ROLLING"
    MIXED = "MIXED"


class CountMetric(DiagnosticModel):
    label: str
    count: int


class StatusTransitionMetric(DiagnosticModel):
    previous_status: str
    later_status: str
    count: int


class WeekPairMetric(DiagnosticModel):
    earlier_week: str
    later_week: str
    count: int


class FinancialDifferenceMetric(DiagnosticModel):
    field: str
    conflict_count: int
    measurable_count: int
    median_absolute_difference: float | None = None
    mean_absolute_difference: float | None = None
    max_absolute_difference: float | None = None


class FinancialBandMetric(DiagnosticModel):
    field: str
    band: str
    count: int


class RestaurantImpactMetric(DiagnosticModel):
    restaurant_id: str
    restaurant_name: str | None = None
    conflicting_order_ids: int
    total_observed_order_ids: int
    conflict_rate: float


class InvalidFinancialMetric(DiagnosticModel):
    canonical_field: str
    source_column: str | None = None
    source_filename: str | None = None
    source_week: int | None = None
    pattern: str
    count: int
    potentially_safe: int


class ConflictDiagnostics(DiagnosticModel):
    total_conflicting_order_ids: int
    operational_only: int
    financial_conflicts: int
    identity_conflicts: int
    mixed_conflicts: int
    technical_formatting_only: int
    technical_lineage_conflicts: int
    potential_lifecycle_updates: int
    potential_auto_resolvable: int
    manual_review_required: int
    restaurants_impacted: int
    same_week_conflicts: int
    adjacent_week_conflicts: int
    separated_week_conflicts: int
    invalid_financial_values: int
    potentially_safe_invalid_values: int
    still_invalid_values: int
    source_behavior: SourceBehavior
    field_counts: tuple[CountMetric, ...] = ()
    field_combinations: tuple[CountMetric, ...] = ()
    category_counts: tuple[CountMetric, ...] = ()
    status_transitions: tuple[StatusTransitionMetric, ...] = ()
    week_pairs: tuple[WeekPairMetric, ...] = ()
    financial_differences: tuple[FinancialDifferenceMetric, ...] = ()
    financial_bands: tuple[FinancialBandMetric, ...] = ()
    restaurant_impact: tuple[RestaurantImpactMetric, ...] = ()
    invalid_financial_patterns: tuple[InvalidFinancialMetric, ...] = ()
