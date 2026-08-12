from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FormulaEvidenceConfidence(StrEnum):
    AUTHORITATIVE = "AUTHORITATIVE"
    STRONG = "STRONG"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class LegacyFormulaEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    financial_field: str
    formula: str | None = None
    evidence_source: str
    source_file: str | None = None
    source_location: str | None = None
    confidence: FormulaEvidenceConfidence


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


class LegacyFormulaRegistry:
    REQUIRED_FIELDS = (
        "commission_base",
        "commission_amount",
        "invoice_ht",
        "invoice_tva",
        "invoice_ttc",
        "note_de_debours",
        "final_net_payable",
    )

    def discover(self) -> tuple[LegacyFormulaEvidence, ...]:
        evidence = [
            LegacyFormulaEvidence(
                financial_field="commission_amount",
                formula="payable * commission_rate; ROUND_HALF_UP to 0.01",
                evidence_source="Initial CashCo V2 prototype; no legacy artifact",
                source_file="src/settlement/calculator.py",
                source_location="SettlementCalculator.summarize",
                confidence=FormulaEvidenceConfidence.WEAK,
            ),
            LegacyFormulaEvidence(
                financial_field="final_net_payable",
                formula="payable - commission + adjustment; ROUND_HALF_UP to 0.01",
                evidence_source="Initial CashCo V2 prototype; no legacy artifact",
                source_file="src/settlement/calculator.py",
                source_location="SettlementCalculator.summarize",
                confidence=FormulaEvidenceConfidence.WEAK,
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
            )
            for field in self.REQUIRED_FIELDS
            if field not in known
        )
        return tuple(evidence)

    def production_ready(
        self,
        evidence: tuple[LegacyFormulaEvidence, ...] | None = None,
    ) -> bool:
        found = evidence or self.discover()
        authoritative = {
            item.financial_field
            for item in found
            if item.confidence == FormulaEvidenceConfidence.AUTHORITATIVE
            and item.formula
        }
        return set(self.REQUIRED_FIELDS) <= authoritative

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
