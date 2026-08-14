from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from src.ingestion.admin_earnings_normalizer import (
    normalize_decimal,
    normalize_identifier,
)
from src.models.enums import FinancialDecision
from src.restaurants.registry_models import (
    RegisteredRestaurant,
    RestaurantRegistryResult,
)
from src.settlement.certified_calculator import CertifiedFinancialCalculator
from src.settlement.financial_rules import (
    ENGINE_VERSION,
    FinancialEligibilityRuleEngine,
)
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.overrides import FinancialOverride, latest_overrides
from src.settlement.phase5_models import (
    AdminStatusProfile,
    CancellationResponsibility,
    CommissionResolution,
    CommissionResolutionStatus,
    IdentityBlockedDiagnostic,
    LegacyCalculationPolicy,
    MoneyReconciliation,
    OperationalClassification,
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
    SettlementEvaluationEvent,
    SettlementOrder,
    SettlementPeriod,
    SettlementSummary,
    StatusCount,
    StatusReasonCount,
)

ZERO = Decimal(0)
COMMISSION_TOLERANCE = Decimal("0.000001")


class Phase5SettlementService:
    """Evaluate canonical orders without mutating any source or persisting decisions."""

    def __init__(self) -> None:
        self.rules = FinancialEligibilityRuleEngine()
        self.legacy_policy = LegacyCalculationPolicy.repository_audit_result()
        self.formula_registry = LegacyFormulaRegistry()
        self.certified_policy = self.formula_registry.active_policy()
        self.formula_certification = self.formula_registry.certification()

    def evaluate(
        self,
        period: SettlementPeriod,
        canonical_orders: pd.DataFrame,
        registry: RestaurantRegistryResult,
        *,
        invalid_financial_issues: pd.DataFrame | None = None,
        overrides: tuple[FinancialOverride, ...] = (),
        evaluated_at: datetime | None = None,
    ) -> SettlementSummary:
        now = evaluated_at or datetime.now(UTC)
        frame = self._canonical_period(canonical_orders, period)
        invalid_by_order = self._invalid_issue_map(invalid_financial_issues)
        status_profile = self._status_profile(canonical_orders)
        override_by_order = latest_overrides(
            tuple(item for item in overrides if item.period_code == period.period_code)
        )

        ready_restaurants = tuple(registry.identity_ready_restaurants)
        blocked_restaurants = tuple(registry.identity_blocked_restaurants)
        blocked_ids = {
            item.restaurant_id
            for item in blocked_restaurants
            if item.restaurant_id is not None
        }
        ready_by_id: dict[str, RegisteredRestaurant] = {}
        duplicate_ready_ids: set[str] = set()
        for restaurant in ready_restaurants:
            if restaurant.restaurant_id is None:
                continue
            if restaurant.restaurant_id in ready_by_id:
                duplicate_ready_ids.add(restaurant.restaurant_id)
            else:
                ready_by_id[restaurant.restaurant_id] = restaurant

        orders_by_restaurant: dict[str, list[SettlementOrder]] = defaultdict(list)
        blocked_order_count = 0
        blocked_gmv = ZERO
        blocked_unresolved_amounts = 0
        outside_scope_orders = 0
        evaluated_orders: list[SettlementOrder] = []
        invoice_scope_orders = 0
        for row in frame.to_dict(orient="records"):
            restaurant_id = normalize_identifier(row.get("restaurant_id"))
            amount = self._decimal(row.get("item_total"))
            if restaurant_id in blocked_ids:
                invoice_scope_orders += 1
                blocked_order_count += 1
                if amount is None:
                    blocked_unresolved_amounts += 1
                else:
                    blocked_gmv += amount
                continue
            if restaurant_id not in ready_by_id:
                outside_scope_orders += 1
                continue
            invoice_scope_orders += 1
            settlement_order = self._settlement_order(
                row,
                restaurant_id,
                invalid_by_order,
                now,
                override_by_order.get(normalize_identifier(row.get("order_id")) or ""),
            )
            orders_by_restaurant[restaurant_id].append(settlement_order)
            evaluated_orders.append(settlement_order)

        decision_counts = Counter(
            item.final_financial_decision for item in evaluated_orders
        )
        reconciled_count = sum(decision_counts.values())
        if reconciled_count != len(evaluated_orders):
            raise ValueError("Financial decision reconciliation failed")

        restaurant_results = tuple(
            self._restaurant_settlement(
                period,
                restaurant,
                tuple(orders_by_restaurant.get(restaurant.restaurant_id or "", ())),
                duplicate_ready_ids,
            )
            for restaurant in ready_restaurants
        )
        source_total = self._sum_amounts(evaluated_orders)
        classified_total = sum(
            (
                item.order_amount
                for item in evaluated_orders
                if item.order_amount is not None
            ),
            ZERO,
        )
        invalid_rows = sum(
            any(
                code.startswith("INVALID_FINANCIAL_VALUE")
                or code == "INVALID_ORDER_AMOUNT"
                for code in item.issue_codes
            )
            for item in evaluated_orders
        )
        unknown_statuses = sum(
            item.financial_classification
            in {OperationalClassification.OTHER, OperationalClassification.UNKNOWN}
            for item in evaluated_orders
        )
        unknown_responsibilities = sum(
            item.cancellation_responsibility == CancellationResponsibility.UNKNOWN
            for item in evaluated_orders
        )
        commission_mismatches = sum(
            "COMMISSION_MISMATCH" in item.issue_codes
            for item in restaurant_results
        )
        event = SettlementEvaluationEvent(
            event_type="SETTLEMENT_PERIOD_EVALUATED",
            period_code=period.period_code,
            occurred_at=now,
            engine_version=ENGINE_VERSION,
            details={
                "canonical_orders_in_period": len(frame),
                "settlement_evaluated_orders": len(evaluated_orders),
                "identity_blocked_orders": blocked_order_count,
                "manual_review_orders": decision_counts[FinancialDecision.MANUAL_REVIEW],
            },
        )
        return SettlementSummary(
            period=period,
            generated_at=now,
            engine_version=ENGINE_VERSION,
            identity_ready_restaurants=len(ready_restaurants),
            identity_blocked_restaurants=len(blocked_restaurants),
            canonical_orders_in_period=len(frame),
            invoice_scope_orders=invoice_scope_orders,
            settlement_evaluated_orders=len(evaluated_orders),
            identity_blocked_orders=blocked_order_count,
            outside_invoice_scope_orders=outside_scope_orders,
            pay_partner_orders=decision_counts[FinancialDecision.PAY_PARTNER],
            excluded_orders=decision_counts[FinancialDecision.EXCLUDE],
            yassir_compensation_orders=decision_counts[
                FinancialDecision.YASSIR_COMPENSATION
            ],
            manual_review_orders=decision_counts[FinancialDecision.MANUAL_REVIEW],
            unknown_statuses=unknown_statuses,
            unknown_cancellation_responsibilities=unknown_responsibilities,
            commission_mismatches=commission_mismatches,
            invalid_financial_rows=invalid_rows,
            overrides_applied=sum(
                item.manual_override_applied for item in evaluated_orders
            ),
            restaurants=restaurant_results,
            identity_blocked=IdentityBlockedDiagnostic(
                blocked_restaurants=len(blocked_restaurants),
                blocked_order_count=blocked_order_count,
                blocked_gmv=blocked_gmv,
                unresolved_amount_rows=blocked_unresolved_amounts,
            ),
            status_profile=status_profile,
            money_reconciliation=(
                MoneyReconciliation(
                    field="item_total",
                    source_total=source_total,
                    classified_total=classified_total,
                    difference=source_total - classified_total,
                    blocking_rows=sum(
                        item.order_amount is None for item in evaluated_orders
                    ),
                ),
            ),
            legacy_policy=self.legacy_policy,
            audit_events=(event,),
        )

    def _settlement_order(
        self,
        row: dict[str, object],
        restaurant_id: str,
        invalid_by_order: dict[str, tuple[str, ...]],
        now: datetime,
        override: FinancialOverride | None,
    ) -> SettlementOrder:
        order_id = normalize_identifier(row.get("order_id"))
        if order_id is None:
            raise ValueError("Canonical settlement order has no Order ID")
        order_date = self._date(row.get("order_date"))
        if order_date is None:
            raise ValueError("Canonical settlement order has no actual Order Date")
        status = self._text(row.get("operational_status"))
        cancellation_reason = self._text(row.get("cancellation_reason"))
        outcome = self.rules.classify(status, cancellation_reason)
        if override is not None and override.restaurant_id != restaurant_id:
            raise ValueError("Financial override Restaurant ID does not match order")
        final_decision = override.new_decision if override else outcome.decision
        amount = self._decimal(row.get("item_total"))
        issues = list(invalid_by_order.get(order_id, ()))
        if amount is None:
            issues.append("INVALID_ORDER_AMOUNT")
        if outcome.rule in {
            "UNKNOWN_ORDER_STATUS",
            "UNCONFIGURED_OPERATIONAL_STATUS",
            "UNKNOWN_CANCELLATION_RESPONSIBILITY",
        }:
            issues.append(outcome.rule)
        return SettlementOrder(
            order_id=order_id,
            restaurant_id=restaurant_id,
            restaurant_name=self._text(row.get("restaurant_name")),
            order_date=order_date,
            source_order_status=status,
            cancellation_reason=cancellation_reason,
            financial_classification=outcome.classification,
            cancellation_responsibility=outcome.responsibility,
            financial_decision=outcome.decision,
            final_financial_decision=final_decision,
            manual_override_applied=override is not None,
            latest_override_id=str(override.override_id) if override else None,
            decision_trace=outcome.trace(status, cancellation_reason, created_at=now),
            order_amount=amount,
            item_total=amount,
            promo_amount=self._decimal(row.get("promo_amount")),
            delivery_fee=self._decimal(row.get("delivery_fee")),
            source_commission_amount=self._decimal(row.get("commission_amount")),
            commission_base=None,
            issue_codes=tuple(dict.fromkeys(issues)),
        )

    def _restaurant_settlement(
        self,
        period: SettlementPeriod,
        restaurant: RegisteredRestaurant,
        orders: tuple[SettlementOrder, ...],
        duplicate_ready_ids: set[str],
    ) -> RestaurantSettlementEvaluation:
        scope_rate, scope_rate_issue = normalize_commission_rate(
            restaurant.invoice_scope_commission_rate
        )
        rst_rate, rst_rate_issue = normalize_commission_rate(
            restaurant.rst_commission_rate
        )
        issues: list[str] = []
        if scope_rate_issue:
            issues.append(scope_rate_issue)
        if rst_rate_issue:
            issues.append("INVALID_RST_COMMISSION_REFERENCE")
        if scope_rate is None:
            issues.append("MISSING_INVOICE_SCOPE_COMMISSION")
        if (
            scope_rate is not None
            and rst_rate is not None
            and abs(scope_rate - rst_rate) > COMMISSION_TOLERANCE
        ):
            issues.append("COMMISSION_MISMATCH")
        if restaurant.restaurant_id in duplicate_ready_ids:
            issues.append("DUPLICATE_READY_RESTAURANT_ID")
        for order in orders:
            issues.extend(order.issue_codes)
        unique_issues = tuple(dict.fromkeys(issues))
        decisions = Counter(item.final_financial_decision for item in orders)
        classifications = Counter(item.financial_classification for item in orders)
        gross = self._sum_amounts(orders)
        eligible = self._sum_decision(orders, FinancialDecision.PAY_PARTNER)
        excluded = self._sum_decision(orders, FinancialDecision.EXCLUDE)
        compensation = self._sum_decision(
            orders, FinancialDecision.YASSIR_COMPENSATION
        )
        commission_resolution = resolve_commission(
            scope_rate,
            rst_rate,
            None,
        )
        if not orders:
            status = RestaurantSettlementStatus.NO_ORDERS
        elif any(
            code
            in {
                "MISSING_INVOICE_SCOPE_COMMISSION",
                "INVALID_INVOICE_SCOPE_COMMISSION",
            }
            for code in unique_issues
        ):
            status = RestaurantSettlementStatus.BLOCKED_COMMISSION
        elif any(
            code.startswith("INVALID_") or code == "DUPLICATE_READY_RESTAURANT_ID"
            for code in unique_issues
        ):
            status = RestaurantSettlementStatus.BLOCKED_DATA
        elif decisions[FinancialDecision.MANUAL_REVIEW]:
            status = RestaurantSettlementStatus.REVIEW_REQUIRED
        else:
            status = RestaurantSettlementStatus.READY
        evaluation = RestaurantSettlementEvaluation(
            period_code=period.period_code,
            restaurant_id=restaurant.restaurant_id or "",
            restaurant_name=restaurant.restaurant_name,
            commission_rate=scope_rate,
            invoice_scope_commission_rate=scope_rate,
            rst_commission_rate=rst_rate,
            commission_resolution=commission_resolution,
            total_orders=len(orders),
            delivered_orders=classifications[OperationalClassification.DELIVERED],
            cancelled_orders=classifications[OperationalClassification.CANCELLED],
            manual_review_orders=decisions[FinancialDecision.MANUAL_REVIEW],
            pay_partner_orders=decisions[FinancialDecision.PAY_PARTNER],
            excluded_orders=decisions[FinancialDecision.EXCLUDE],
            yassir_compensation_orders=decisions[
                FinancialDecision.YASSIR_COMPENSATION
            ],
            gross_order_value=gross,
            eligible_partner_amount=eligible,
            excluded_amount=excluded,
            compensation_amount=compensation,
            commission_base=None,
            commission_amount=None,
            invoice_ht=None,
            invoice_tva=None,
            invoice_ttc=None,
            disbursement_note=None,
            net_payable=None,
            settlement_status=status,
            issue_codes=unique_issues,
            orders=orders,
        )
        if status == RestaurantSettlementStatus.READY:
            return CertifiedFinancialCalculator().calculate(
                evaluation,
                certification=self.formula_certification,
                policy=self.certified_policy,
            )
        return evaluation

    @staticmethod
    def _canonical_period(
        frame: pd.DataFrame,
        period: SettlementPeriod,
    ) -> pd.DataFrame:
        required = {"order_id", "restaurant_id", "order_date", "item_total"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(
                f"Canonical orders are missing required fields: {', '.join(sorted(missing))}"
            )
        order_dates = pd.to_datetime(frame["order_date"], errors="coerce").dt.date
        mask = order_dates.between(period.start_date, period.end_date)
        selected = frame.loc[mask].copy()
        selected["order_date"] = order_dates.loc[mask]
        return selected

    @staticmethod
    def _invalid_issue_map(
        issues: pd.DataFrame | None,
    ) -> dict[str, tuple[str, ...]]:
        if issues is None or issues.empty or "category" not in issues.columns:
            return {}
        relevant = issues.loc[issues["category"] == "INVALID_FINANCIAL_VALUE"]
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in relevant.to_dict(orient="records"):
            order_id = normalize_identifier(row.get("order_id"))
            if order_id is None:
                continue
            field = str(row.get("field") or "unknown")
            grouped[order_id].append(f"INVALID_FINANCIAL_VALUE:{field}")
        return {
            order_id: tuple(dict.fromkeys(codes))
            for order_id, codes in grouped.items()
        }

    @staticmethod
    def _status_profile(frame: pd.DataFrame) -> AdminStatusProfile:
        statuses = (
            frame.get("operational_status", pd.Series(dtype=object))
            .fillna("<NULL>")
            .astype(str)
        )
        reasons = (
            frame.get("cancellation_reason", pd.Series(dtype=object))
            .fillna("<NULL>")
            .astype(str)
        )
        status_counts = statuses.value_counts()
        reason_counts = reasons.value_counts()
        combinations = (
            pd.DataFrame(
                {"operational_status": statuses, "cancellation_reason": reasons}
            )
            .value_counts()
            .reset_index(name="count")
        )
        return AdminStatusProfile(
            operational_statuses=tuple(
                StatusCount(value=str(value), count=int(count))
                for value, count in status_counts.items()
            ),
            cancellation_fields=("cancellation_reason",),
            cancellation_reasons=tuple(
                StatusCount(value=str(value), count=int(count))
                for value, count in reason_counts.items()
            ),
            status_reason_counts=tuple(
                StatusReasonCount(
                    operational_status=str(row.operational_status),
                    cancellation_reason=str(row.cancellation_reason),
                    count=int(row.count),
                )
                for row in combinations.itertuples(index=False)
            ),
        )

    @staticmethod
    def _sum_amounts(orders: list[SettlementOrder] | tuple[SettlementOrder, ...]) -> Decimal:
        return sum(
            (item.order_amount for item in orders if item.order_amount is not None),
            ZERO,
        )

    @staticmethod
    def _sum_decision(
        orders: tuple[SettlementOrder, ...],
        decision: FinancialDecision,
    ) -> Decimal:
        return sum(
            (
                item.order_amount
                for item in orders
                if item.final_financial_decision == decision
                and item.order_amount is not None
            ),
            ZERO,
        )

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        try:
            return normalize_decimal(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date(value: object) -> date | None:
        if value is None or pd.isna(value):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text or None


def normalize_commission_rate(
    value: Decimal | object | None,
) -> tuple[Decimal | None, str | None]:
    try:
        rate = normalize_decimal(value)
    except (TypeError, ValueError):
        return None, "INVALID_INVOICE_SCOPE_COMMISSION"
    if rate is None:
        return None, None
    if rate < 0 or rate > 100:
        return None, "INVALID_INVOICE_SCOPE_COMMISSION"
    if rate > 1:
        rate /= Decimal(100)
    return rate, None


def resolve_commission(
    scope_rate: Decimal | None,
    rst_rate: Decimal | None,
    financial_base: Decimal | None = None,
) -> CommissionResolution:
    if scope_rate is not None and rst_rate is not None:
        difference = scope_rate - rst_rate
        status = (
            CommissionResolutionStatus.MATCH
            if abs(difference) <= COMMISSION_TOLERANCE
            else CommissionResolutionStatus.MISMATCH
        )
        effective = scope_rate
        source = "INVOICE_SCOPE_AUTHORITY"
    elif scope_rate is not None:
        difference = None
        status = CommissionResolutionStatus.SCOPE_ONLY
        effective = scope_rate
        source = "INVOICE_SCOPE_AUTHORITY"
    elif rst_rate is not None:
        difference = None
        status = CommissionResolutionStatus.RST_ONLY
        effective = None
        source = "NO_AUTHORIZED_FALLBACK"
    else:
        difference = None
        status = CommissionResolutionStatus.MISSING
        effective = None
        source = "MISSING"
    impact = (
        abs(difference) * financial_base
        if difference is not None and financial_base is not None
        else None
    )
    return CommissionResolution(
        scope_commission=scope_rate,
        rst_commission=rst_rate,
        difference=difference,
        status=status,
        effective_commission=effective,
        resolution_source=source,
        potential_financial_impact=impact,
    )
