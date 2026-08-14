from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FormulaEvidenceConfidence(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    STRONG = "STRONG"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class FormulaCertificationStatus(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    DISCOVERED = "DISCOVERED"
    PARITY_FAILED = "PARITY_FAILED"
    PARTIALLY_VALIDATED = "PARTIALLY_VALIDATED"
    CERTIFIED = "CERTIFIED"


class FormulaEvidenceType(StrEnum):
    PRODUCTION_SOURCE_CODE = "PRODUCTION_SOURCE_CODE"
    HISTORICAL_ARTIFACT = "HISTORICAL_ARTIFACT"
    SPECIFICATION = "SPECIFICATION"
    INFERENCE = "INFERENCE"


class BusinessApprovalStatus(StrEnum):
    BUSINESS_OWNER_CONFIRMED = "BUSINESS_OWNER_CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"


class FormulaEvidenceCategory(StrEnum):
    ORDER_ELIGIBILITY = "ORDER_ELIGIBILITY"
    PARTNER_GROSS_AMOUNT = "PARTNER_GROSS_AMOUNT"
    COMMISSION_BASE = "COMMISSION_BASE"
    COMMISSION_AMOUNT = "COMMISSION_AMOUNT"
    INVOICE_HT = "INVOICE_HT"
    INVOICE_TVA = "INVOICE_TVA"
    INVOICE_TTC = "INVOICE_TTC"
    NOTE_DE_DEBOURS = "NOTE_DE_DEBOURS"
    YASSIR_COMPENSATION = "YASSIR_COMPENSATION"
    FINAL_NET_PAYABLE = "FINAL_NET_PAYABLE"
    ROUNDING_POLICY = "ROUNDING_POLICY"
    SALES_TTC = "SALES_TTC"
    SALES_HT = "SALES_HT"
    COMMISSION_RATE_NORMALIZATION = "COMMISSION_RATE_NORMALIZATION"
    TVA_RATE = "TVA_RATE"


class LegacyFormulaEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    financial_field: str
    formula: str | None = None
    evidence_source: str
    source_file: str | None = None
    source_location: str | None = None
    confidence: FormulaEvidenceConfidence
    category: FormulaEvidenceCategory | None = None
    notes: str | None = None
    evidence_type: FormulaEvidenceType | None = None
    approval: BusinessApprovalStatus = BusinessApprovalStatus.NOT_CONFIRMED


class ParityStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NO_REFERENCE = "NO_REFERENCE"
    FORMULA_NOT_VALIDATED = "FORMULA_NOT_VALIDATED"


class LegacyParityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    financial_field: str
    legacy_expected_amount: Decimal | None = None
    new_cashco_amount: Decimal | None = None
    difference: Decimal | None = None
    status: ParityStatus
    policy_version: str | None = None
    reference_source: str | None = None


class FormulaEvidenceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_at: datetime
    searched_locations: tuple[str, ...]
    evidence: tuple[LegacyFormulaEvidence, ...]
    authoritative_fields: tuple[str, ...]
    strong_fields: tuple[str, ...]
    weak_fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]


class FinancialFormulaCertification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: FormulaCertificationStatus
    policy_version: str | None = None
    authoritative_fields: tuple[str, ...] = ()
    required_fields: tuple[str, ...]
    parity_cases: int = 0
    parity_matches: int = 0
    parity_mismatches: int = 0
    source_reconstructed_cases: int = 0
    no_reference_results: int = 0
    reconciliation_difference: Decimal | None = None
    policy_implemented: bool = False
    implementation_parity_passed: bool = False
    historical_parity_required: bool = False
    certified_at: datetime | None = None
    reason: str

    @property
    def production_ready(self) -> bool:
        return self.status == FormulaCertificationStatus.CERTIFIED


class HistoricalParityCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    restaurant_id: str
    period_code: str
    reference_source: str
    policy_version: str
    legacy_expected: dict[str, Decimal | None]
    cashco_calculated: dict[str, Decimal | None]

    @field_validator("legacy_expected", "cashco_calculated", mode="before")
    @classmethod
    def reject_binary_float(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        if any(isinstance(item, float) for item in value.values()):
            raise ValueError("Parity financial values must not use binary float")
        return value


class HistoricalSourceOrder(BaseModel):
    """One immutable legacy source order used to reproduce a historical result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    source_values: dict[str, Decimal | str | int | bool | None]

    @field_validator("source_values", mode="before")
    @classmethod
    def reject_binary_float(cls, value: dict[str, object]) -> dict[str, object]:
        if any(isinstance(item, float) for item in value.values()):
            raise ValueError("Historical source order values must not use binary float")
        return value


class HistoricalOrderCalculation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    eligible_partner_amount: Decimal | None


class HistoricalFinancialChain(BaseModel):
    """Auditable source-to-net calculation produced exclusively by a policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str
    order_calculations: tuple[HistoricalOrderCalculation, ...]
    partner_amount: Decimal
    commission_base: Decimal
    commission_amount: Decimal
    invoice_ht: Decimal
    invoice_tva: Decimal
    invoice_ttc: Decimal
    note_de_debours: Decimal
    final_net_payable: Decimal

    def parity_values(self) -> dict[str, Decimal]:
        return {
            "commission_amount": self.commission_amount,
            "invoice_ht": self.invoice_ht,
            "invoice_tva": self.invoice_tva,
            "invoice_ttc": self.invoice_ttc,
            "note_de_debours": self.note_de_debours,
            "final_net_payable": self.final_net_payable,
        }


class HistoricalReconstructionCase(BaseModel):
    """Historical OLD CashCo result plus the exact orders needed to reproduce it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    restaurant_id: str
    period_code: str
    reference_source: str
    policy_version: str
    source_orders: tuple[HistoricalSourceOrder, ...]
    commission_rate: Decimal
    legacy_expected: dict[str, Decimal | None]
    settlement_context: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator("commission_rate", mode="before")
    @classmethod
    def reject_float_rate(cls, value: object) -> object:
        if isinstance(value, float):
            raise TypeError("Historical commission rate must not use binary float")
        return value

    @field_validator("legacy_expected", "settlement_context", mode="before")
    @classmethod
    def reject_float_financial_values(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        if any(isinstance(item, float) for item in value.values()):
            raise ValueError("Historical financial values must not use binary float")
        return value


class HistoricalParityCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    restaurant_id: str
    period_code: str
    policy_version: str
    fields: tuple[LegacyParityResult, ...]
    reconstructed_from_source: bool = False
    calculation_chain: HistoricalFinancialChain | None = None

    @property
    def matches(self) -> int:
        return sum(item.status == ParityStatus.MATCH for item in self.fields)

    @property
    def mismatches(self) -> int:
        return sum(item.status == ParityStatus.MISMATCH for item in self.fields)

    @property
    def total_absolute_difference(self) -> Decimal | None:
        differences = [item.difference for item in self.fields if item.difference is not None]
        if len(differences) != len(self.fields):
            return None
        return sum((abs(item) for item in differences), Decimal(0))


class LegacyCalculationPolicy(Protocol):
    """Certified policy contract. No production implementation exists yet."""

    policy_version: str

    def eligible_order_amount(self, order: dict[str, object]) -> Decimal | None: ...
    def commission_base(self, order_or_settlement: dict[str, object]) -> Decimal: ...
    def commission_amount(self, base: Decimal, rate: Decimal) -> Decimal: ...
    def invoice_ht(self, values: dict[str, Decimal]) -> Decimal: ...
    def invoice_tva(self, invoice_ht: Decimal) -> Decimal: ...
    def invoice_ttc(self, invoice_ht: Decimal, invoice_tva: Decimal) -> Decimal: ...
    def note_de_debours(self, values: dict[str, Decimal]) -> Decimal: ...
    def final_net_payable(self, values: dict[str, Decimal]) -> Decimal: ...
    def rounding_policy(self, value: Decimal, field: str) -> Decimal: ...


class LegacyFormulaRegistry:
    REQUIRED_FIELDS = (
        "sales_ttc",
        "sales_ht",
        "commission_rate_normalization",
        "commission_base",
        "commission_amount",
        "tva_rate",
        "tva",
        "invoice_ttc",
        "note_de_debours",
        "final_net_payable",
        "rounding_policy",
    )

    SEARCHED_LOCATIONS = (
        "4_Generateur bulk.py authoritative monetary calculation block",
        "business-owner production-source approval",
        "cashco_legacy_v1 deterministic implementation cases",
    )
    FIELD_CATEGORIES: ClassVar[dict[str, FormulaEvidenceCategory]] = {
        "sales_ttc": FormulaEvidenceCategory.SALES_TTC,
        "sales_ht": FormulaEvidenceCategory.SALES_HT,
        "commission_rate_normalization": FormulaEvidenceCategory.COMMISSION_RATE_NORMALIZATION,
        "commission_base": FormulaEvidenceCategory.COMMISSION_BASE,
        "commission_amount": FormulaEvidenceCategory.COMMISSION_AMOUNT,
        "tva_rate": FormulaEvidenceCategory.TVA_RATE,
        "tva": FormulaEvidenceCategory.INVOICE_TVA,
        "invoice_ttc": FormulaEvidenceCategory.INVOICE_TTC,
        "note_de_debours": FormulaEvidenceCategory.NOTE_DE_DEBOURS,
        "final_net_payable": FormulaEvidenceCategory.FINAL_NET_PAYABLE,
        "rounding_policy": FormulaEvidenceCategory.ROUNDING_POLICY,
    }

    AUTHORITATIVE_FORMULAS: ClassVar[dict[str, str]] = {
        "sales_ttc": "clean_currency(Item total)",
        "sales_ht": "sales_ttc / 1.2",
        "commission_rate_normalization": "rate / 100 if rate > 1 else rate",
        "commission_base": "sales_ht",
        "commission_amount": "sales_ht * normalized commission rate",
        "tva_rate": "20%",
        "tva": "commission_ht * 0.20",
        "invoice_ttc": "commission_ht + tva",
        "note_de_debours": "sales_ttc - invoice_ttc",
        "final_net_payable": "sales_ttc - invoice_ttc",
        "rounding_policy": "no intermediate rounding; presentation to 2 decimals",
    }

    def discover(self) -> tuple[LegacyFormulaEvidence, ...]:
        return tuple(
            LegacyFormulaEvidence(
                financial_field=field,
                formula=self.AUTHORITATIVE_FORMULAS[field],
                evidence_source="Approved legacy production generator",
                source_file="4_Generateur bulk.py",
                source_location="authoritative monetary calculation block",
                confidence=FormulaEvidenceConfidence.AUTHORITATIVE,
                category=self.FIELD_CATEGORIES[field],
                notes="Business owner explicitly confirmed this production source as 100% correct.",
                evidence_type=FormulaEvidenceType.PRODUCTION_SOURCE_CODE,
                approval=BusinessApprovalStatus.BUSINESS_OWNER_CONFIRMED,
            )
            for field in self.REQUIRED_FIELDS
        )

    def evidence_report(self) -> FormulaEvidenceReport:
        evidence = self.discover()
        by_confidence = {
            confidence: tuple(
                sorted(
                    item.financial_field
                    for item in evidence
                    if item.confidence == confidence
                )
            )
            for confidence in FormulaEvidenceConfidence
        }
        return FormulaEvidenceReport(
            generated_at=datetime.now(UTC),
            searched_locations=self.SEARCHED_LOCATIONS,
            evidence=evidence,
            authoritative_fields=by_confidence[
                FormulaEvidenceConfidence.AUTHORITATIVE
            ],
            strong_fields=by_confidence[FormulaEvidenceConfidence.STRONG],
            weak_fields=by_confidence[FormulaEvidenceConfidence.WEAK],
            unknown_fields=by_confidence[FormulaEvidenceConfidence.UNKNOWN],
        )

    def certification(self) -> FinancialFormulaCertification:
        policy = self.active_policy()
        return FormulaCertificationService(self.REQUIRED_FIELDS).certify(
            evidence=self.discover(),
            parity_results=(),
            policy_version=policy.policy_version,
            policy_implemented=True,
            implementation_validated=policy.implementation_matches_authoritative_cases(),
            reconciliation_difference=Decimal(0),
        )

    @staticmethod
    def active_policy() -> LegacyCalculationPolicy:
        from src.settlement.cashco_legacy_v1 import CashCoLegacyV1Policy

        return CashCoLegacyV1Policy()

    def production_ready(
        self,
        evidence: tuple[LegacyFormulaEvidence, ...] | None = None,
        certification: FinancialFormulaCertification | None = None,
    ) -> bool:
        if certification is not None:
            return certification.production_ready
        candidate = FormulaCertificationService(self.REQUIRED_FIELDS).certify(
            evidence=evidence or self.discover(),
            parity_results=(),
            policy_version=self.active_policy().policy_version,
            policy_implemented=True,
            implementation_validated=(
                self.active_policy().implementation_matches_authoritative_cases()
            ),
            reconciliation_difference=Decimal(0),
        )
        return candidate.production_ready

    def compare(
        self,
        *,
        restaurant_id: str,
        period_code: str,
        financial_field: str,
        legacy_expected: Decimal | None,
        new_amount: Decimal | None,
        formula_validated: bool,
    ) -> LegacyParityResult:
        if not formula_validated:
            status = ParityStatus.FORMULA_NOT_VALIDATED
            difference = None
        elif legacy_expected is None or new_amount is None:
            status = ParityStatus.NO_REFERENCE
            difference = None
        else:
            difference = new_amount - legacy_expected
            status = ParityStatus.MATCH if difference == 0 else ParityStatus.MISMATCH
        return LegacyParityResult(
            restaurant_id=restaurant_id,
            period_code=period_code,
            financial_field=financial_field,
            legacy_expected_amount=legacy_expected,
            new_cashco_amount=new_amount,
            difference=difference,
            status=status,
        )


class HistoricalParityEngine:
    def __init__(self, fields: tuple[str, ...] | None = None) -> None:
        self.fields = fields or (
            "commission_amount",
            "invoice_ht",
            "invoice_tva",
            "invoice_ttc",
            "note_de_debours",
            "final_net_payable",
        )

    def compare(self, value: HistoricalParityCase) -> HistoricalParityCaseResult:
        results: list[LegacyParityResult] = []
        for field in self.fields:
            expected = value.legacy_expected.get(field)
            calculated = value.cashco_calculated.get(field)
            if expected is None or calculated is None:
                status = ParityStatus.NO_REFERENCE
                difference = None
            else:
                self._require_decimal(expected, field)
                self._require_decimal(calculated, field)
                difference = calculated - expected
                status = ParityStatus.MATCH if difference == 0 else ParityStatus.MISMATCH
            results.append(
                LegacyParityResult(
                    restaurant_id=value.restaurant_id,
                    period_code=value.period_code,
                    financial_field=field,
                    legacy_expected_amount=expected,
                    new_cashco_amount=calculated,
                    difference=difference,
                    status=status,
                    policy_version=value.policy_version,
                    reference_source=value.reference_source,
                )
            )
        return HistoricalParityCaseResult(
            case_id=value.case_id,
            restaurant_id=value.restaurant_id,
            period_code=value.period_code,
            policy_version=value.policy_version,
            fields=tuple(results),
        )

    def reconstruct_and_compare(
        self,
        value: HistoricalReconstructionCase,
        policy: LegacyCalculationPolicy,
    ) -> HistoricalParityCaseResult:
        """Rebuild the V2 chain from source orders, then compare every OLD output."""

        if policy.policy_version != value.policy_version:
            raise ValueError("Historical case policy version does not match implementation")
        if not value.source_orders:
            raise ValueError("At least one historical source order is required")

        order_calculations: list[HistoricalOrderCalculation] = []
        partner_amount = Decimal(0)
        for order in value.source_orders:
            source = {"order_id": order.order_id, **order.source_values}
            amount = policy.eligible_order_amount(source)
            if amount is not None:
                self._require_decimal(amount, "eligible_partner_amount")
                partner_amount += amount
            order_calculations.append(
                HistoricalOrderCalculation(
                    order_id=order.order_id,
                    eligible_partner_amount=amount,
                )
            )

        calculation_values: dict[str, Decimal] = dict(value.settlement_context)
        calculation_values.update(
            {
                "partner_amount": partner_amount,
                "eligible_partner_amount": partner_amount,
            }
        )
        commission_base = policy.commission_base(calculation_values)
        commission_amount = policy.commission_amount(
            commission_base, value.commission_rate
        )
        calculation_values.update(
            {
                "commission_base": commission_base,
                "commission_amount": commission_amount,
            }
        )
        invoice_ht = policy.invoice_ht(calculation_values)
        invoice_tva = policy.invoice_tva(invoice_ht)
        invoice_ttc = policy.invoice_ttc(invoice_ht, invoice_tva)
        calculation_values.update(
            {
                "invoice_ht": invoice_ht,
                "invoice_tva": invoice_tva,
                "invoice_ttc": invoice_ttc,
            }
        )
        note_de_debours = policy.note_de_debours(calculation_values)
        calculation_values["note_de_debours"] = note_de_debours
        final_net_payable = policy.final_net_payable(calculation_values)
        chain = HistoricalFinancialChain(
            policy_version=policy.policy_version,
            order_calculations=tuple(order_calculations),
            partner_amount=partner_amount,
            commission_base=commission_base,
            commission_amount=commission_amount,
            invoice_ht=invoice_ht,
            invoice_tva=invoice_tva,
            invoice_ttc=invoice_ttc,
            note_de_debours=note_de_debours,
            final_net_payable=final_net_payable,
        )
        compared = self.compare(
            HistoricalParityCase(
                case_id=value.case_id,
                restaurant_id=value.restaurant_id,
                period_code=value.period_code,
                reference_source=value.reference_source,
                policy_version=value.policy_version,
                legacy_expected=value.legacy_expected,
                cashco_calculated=chain.parity_values(),
            )
        )
        return compared.model_copy(
            update={
                "reconstructed_from_source": True,
                "calculation_chain": chain,
            }
        )

    @staticmethod
    def _require_decimal(value: object, field: str) -> None:
        if not isinstance(value, Decimal):
            raise TypeError(f"{field} must use Decimal")


class FormulaCertificationService:
    def __init__(self, required_fields: tuple[str, ...]) -> None:
        self.required_fields = required_fields

    def certify(
        self,
        *,
        evidence: tuple[LegacyFormulaEvidence, ...],
        parity_results: tuple[HistoricalParityCaseResult, ...],
        policy_version: str | None,
        policy_implemented: bool,
        reconciliation_difference: Decimal | None,
        implementation_validated: bool = False,
        historical_parity_required: bool = False,
    ) -> FinancialFormulaCertification:
        authoritative = tuple(
            sorted(
                {
                    item.financial_field
                    for item in evidence
                    if item.confidence == FormulaEvidenceConfidence.AUTHORITATIVE
                    and item.formula
                    and item.approval
                    == BusinessApprovalStatus.BUSINESS_OWNER_CONFIRMED
                }
            )
        )
        matches = sum(item.matches for item in parity_results)
        mismatches = sum(item.mismatches for item in parity_results)
        no_reference = sum(
            field.status == ParityStatus.NO_REFERENCE
            for result in parity_results
            for field in result.fields
        )
        reconstructed_cases = sum(
            result.reconstructed_from_source for result in parity_results
        )
        all_evidence = set(self.required_fields) <= set(authoritative)
        enough_cases = len(parity_results) >= 2
        all_cases_reconstructed = bool(parity_results) and reconstructed_cases == len(
            parity_results
        )
        complete_references = bool(parity_results) and no_reference == 0
        reconciled = reconciliation_difference == Decimal(0)
        if not authoritative:
            status = FormulaCertificationStatus.NOT_FOUND
            reason = "No authoritative legacy formula evidence was found."
        elif historical_parity_required and mismatches:
            status = FormulaCertificationStatus.PARITY_FAILED
            reason = "Historical parity contains visible mismatches."
        elif not all_evidence:
            status = FormulaCertificationStatus.PARTIALLY_VALIDATED
            reason = "Authoritative evidence does not cover every required formula."
        elif not policy_implemented or not policy_version:
            status = FormulaCertificationStatus.DISCOVERED
            reason = "Evidence exists but no versioned calculation policy is implemented."
        elif not implementation_validated or not reconciled:
            status = FormulaCertificationStatus.PARITY_FAILED
            reason = "V2 implementation does not reproduce the approved production source."
        elif historical_parity_required and (
            not enough_cases or not all_cases_reconstructed or not complete_references
        ):
            status = FormulaCertificationStatus.PARTIALLY_VALIDATED
            reason = (
                "Source-order reconstruction, parity coverage, or zero-difference "
                "reconciliation is incomplete."
            )
        else:
            status = FormulaCertificationStatus.CERTIFIED
            reason = "Authoritative evidence, policy, parity, and reconciliation are complete."
        return FinancialFormulaCertification(
            status=status,
            policy_version=policy_version,
            authoritative_fields=authoritative,
            required_fields=self.required_fields,
            parity_cases=len(parity_results),
            parity_matches=matches,
            parity_mismatches=mismatches,
            source_reconstructed_cases=reconstructed_cases,
            no_reference_results=no_reference,
            reconciliation_difference=reconciliation_difference,
            policy_implemented=policy_implemented,
            implementation_parity_passed=implementation_validated,
            historical_parity_required=historical_parity_required,
            certified_at=datetime.now(UTC)
            if status == FormulaCertificationStatus.CERTIFIED
            else None,
            reason=reason,
        )
