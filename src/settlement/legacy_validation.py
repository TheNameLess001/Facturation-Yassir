from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, field_validator


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
    no_reference_results: int = 0
    reconciliation_difference: Decimal | None = None
    policy_implemented: bool = False
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


class HistoricalParityCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    restaurant_id: str
    period_code: str
    policy_version: str
    fields: tuple[LegacyParityResult, ...]

    @property
    def matches(self) -> int:
        return sum(item.status == ParityStatus.MATCH for item in self.fields)

    @property
    def mismatches(self) -> int:
        return sum(item.status == ParityStatus.MISMATCH for item in self.fields)


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
        "order_eligibility",
        "partner_gross_amount",
        "commission_base",
        "commission_amount",
        "invoice_ht",
        "invoice_tva",
        "invoice_ttc",
        "note_de_debours",
        "yassir_compensation",
        "final_net_payable",
        "rounding_policy",
    )

    SEARCHED_LOCATIONS = (
        "working tree source, documentation, tests, and reference files",
        "all reachable Git commits, branches, tags, and reflogs",
        "deleted paths and unreachable Git blobs",
        "PDF, Excel, CSV, invoice, disbursement-note, and template filenames",
    )
    FIELD_CATEGORIES: ClassVar[dict[str, FormulaEvidenceCategory]] = {
        "order_eligibility": FormulaEvidenceCategory.ORDER_ELIGIBILITY,
        "partner_gross_amount": FormulaEvidenceCategory.PARTNER_GROSS_AMOUNT,
        "commission_base": FormulaEvidenceCategory.COMMISSION_BASE,
        "commission_amount": FormulaEvidenceCategory.COMMISSION_AMOUNT,
        "invoice_ht": FormulaEvidenceCategory.INVOICE_HT,
        "invoice_tva": FormulaEvidenceCategory.INVOICE_TVA,
        "invoice_ttc": FormulaEvidenceCategory.INVOICE_TTC,
        "note_de_debours": FormulaEvidenceCategory.NOTE_DE_DEBOURS,
        "yassir_compensation": FormulaEvidenceCategory.YASSIR_COMPENSATION,
        "final_net_payable": FormulaEvidenceCategory.FINAL_NET_PAYABLE,
        "rounding_policy": FormulaEvidenceCategory.ROUNDING_POLICY,
    }

    def discover(self) -> tuple[LegacyFormulaEvidence, ...]:
        evidence = [
            LegacyFormulaEvidence(
                financial_field="commission_amount",
                formula="payable * commission_rate; ROUND_HALF_UP to 0.01",
                evidence_source="Initial CashCo V2 prototype; no legacy artifact",
                source_file="src/settlement/calculator.py",
                source_location="SettlementCalculator.summarize",
                confidence=FormulaEvidenceConfidence.WEAK,
                category=FormulaEvidenceCategory.COMMISSION_AMOUNT,
                notes="Prototype code is not evidence that this formula was used in production.",
            ),
            LegacyFormulaEvidence(
                financial_field="final_net_payable",
                formula="payable - commission + adjustment; ROUND_HALF_UP to 0.01",
                evidence_source="Initial CashCo V2 prototype; no legacy artifact",
                source_file="src/settlement/calculator.py",
                source_location="SettlementCalculator.summarize",
                confidence=FormulaEvidenceConfidence.WEAK,
                category=FormulaEvidenceCategory.FINAL_NET_PAYABLE,
                notes="Prototype code is incomplete and has no HT/TVA/TTC/debours chain.",
            ),
        ]
        known = {item.financial_field for item in evidence}
        evidence.extend(
            LegacyFormulaEvidence(
                financial_field=field,
                formula=None,
                evidence_source=(
                    "Repository, reachable Git history, reference files, templates, "
                    "and sample documents searched; no formula found"
                ),
                confidence=FormulaEvidenceConfidence.UNKNOWN,
                category=self.FIELD_CATEGORIES[field],
                notes="No production rule, rate, taxable base, component set, or rounding stage is proven.",
            )
            for field in self.REQUIRED_FIELDS
            if field not in known
        )
        return tuple(evidence)

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
        return FormulaCertificationService(self.REQUIRED_FIELDS).certify(
            evidence=self.discover(),
            parity_results=(),
            policy_version=None,
            policy_implemented=False,
            reconciliation_difference=None,
        )

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
            policy_version=None,
            policy_implemented=False,
            reconciliation_difference=None,
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
    ) -> FinancialFormulaCertification:
        authoritative = tuple(
            sorted(
                {
                    item.financial_field
                    for item in evidence
                    if item.confidence == FormulaEvidenceConfidence.AUTHORITATIVE
                    and item.formula
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
        all_evidence = set(self.required_fields) <= set(authoritative)
        enough_cases = len(parity_results) >= 2
        complete_references = bool(parity_results) and no_reference == 0
        reconciled = reconciliation_difference == Decimal(0)
        if not authoritative:
            status = FormulaCertificationStatus.NOT_FOUND
            reason = "No authoritative legacy formula evidence was found."
        elif mismatches:
            status = FormulaCertificationStatus.PARITY_FAILED
            reason = "Historical parity contains visible mismatches."
        elif not all_evidence:
            status = FormulaCertificationStatus.PARTIALLY_VALIDATED
            reason = "Authoritative evidence does not cover every required formula."
        elif not policy_implemented or not policy_version:
            status = FormulaCertificationStatus.DISCOVERED
            reason = "Evidence exists but no versioned calculation policy is implemented."
        elif not enough_cases or not complete_references or not reconciled:
            status = FormulaCertificationStatus.PARTIALLY_VALIDATED
            reason = "Parity coverage or zero-difference reconciliation is incomplete."
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
            no_reference_results=no_reference,
            reconciliation_difference=reconciliation_difference,
            policy_implemented=policy_implemented,
            certified_at=datetime.now(UTC)
            if status == FormulaCertificationStatus.CERTIFIED
            else None,
            reason=reason,
        )
