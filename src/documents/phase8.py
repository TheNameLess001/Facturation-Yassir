from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from src.restaurants.registry_models import RegisteredRestaurant
from src.settlement.certified_calculator import CertifiedFinancialCalculator
from src.settlement.legacy_validation import (
    FinancialFormulaCertification,
    LegacyCalculationPolicy,
    LegacyFormulaRegistry,
)
from src.settlement.phase5_models import (
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)


class CashCoDocumentType(StrEnum):
    INVOICE = "INVOICE"
    NOTE_DE_DEBOURS = "NOTE_DE_DEBOURS"
    PARTNER_STATEMENT = "PARTNER_STATEMENT"


class DocumentReadinessStatus(StrEnum):
    READY = "READY"
    MISSING_LEGAL = "MISSING_LEGAL"
    FINANCIAL_REVIEW = "FINANCIAL_REVIEW"
    FORMULA_NOT_VALIDATED = "FORMULA_NOT_VALIDATED"
    BLOCKED = "BLOCKED"
    GENERATED = "GENERATED"


class DocumentReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    status: DocumentReadinessStatus
    identity_ready: bool
    settlement_final: bool
    legal_ready: bool
    financial_formulas_validated: bool
    missing_legal_fields: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    potentially_eligible: bool = False


class DocumentPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    document_type: CashCoDocumentType
    version: int = Field(ge=1)
    document_key: str
    watermark: str
    generated_at: datetime
    readiness: DocumentReadiness
    content: dict[str, str | None]
    financial_policy_version: str | None = None


class Phase8DocumentEngine:
    LEGAL_FIELDS: ClassVar[dict[str, str]] = {
        "legal_entity": "Legal Entity",
        "ice": "ICE",
        "if_number": "IF",
        "rc": "RC",
        "address": "Address",
    }

    def __init__(
        self,
        formulas: LegacyFormulaRegistry | None = None,
        *,
        certification: FinancialFormulaCertification | None = None,
        policy: LegacyCalculationPolicy | None = None,
    ) -> None:
        self.formulas = formulas or LegacyFormulaRegistry()
        self.certification = certification or self.formulas.certification()
        self.policy = policy or self.formulas.active_policy()

    def financial_formulas_ready(self) -> bool:
        return bool(
            self.certification
            and self.certification.production_ready
            and self.policy is not None
        )

    def readiness(
        self,
        restaurant: RegisteredRestaurant,
        settlement: RestaurantSettlementEvaluation,
    ) -> DocumentReadiness:
        missing_legal = tuple(
            label
            for field, label in self.LEGAL_FIELDS.items()
            if not getattr(restaurant, field)
        )
        formula_ready = self.financial_formulas_ready()
        financial_review = (
            settlement.manual_review_orders > 0
            or settlement.settlement_status
            in {
                RestaurantSettlementStatus.REVIEW_REQUIRED,
                RestaurantSettlementStatus.BLOCKED_COMMISSION,
                RestaurantSettlementStatus.BLOCKED_DATA,
            }
        )
        valid_commission = (
            settlement.commission_resolution.effective_commission is not None
        )
        potentially_eligible = bool(
            settlement.total_orders
            and not financial_review
            and valid_commission
            and not missing_legal
        )
        issues: list[str] = []
        if financial_review:
            issues.append("FINANCIAL_REVIEW_REQUIRED")
        if not valid_commission:
            issues.append("COMMISSION_NOT_RESOLVED")
        issues.extend(f"MISSING_{item.upper().replace(' ', '_')}" for item in missing_legal)
        if not formula_ready:
            issues.append("LEGACY_FORMULA_VALIDATION_REQUIRED")
        if financial_review or not valid_commission:
            status = DocumentReadinessStatus.FINANCIAL_REVIEW
        elif missing_legal:
            status = DocumentReadinessStatus.MISSING_LEGAL
        elif not formula_ready:
            status = DocumentReadinessStatus.FORMULA_NOT_VALIDATED
        elif settlement.total_orders == 0:
            status = DocumentReadinessStatus.BLOCKED
        else:
            status = DocumentReadinessStatus.READY
        return DocumentReadiness(
            restaurant_id=settlement.restaurant_id,
            period_code=settlement.period_code,
            status=status,
            identity_ready=restaurant.readiness.identity_ready,
            settlement_final=not financial_review,
            legal_ready=not missing_legal,
            financial_formulas_validated=formula_ready,
            missing_legal_fields=missing_legal,
            issue_codes=tuple(issues),
            potentially_eligible=potentially_eligible,
        )

    def preview(
        self,
        document_type: CashCoDocumentType,
        restaurant: RegisteredRestaurant,
        settlement: RestaurantSettlementEvaluation,
        *,
        version: int = 1,
        generated_at: datetime | None = None,
    ) -> DocumentPreview:
        readiness = self.readiness(restaurant, settlement)
        financial_content = self._financial_content(settlement)
        watermark = (
            "DRAFT · NOT VALIDATED"
            if readiness.status != DocumentReadinessStatus.READY
            else "DRAFT"
        )
        return DocumentPreview(
            restaurant_id=settlement.restaurant_id,
            period_code=settlement.period_code,
            document_type=document_type,
            version=version,
            document_key=(
                f"{settlement.restaurant_id}:{settlement.period_code}:"
                f"{document_type.value}:v{version}"
            ),
            watermark=watermark,
            generated_at=generated_at or datetime.now(UTC),
            readiness=readiness,
            financial_policy_version=(
                self.policy.policy_version
                if self.financial_formulas_ready() and self.policy
                else None
            ),
            content={
                "partner": restaurant.restaurant_name,
                "restaurant_id": settlement.restaurant_id,
                "period": settlement.period_code,
                "legal_entity": restaurant.legal_entity,
                "ice": restaurant.ice,
                "if": restaurant.if_number,
                "rc": restaurant.rc,
                "address": restaurant.address,
                "commission": (
                    str(settlement.commission_resolution.effective_commission)
                    if settlement.commission_resolution.effective_commission is not None
                    else None
                ),
                "gross_order_value": str(settlement.gross_order_value),
                **financial_content,
            },
        )

    def _financial_content(
        self, settlement: RestaurantSettlementEvaluation
    ) -> dict[str, str | None]:
        empty = {
            "sales_ttc": None,
            "sales_ht": None,
            "commission_amount": None,
            "invoice_ht": None,
            "invoice_tva": None,
            "invoice_ttc": None,
            "note_de_debours": None,
            "final_net_payable": None,
        }
        if (
            not self.financial_formulas_ready()
            or self.policy is None
            or self.certification is None
        ):
            return empty
        calculated = CertifiedFinancialCalculator().calculate(
            settlement,
            certification=self.certification,
            policy=self.policy,
        )
        def present(value: object, field: str) -> str | None:
            if value is None:
                return None
            rounded = self.policy.rounding_policy(value, field)
            return f"{rounded:.2f}"

        return {
            "sales_ttc": present(calculated.sales_ttc, "sales_ttc"),
            "sales_ht": present(calculated.sales_ht, "sales_ht"),
            "commission_amount": present(
                calculated.commission_amount, "commission_amount"
            ),
            "invoice_ht": present(calculated.invoice_ht, "invoice_ht"),
            "invoice_tva": present(calculated.invoice_tva, "invoice_tva"),
            "invoice_ttc": present(calculated.invoice_ttc, "invoice_ttc"),
            "note_de_debours": present(
                calculated.disbursement_note, "note_de_debours"
            ),
            "final_net_payable": present(
                calculated.net_payable, "final_net_payable"
            ),
        }

    @staticmethod
    def render_local_preview(preview: DocumentPreview) -> bytes:
        return json.dumps(
            preview.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()
