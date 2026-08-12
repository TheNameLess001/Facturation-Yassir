from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from src.models.enums import FinancialDecision
from src.settlement.phase5_models import (
    CancellationResponsibility,
    OperationalClassification,
    SettlementDecisionTrace,
)

ENGINE_VERSION = "cashco-phase5-1.0"

DELIVERED_STATUSES = frozenset({"DELIVERED", "COMPLETED"})
CANCELLED_STATUSES = frozenset(
    {
        "CANCELLED",
        "CANCELED",
        "CANCELLED_BY_USER",
        "CANCELED_BY_USER",
        "CANCELLED_BY_ADMIN",
        "CANCELED_BY_ADMIN",
        "RESTAURANT_REJECTED",
    }
)

# These are controlled, exact normalized values observed in the real Admin
# Earnings source. Unlisted free text remains UNKNOWN; there is no fuzzy rule.
RESTAURANT_REASONS = frozenset(
    {
        "ORDER_TAKES_TOO_LONG_TO_BE_ACCEPTED",
        "RESTAURANT_FERME",
        "RESTAURANT_CLOSED",
        "STORE_CLOSED",
        "RESTO_FERME",
        "PRODUIT_INDISPONIBLE",
        "PRODUCT_UNAVAILABLE",
        "OUT_OF_STOCK",
        "CANCELLED_DUE_TO_MISSING_ITEMS",
        "MISSING_ITEMS",
        "PARTENARIAT_SUSPENDU",
        "HORS_SHIFT",
        "PREPARATION_DELAY",
        "RETARD_PREPARATION",
        "RETARD_DE_PREPARATION",
    }
)
CUSTOMER_REASONS = frozenset(
    {
        "CLIENT_UNREACHABLE",
        "CLIENT_INJOIGNABLE",
        "CUSTOMER_UNREACHABLE",
        "I_WANT_TO_CHANGE_THE_PAYMENT_METHOD",
        "INCORRECT_PAYMENT_METHOD_SELECTED",
        "I_HAVE_A_PERSONAL_INCIDENT",
        "ORDER_PLACED_ACCIDENTALLY",
        "ORDER_MADE_BY_MISTAKE",
        "ORDER_CREATED_BY_MISTAKE",
        "DUPLICATE_ORDER",
        "FRAUDULENT_ORDER",
        "UNRELIABLE_CUSTOMER",
        "CUSTOMER_CHANGED_LOCATION",
        "I_M_GOING_TO_ORDER_FROM_ANOTHER_RESTAURANT",
    }
)
COURIER_REASONS = frozenset(
    {
        "ORDER_TAKES_TOO_LONG_TO_BE_DELIVERED",
        "ISSUE_DRIVER_AFTER_PICKUP",
        "LIVREUR_INJOIGNABLE",
        "DRIVER_HAD_AN_ACCIDENT",
        "LIVREUR_A_EU_UN_ACCIDENT",
    }
)
YASSIR_REASONS = frozenset(
    {
        "NO_AVAILABLE_COURIER",
        "NO_COURIER_AVAILABLE",
        "ORDER_TAKES_TOO_LONG_TO_BE_ASSIGNED",
        "ORDER_TAKES_TOO_LONG_TO_BE_ASSIGNED_",
        "SYSTEM_UPDATE",
    }
)


class FinancialRuleOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classification: OperationalClassification
    responsibility: CancellationResponsibility
    decision: FinancialDecision
    rule: str

    def trace(
        self,
        source_status: str | None,
        cancellation_reason: str | None,
        *,
        created_at: datetime | None = None,
    ) -> SettlementDecisionTrace:
        return SettlementDecisionTrace(
            decision=self.decision,
            decision_rule=self.rule,
            source_fields_used={
                "operational_status": source_status,
                "cancellation_reason": cancellation_reason,
            },
            created_at=created_at or datetime.now(UTC),
            engine_version=ENGINE_VERSION,
        )


class FinancialEligibilityRuleEngine:
    """Conservative Phase 5 classification with no fuzzy responsibility inference."""

    def classify(
        self,
        source_status: str | None,
        cancellation_reason: str | None,
    ) -> FinancialRuleOutcome:
        status = normalize_financial_text(source_status)
        reason = normalize_financial_text(cancellation_reason)
        if status in DELIVERED_STATUSES:
            return FinancialRuleOutcome(
                classification=OperationalClassification.DELIVERED,
                responsibility=CancellationResponsibility.NOT_APPLICABLE,
                decision=FinancialDecision.PAY_PARTNER,
                rule="DELIVERED_ORDER",
            )
        if status not in CANCELLED_STATUSES:
            classification = (
                OperationalClassification.UNKNOWN
                if not status
                else OperationalClassification.OTHER
            )
            return FinancialRuleOutcome(
                classification=classification,
                responsibility=CancellationResponsibility.NOT_APPLICABLE,
                decision=FinancialDecision.MANUAL_REVIEW,
                rule=(
                    "UNKNOWN_ORDER_STATUS"
                    if classification == OperationalClassification.UNKNOWN
                    else "UNCONFIGURED_OPERATIONAL_STATUS"
                ),
            )

        responsibility = self._cancellation_responsibility(status, reason)
        if responsibility == CancellationResponsibility.RESTAURANT:
            decision = FinancialDecision.EXCLUDE
            rule = "RESTAURANT_CANCELLATION"
        elif responsibility == CancellationResponsibility.YASSIR:
            decision = FinancialDecision.YASSIR_COMPENSATION
            rule = "YASSIR_PLATFORM_CANCELLATION"
        elif responsibility == CancellationResponsibility.CUSTOMER:
            decision = FinancialDecision.MANUAL_REVIEW
            rule = "CUSTOMER_CANCELLATION_REQUIRES_REVIEW"
        elif responsibility == CancellationResponsibility.COURIER:
            decision = FinancialDecision.MANUAL_REVIEW
            rule = "COURIER_CANCELLATION_REQUIRES_REVIEW"
        else:
            decision = FinancialDecision.MANUAL_REVIEW
            rule = "UNKNOWN_CANCELLATION_RESPONSIBILITY"
        return FinancialRuleOutcome(
            classification=OperationalClassification.CANCELLED,
            responsibility=responsibility,
            decision=decision,
            rule=rule,
        )

    @staticmethod
    def _cancellation_responsibility(
        status: str,
        reason: str,
    ) -> CancellationResponsibility:
        if status == "RESTAURANT_REJECTED":
            return CancellationResponsibility.RESTAURANT
        if status in {"CANCELLED_BY_USER", "CANCELED_BY_USER"}:
            return CancellationResponsibility.CUSTOMER
        if reason in RESTAURANT_REASONS:
            return CancellationResponsibility.RESTAURANT
        if reason in CUSTOMER_REASONS:
            return CancellationResponsibility.CUSTOMER
        if reason in COURIER_REASONS:
            return CancellationResponsibility.COURIER
        if reason in YASSIR_REASONS:
            return CancellationResponsibility.YASSIR
        return CancellationResponsibility.UNKNOWN


def normalize_financial_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")
