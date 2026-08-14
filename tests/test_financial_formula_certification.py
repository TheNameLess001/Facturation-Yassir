from __future__ import annotations

import io
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from src.documents.phase8 import (
    CashCoDocumentType,
    DocumentReadinessStatus,
    Phase8DocumentEngine,
)
from src.restaurants.registry_models import (
    DataQualityStatus,
    MappingStatus,
    RegisteredRestaurant,
    RestaurantReadiness,
)
from src.settlement.certified_calculator import CertifiedFinancialCalculator
from src.settlement.legacy_validation import (
    FinancialFormulaCertification,
    FormulaCertificationService,
    FormulaCertificationStatus,
    FormulaEvidenceConfidence,
    HistoricalParityCase,
    HistoricalParityEngine,
    LegacyFormulaEvidence,
    LegacyFormulaRegistry,
    ParityStatus,
)
from src.settlement.phase5_models import (
    CommissionResolution,
    CommissionResolutionStatus,
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)
from src.settlement.reference_import import (
    HistoricalReferenceImporter,
    ReferenceArtifactType,
    ReferenceInspectionStatus,
)


class SyntheticCertifiedPolicy:
    """Explicit synthetic fixture; this is not a CashCo production policy."""

    policy_version = "synthetic_test_v1"
    quantum = Decimal("0.01")

    def eligible_order_amount(self, order: dict[str, object]) -> Decimal | None:
        return order.get("amount") if order.get("eligible") else None  # type: ignore[return-value]

    def commission_base(self, values: dict[str, object]) -> Decimal:
        value = values["eligible_partner_amount"]
        assert isinstance(value, Decimal)
        return value

    def commission_amount(self, base: Decimal, rate: Decimal) -> Decimal:
        return self.rounding_policy(base * rate, "commission_amount")

    def invoice_ht(self, values: dict[str, Decimal]) -> Decimal:
        return self.rounding_policy(values["commission_amount"], "invoice_ht")

    def invoice_tva(self, invoice_ht: Decimal) -> Decimal:
        return self.rounding_policy(invoice_ht * Decimal("0.20"), "invoice_tva")

    def invoice_ttc(self, invoice_ht: Decimal, invoice_tva: Decimal) -> Decimal:
        return self.rounding_policy(invoice_ht + invoice_tva, "invoice_ttc")

    def note_de_debours(self, values: dict[str, Decimal]) -> Decimal:
        return self.rounding_policy(
            values["eligible_partner_amount"] + values["compensation_amount"],
            "note_de_debours",
        )

    def final_net_payable(self, values: dict[str, Decimal]) -> Decimal:
        return self.rounding_policy(
            values["note_de_debours"] - values["invoice_ttc"],
            "final_net_payable",
        )

    def rounding_policy(self, value: Decimal, field: str) -> Decimal:
        assert field
        assert isinstance(value, Decimal)
        return value.quantize(self.quantum, rounding=ROUND_HALF_UP)


def authoritative_evidence() -> tuple[LegacyFormulaEvidence, ...]:
    return tuple(
        LegacyFormulaEvidence(
            financial_field=field,
            formula=f"synthetic approved formula for {field}",
            evidence_source="synthetic approved accounting specification",
            source_file="synthetic_fixture_only",
            confidence=FormulaEvidenceConfidence.AUTHORITATIVE,
        )
        for field in LegacyFormulaRegistry.REQUIRED_FIELDS
    )


def parity_case(case_id: str, *, mismatch: bool = False) -> HistoricalParityCase:
    expected = {
        "commission_amount": Decimal("20.00"),
        "invoice_ht": Decimal("20.00"),
        "invoice_tva": Decimal("4.00"),
        "invoice_ttc": Decimal("24.00"),
        "note_de_debours": Decimal("100.00"),
        "final_net_payable": Decimal("76.00"),
    }
    calculated = dict(expected)
    if mismatch:
        calculated["invoice_ttc"] = Decimal("24.01")
    return HistoricalParityCase(
        case_id=case_id,
        restaurant_id=f"R-{case_id}",
        period_code="2026-07-P2",
        reference_source="synthetic fixture",
        policy_version="synthetic_test_v1",
        legacy_expected=expected,
        cashco_calculated=calculated,
    )


def certified() -> FinancialFormulaCertification:
    parity = HistoricalParityEngine()
    return FormulaCertificationService(LegacyFormulaRegistry.REQUIRED_FIELDS).certify(
        evidence=authoritative_evidence(),
        parity_results=(parity.compare(parity_case("1")), parity.compare(parity_case("2"))),
        policy_version="synthetic_test_v1",
        policy_implemented=True,
        reconciliation_difference=Decimal(0),
    )


def registered_restaurant() -> RegisteredRestaurant:
    return RegisteredRestaurant(
        restaurant_id="R-1",
        restaurant_name="Synthetic Restaurant",
        legal_entity="Synthetic SARL",
        ice="ICE",
        if_number="IF",
        rc="RC",
        address="Address",
        scope_source_row=2,
        mapping_method="EXACT_ID",
        mapping_status=MappingStatus.MATCHED_BY_ID,
        data_quality_status=DataQualityStatus.HEALTHY,
        readiness=RestaurantReadiness(
            identity_ready=True,
            orders_available=True,
            settlement_ready=True,
            document_ready=True,
            email_ready=True,
            payment_ready=True,
        ),
    )


def settlement() -> RestaurantSettlementEvaluation:
    return RestaurantSettlementEvaluation(
        period_code="2026-07-P2",
        restaurant_id="R-1",
        restaurant_name="Synthetic Restaurant",
        commission_rate=Decimal("0.20"),
        invoice_scope_commission_rate=Decimal("0.20"),
        commission_resolution=CommissionResolution(
            scope_commission=Decimal("0.20"),
            status=CommissionResolutionStatus.SCOPE_ONLY,
            effective_commission=Decimal("0.20"),
            resolution_source="INVOICE_SCOPE_AUTHORITY",
        ),
        total_orders=1,
        delivered_orders=1,
        cancelled_orders=0,
        manual_review_orders=0,
        pay_partner_orders=1,
        excluded_orders=0,
        yassir_compensation_orders=0,
        gross_order_value=Decimal("100.00"),
        eligible_partner_amount=Decimal("100.00"),
        excluded_amount=Decimal(0),
        compensation_amount=Decimal(0),
        settlement_status=RestaurantSettlementStatus.READY,
    )


def test_repository_evidence_is_weak_or_unknown_and_not_found() -> None:
    registry = LegacyFormulaRegistry()
    report = registry.evidence_report()
    certification = registry.certification()
    assert report.authoritative_fields == ()
    assert set(report.weak_fields) == {"commission_amount", "final_net_payable"}
    assert certification.status == FormulaCertificationStatus.NOT_FOUND
    assert not certification.production_ready


def test_authoritative_evidence_alone_does_not_certify() -> None:
    certification = FormulaCertificationService(
        LegacyFormulaRegistry.REQUIRED_FIELDS
    ).certify(
        evidence=authoritative_evidence(),
        parity_results=(),
        policy_version=None,
        policy_implemented=False,
        reconciliation_difference=None,
    )
    assert certification.status == FormulaCertificationStatus.DISCOVERED
    assert not certification.production_ready


def test_synthetic_policy_decimal_calculations_and_version() -> None:
    policy = SyntheticCertifiedPolicy()
    base = policy.commission_base({"eligible_partner_amount": Decimal("100.025")})
    commission = policy.commission_amount(base, Decimal("0.20"))
    ht = policy.invoice_ht({"commission_amount": commission})
    tva = policy.invoice_tva(ht)
    ttc = policy.invoice_ttc(ht, tva)
    debours = policy.note_de_debours(
        {
            "eligible_partner_amount": Decimal("100.025"),
            "compensation_amount": Decimal("5.00"),
        }
    )
    net = policy.final_net_payable(
        {"note_de_debours": debours, "invoice_ttc": ttc}
    )
    assert policy.policy_version == "synthetic_test_v1"
    assert base == Decimal("100.025")
    assert commission == Decimal("20.01")
    assert (ht, tva, ttc, debours, net) == (
        Decimal("20.01"),
        Decimal("4.00"),
        Decimal("24.01"),
        Decimal("105.03"),
        Decimal("81.02"),
    )


def test_parity_match_mismatch_no_reference_and_float_rejection() -> None:
    engine = HistoricalParityEngine()
    match = engine.compare(parity_case("match"))
    mismatch = engine.compare(parity_case("mismatch", mismatch=True))
    no_reference_case = parity_case("none").model_copy(
        update={
            "legacy_expected": {
                **parity_case("none").legacy_expected,
                "invoice_tva": None,
            }
        }
    )
    no_reference = engine.compare(no_reference_case)
    assert match.mismatches == 0
    assert mismatch.mismatches == 1
    ttc = next(item for item in mismatch.fields if item.financial_field == "invoice_ttc")
    assert ttc.status == ParityStatus.MISMATCH
    assert ttc.difference == Decimal("0.01")
    assert any(item.status == ParityStatus.NO_REFERENCE for item in no_reference.fields)
    with pytest.raises(ValueError, match="binary float"):
        HistoricalParityCase(
            case_id="float",
            restaurant_id="R",
            period_code="P",
            reference_source="bad fixture",
            policy_version="v1",
            legacy_expected={"invoice_ttc": 1.2},
            cashco_calculated={"invoice_ttc": Decimal("1.20")},
        )


def test_certification_gate_and_parity_failure() -> None:
    assert certified().status == FormulaCertificationStatus.CERTIFIED
    parity = HistoricalParityEngine()
    failed = FormulaCertificationService(LegacyFormulaRegistry.REQUIRED_FIELDS).certify(
        evidence=authoritative_evidence(),
        parity_results=(
            parity.compare(parity_case("1")),
            parity.compare(parity_case("2", mismatch=True)),
        ),
        policy_version="synthetic_test_v1",
        policy_implemented=True,
        reconciliation_difference=Decimal(0),
    )
    assert failed.status == FormulaCertificationStatus.PARITY_FAILED
    assert not failed.production_ready


def test_documents_stay_blocked_before_certification_and_unlock_after() -> None:
    blocked = Phase8DocumentEngine().readiness(registered_restaurant(), settlement())
    engine = Phase8DocumentEngine(
        certification=certified(),
        policy=SyntheticCertifiedPolicy(),
    )
    unlocked = engine.readiness(registered_restaurant(), settlement())
    preview = engine.preview(
        CashCoDocumentType.INVOICE,
        registered_restaurant(),
        settlement(),
    )
    calculated = CertifiedFinancialCalculator().calculate(
        settlement(),
        certification=certified(),
        policy=SyntheticCertifiedPolicy(),
    )
    assert blocked.status == DocumentReadinessStatus.FORMULA_NOT_VALIDATED
    assert unlocked.status == DocumentReadinessStatus.READY
    assert preview.content["invoice_ttc"] == "24.00"
    assert preview.content["final_net_payable"] == "76.00"
    assert preview.financial_policy_version == "synthetic_test_v1"
    assert calculated.financial_policy_version == "synthetic_test_v1"


def test_reference_import_profiles_without_retaining_values() -> None:
    importer = HistoricalReferenceImporter()
    csv_profile = importer.inspect(
        "legacy.csv",
        b"Restaurant,HT,TVA\nSecret Restaurant,10,2\n",
    )
    pdf_profile = importer.inspect(
        "invoice.pdf",
        b"%PDF-1.4 synthetic",
        pdf_type=ReferenceArtifactType.PDF_INVOICE,
    )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{"Restaurant": "Synthetic", "TTC": "12"}]).to_excel(
            writer, index=False, sheet_name="Settlement"
        )
    excel_profile = importer.inspect("legacy.xlsx", output.getvalue())
    assert csv_profile.sheets[0].columns == ("Restaurant", "HT", "TVA")
    assert "Secret Restaurant" not in csv_profile.model_dump_json()
    assert pdf_profile.status == ReferenceInspectionStatus.UNSTRUCTURED_ACCEPTED
    assert excel_profile.sheets[0].name == "Settlement"
    assert "Synthetic" not in excel_profile.model_dump_json()
