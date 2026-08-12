from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from src.ingestion.admin_earnings_models import IngestionIssue
from src.models.domain import Order, Restaurant, RestaurantSettlement
from src.models.enums import AuditLevel, FinancialDecision, WorkflowState


class SettlementValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    state: WorkflowState
    issues: tuple[IngestionIssue, ...] = ()


class SettlementValidationService:
    def validate(
        self,
        settlement: RestaurantSettlement,
        orders: tuple[Order, ...],
        restaurant: Restaurant | None,
    ) -> SettlementValidationResult:
        issues: list[IngestionIssue] = []
        if restaurant is None:
            issues.append(
                self._issue(
                    "MISSING_RESTAURANT_MASTER", "Restaurant master is missing."
                )
            )
        else:
            for value, code, label in (
                (restaurant.legal_entity, "MISSING_LEGAL_ENTITY", "Legal entity"),
                (restaurant.ice, "MISSING_ICE", "ICE"),
                (restaurant.rib, "MISSING_RIB", "RIB"),
            ):
                if not value:
                    issues.append(
                        self._issue(code, f"{label} is required before validation.")
                    )
        if settlement.net_payable < 0:
            issues.append(
                self._issue("INVALID_NET_PAYABLE", "Net payable cannot be negative.")
            )
        if any(
            item.final_settlement_decision == FinancialDecision.MANUAL_REVIEW
            for item in orders
        ):
            issues.append(
                self._issue(
                    "UNRESOLVED_MANUAL_REVIEW",
                    "Every MANUAL_REVIEW order must be resolved before validation.",
                )
            )
        return SettlementValidationResult(
            valid=not issues,
            state=WorkflowState.VALIDATED if not issues else WorkflowState.BLOCKED,
            issues=tuple(issues),
        )

    @staticmethod
    def _issue(code: str, message: str) -> IngestionIssue:
        return IngestionIssue(level=AuditLevel.BLOCKING, code=code, message=message)
