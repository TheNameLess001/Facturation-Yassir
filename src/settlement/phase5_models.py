from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.enums import FinancialDecision


class Phase5Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SettlementHalf(StrEnum):
    P1 = "P1"
    P2 = "P2"


class SettlementPeriodStatus(StrEnum):
    COMPLETE = "COMPLETE"
    OPEN_INCOMPLETE = "OPEN_INCOMPLETE"
    FUTURE = "FUTURE"


class SettlementPeriod(Phase5Model):
    year: int = Field(ge=2000, le=9999)
    month: int = Field(ge=1, le=12)
    half: SettlementHalf
    start_date: date
    end_date: date
    start_at: datetime
    end_at: datetime
    period_code: str
    display_name: str
    status: SettlementPeriodStatus

    @model_validator(mode="after")
    def validate_period(self) -> SettlementPeriod:
        if self.end_date < self.start_date:
            raise ValueError("Settlement period end must follow its start")
        if self.end_at <= self.start_at:
            raise ValueError("Settlement period end timestamp must follow its start")
        expected = f"{self.year:04d}-{self.month:02d}-{self.half.value}"
        if self.period_code != expected:
            raise ValueError("Settlement period code does not match its dates")
        return self

    @property
    def period_id(self) -> str:
        """Backward-compatible identifier used by the Phase 1 boundaries."""
        return self.period_code


class OperationalClassification(StrEnum):
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class CancellationResponsibility(StrEnum):
    RESTAURANT = "RESTAURANT"
    CUSTOMER = "CUSTOMER"
    COURIER = "COURIER"
    YASSIR = "YASSIR"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RestaurantSettlementStatus(StrEnum):
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED_IDENTITY = "BLOCKED_IDENTITY"
    BLOCKED_COMMISSION = "BLOCKED_COMMISSION"
    BLOCKED_DATA = "BLOCKED_DATA"
    NO_ORDERS = "NO_ORDERS"


class CommissionResolutionStatus(StrEnum):
    MATCH = "MATCH"
    SCOPE_ONLY = "SCOPE_ONLY"
    RST_ONLY = "RST_ONLY"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"


class CommissionResolution(Phase5Model):
    scope_commission: Decimal | None = None
    rst_commission: Decimal | None = None
    difference: Decimal | None = None
    status: CommissionResolutionStatus
    effective_commission: Decimal | None = None
    resolution_source: str
    potential_financial_impact: Decimal | None = None


class SettlementDecisionTrace(Phase5Model):
    decision: FinancialDecision
    decision_rule: str
    source_fields_used: dict[str, str | None]
    created_at: datetime
    engine_version: str


class SettlementOrder(Phase5Model):
    order_id: str
    restaurant_id: str
    restaurant_name: str | None = None
    order_date: date
    source_order_status: str | None = None
    cancellation_reason: str | None = None
    financial_classification: OperationalClassification
    cancellation_responsibility: CancellationResponsibility
    financial_decision: FinancialDecision
    final_financial_decision: FinancialDecision
    manual_override_applied: bool = False
    latest_override_id: str | None = None
    decision_trace: SettlementDecisionTrace
    order_amount: Decimal | None = None
    order_amount_source_field: str = "item_total"
    item_total: Decimal | None = None
    promo_amount: Decimal | None = None
    delivery_fee: Decimal | None = None
    source_commission_amount: Decimal | None = None
    commission_base: Decimal | None = None
    issue_codes: tuple[str, ...] = ()

    @property
    def system_financial_decision(self) -> FinancialDecision:
        return self.financial_decision


class IdentityBlockedDiagnostic(Phase5Model):
    blocked_restaurants: int
    blocked_order_count: int
    blocked_gmv: Decimal
    unresolved_amount_rows: int = 0


class RestaurantSettlementEvaluation(Phase5Model):
    period_code: str
    restaurant_id: str
    restaurant_name: str | None = None
    commission_rate: Decimal | None = None
    invoice_scope_commission_rate: Decimal | None = None
    rst_commission_rate: Decimal | None = None
    commission_resolution: CommissionResolution
    total_orders: int
    delivered_orders: int
    cancelled_orders: int
    manual_review_orders: int
    pay_partner_orders: int
    excluded_orders: int
    yassir_compensation_orders: int
    gross_order_value: Decimal
    eligible_partner_amount: Decimal
    excluded_amount: Decimal
    compensation_amount: Decimal
    sales_ttc: Decimal | None = None
    sales_ht: Decimal | None = None
    commission_base: Decimal | None = None
    commission_amount: Decimal | None = None
    invoice_ht: Decimal | None = None
    invoice_tva: Decimal | None = None
    invoice_ttc: Decimal | None = None
    disbursement_note: Decimal | None = None
    net_payable: Decimal | None = None
    financial_policy_version: str | None = None
    settlement_status: RestaurantSettlementStatus
    issue_codes: tuple[str, ...] = ()
    orders: tuple[SettlementOrder, ...] = ()


class StatusCount(Phase5Model):
    value: str
    count: int = Field(ge=0)


class StatusReasonCount(Phase5Model):
    operational_status: str
    cancellation_reason: str
    count: int = Field(ge=0)


class AdminStatusProfile(Phase5Model):
    operational_statuses: tuple[StatusCount, ...]
    cancellation_fields: tuple[str, ...]
    cancellation_reasons: tuple[StatusCount, ...]
    status_reason_counts: tuple[StatusReasonCount, ...]


class MoneyReconciliation(Phase5Model):
    field: str
    source_total: Decimal
    classified_total: Decimal
    difference: Decimal
    blocking_rows: int = Field(default=0, ge=0)


class SettlementEvaluationEvent(Phase5Model):
    event_type: str
    period_code: str
    occurred_at: datetime
    engine_version: str
    details: dict[str, int | str]


class LegacyCalculationPolicy(Phase5Model):
    identified: bool
    source_reference: str
    authoritative: bool
    prototype_formulas: dict[str, str] = Field(default_factory=dict)
    unavailable_outputs: tuple[str, ...] = ()
    note: str

    @classmethod
    def repository_audit_result(cls) -> LegacyCalculationPolicy:
        return cls(
            identified=True,
            source_reference="4_Generateur bulk.py · business-owner approved",
            authoritative=True,
            prototype_formulas={
                "sales_ttc": "Item total",
                "sales_ht": "sales_ttc / 1.2",
                "commission_ht": "sales_ht * normalized commission rate",
                "tva": "commission_ht * 0.20",
                "invoice_ttc": "commission_ht + tva",
                "net_payable": "sales_ttc - invoice_ttc",
            },
            unavailable_outputs=(),
            note=(
                "Monetary policy cashco_legacy_v1 reproduces the explicitly approved "
                "legacy production generator without intermediate rounding."
            ),
        )


class SettlementSummary(Phase5Model):
    period: SettlementPeriod
    generated_at: datetime
    engine_version: str
    identity_ready_restaurants: int
    identity_blocked_restaurants: int
    canonical_orders_in_period: int
    invoice_scope_orders: int
    settlement_evaluated_orders: int
    identity_blocked_orders: int
    outside_invoice_scope_orders: int
    pay_partner_orders: int
    excluded_orders: int
    yassir_compensation_orders: int
    manual_review_orders: int
    unknown_statuses: int
    unknown_cancellation_responsibilities: int
    commission_mismatches: int
    invalid_financial_rows: int
    overrides_applied: int = 0
    restaurants: tuple[RestaurantSettlementEvaluation, ...]
    identity_blocked: IdentityBlockedDiagnostic
    status_profile: AdminStatusProfile
    money_reconciliation: tuple[MoneyReconciliation, ...]
    legacy_policy: LegacyCalculationPolicy
    audit_events: tuple[SettlementEvaluationEvent, ...]

    @property
    def restaurants_with_orders(self) -> int:
        return sum(item.total_orders > 0 for item in self.restaurants)

    @property
    def no_orders_restaurants(self) -> int:
        return sum(
            item.settlement_status == RestaurantSettlementStatus.NO_ORDERS
            for item in self.restaurants
        )

    def restaurant_status_count(self, status: RestaurantSettlementStatus) -> int:
        return sum(item.settlement_status == status for item in self.restaurants)
