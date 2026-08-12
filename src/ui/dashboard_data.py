from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.documents.phase8 import DocumentReadiness, DocumentReadinessStatus
from src.settlement.phase5_models import (
    RestaurantSettlementStatus,
    SettlementSummary,
)


class PeriodOperationalStatus(StrEnum):
    OPEN = "OPEN"
    REVIEW = "REVIEW"
    FINANCIALLY_READY = "FINANCIALLY_READY"
    DOCUMENTS_READY = "DOCUMENTS_READY"
    AUTHORIZED = "AUTHORIZED"
    SENT = "SENT"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class DashboardSnapshot:
    period_code: str
    restaurants_in_scope: int
    identity_ready: int
    settlement_ready: int
    orders_evaluated: int
    manual_review: int
    documents_ready: int
    identity_blockers: int
    commission_mismatches: int
    invalid_financial_rows: int
    missing_legal_data: int
    formula_validation_required: int
    overrides_applied: int
    period_status: PeriodOperationalStatus


@dataclass(frozen=True)
class PeriodTrendPoint:
    period_code: str
    orders_evaluated: int
    pay_partner_orders: int
    review_rate: float
    settlement_ready_restaurants: int


def dashboard_snapshot(
    summary: SettlementSummary,
    document_readiness: tuple[DocumentReadiness, ...],
) -> DashboardSnapshot:
    document_ready = sum(
        item.status == DocumentReadinessStatus.READY for item in document_readiness
    )
    missing_legal = sum(
        item.status == DocumentReadinessStatus.MISSING_LEGAL
        for item in document_readiness
    )
    formula_required = sum(
        "LEGACY_FORMULA_VALIDATION_REQUIRED" in item.issue_codes
        for item in document_readiness
    )
    settlement_ready = summary.restaurant_status_count(
        RestaurantSettlementStatus.READY
    )
    if summary.manual_review_orders or summary.invalid_financial_rows:
        period_status = PeriodOperationalStatus.REVIEW
    elif settlement_ready:
        period_status = (
            PeriodOperationalStatus.DOCUMENTS_READY
            if document_ready == settlement_ready
            else PeriodOperationalStatus.FINANCIALLY_READY
        )
    else:
        period_status = PeriodOperationalStatus.OPEN
    return DashboardSnapshot(
        period_code=summary.period.period_code,
        restaurants_in_scope=(
            summary.identity_ready_restaurants + summary.identity_blocked_restaurants
        ),
        identity_ready=summary.identity_ready_restaurants,
        settlement_ready=settlement_ready,
        orders_evaluated=summary.settlement_evaluated_orders,
        manual_review=summary.manual_review_orders,
        documents_ready=document_ready,
        identity_blockers=summary.identity_blocked_restaurants,
        commission_mismatches=summary.commission_mismatches,
        invalid_financial_rows=summary.invalid_financial_rows,
        missing_legal_data=missing_legal,
        formula_validation_required=formula_required,
        overrides_applied=summary.overrides_applied,
        period_status=period_status,
    )


def period_trend(summaries: tuple[SettlementSummary, ...]) -> tuple[PeriodTrendPoint, ...]:
    return tuple(
        PeriodTrendPoint(
            period_code=item.period.period_code,
            orders_evaluated=item.settlement_evaluated_orders,
            pay_partner_orders=item.pay_partner_orders,
            review_rate=(
                item.manual_review_orders / item.settlement_evaluated_orders
                if item.settlement_evaluated_orders
                else 0.0
            ),
            settlement_ready_restaurants=item.restaurant_status_count(
                RestaurantSettlementStatus.READY
            ),
        )
        for item in sorted(summaries, key=lambda value: value.period.period_code)
    )


def settlement_progress(
    summary: SettlementSummary,
    document_readiness: tuple[DocumentReadiness, ...],
) -> tuple[tuple[str, int], ...]:
    financially_ready = summary.restaurant_status_count(
        RestaurantSettlementStatus.READY
    )
    return (
        (
            "Scope",
            summary.identity_ready_restaurants + summary.identity_blocked_restaurants,
        ),
        ("Identity Ready", summary.identity_ready_restaurants),
        ("Orders Found", summary.restaurants_with_orders),
        ("Settlement Evaluated", summary.restaurants_with_orders),
        ("Financially Ready", financially_ready),
        (
            "Documents Ready",
            sum(
                item.status == DocumentReadinessStatus.READY
                for item in document_readiness
            ),
        ),
        ("Email Ready", 0),
    )
