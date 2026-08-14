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
from src.settlement.cashco_legacy_v1 import (
    AUTHORITATIVE_SOURCE,
    POLICY_VERSION,
    TVA_RATE,
    CashCoLegacyV1Policy,
)
from src.settlement.certified_calculator import CertifiedFinancialCalculator
from src.settlement.legacy_validation import (
    BusinessApprovalStatus,
    FinancialFormulaCertification,
    FormulaCertificationService,
    FormulaCertificationStatus,
    FormulaEvidenceConfidence,
    HistoricalParityCase,
    HistoricalParityEngine,
    HistoricalReconstructionCase,
    HistoricalSourceOrder,
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
            approval=BusinessApprovalStatus.BUSINESS_OWNER_CONFIRMED,
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


def reconstruction_case(
    case_id: str, *, mismatch: bool = False
) -> HistoricalReconstructionCase:
    expected = {
        "commission_amount": Decimal("20.00"),
        "invoice_ht": Decimal("20.00"),
        "invoice_tva": Decimal("4.00"),
        "invoice_ttc": Decimal("24.00"),
        "note_de_debours": Decimal("100.00"),
        "final_net_payable": Decimal("76.00"),
    }
    if mismatch:
        expected["invoice_ttc"] = Decimal("24.01")
    return HistoricalReconstructionCase(
        case_id=case_id,
        restaurant_id=f"R-{case_id}",
        period_code="2026-07-P2",
        reference_source="synthetic formula-bearing workbook",
        policy_version="synthetic_test_v1",
        commission_rate=Decimal("0.20"),
        legacy_expected=expected,
        settlement_context={"compensation_amount": Decimal("0.00")},
        source_orders=(
            HistoricalSourceOrder(
                order_id=f"O-{case_id}-1",
                source_values={"eligible": True, "amount": Decimal("60.00")},
            ),
            HistoricalSourceOrder(
                order_id=f"O-{case_id}-2",
                source_values={"eligible": True, "amount": Decimal("40.00")},
            ),
            HistoricalSourceOrder(
                order_id=f"O-{case_id}-3",
                source_values={"eligible": False, "amount": Decimal("5.00")},
            ),
        ),
    )


def certified() -> FinancialFormulaCertification:
    parity = HistoricalParityEngine()
    policy = SyntheticCertifiedPolicy()
    return FormulaCertificationService(LegacyFormulaRegistry.REQUIRED_FIELDS).certify(
        evidence=authoritative_evidence(),
        parity_results=(
            parity.reconstruct_and_compare(reconstruction_case("1"), policy),
            parity.reconstruct_and_compare(reconstruction_case("2"), policy),
        ),
        policy_version="synthetic_test_v1",
        policy_implemented=True,
        implementation_validated=True,
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


def test_approved_production_source_certifies_active_policy() -> None:
    registry = LegacyFormulaRegistry()
    report = registry.evidence_report()
    certification = registry.certification()
    assert set(report.authoritative_fields) == set(registry.REQUIRED_FIELDS)
    assert report.weak_fields == ()
    assert {item.source_file for item in report.evidence} == {AUTHORITATIVE_SOURCE}
    assert certification.status == FormulaCertificationStatus.CERTIFIED
    assert certification.policy_version == POLICY_VERSION
    assert certification.production_ready
    assert certification.parity_cases == 0


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


def test_weak_or_unapproved_evidence_cannot_certify() -> None:
    weak = tuple(
        LegacyFormulaEvidence(
            financial_field=field,
            formula="inferred formula",
            evidence_source="prototype comment",
            confidence=FormulaEvidenceConfidence.WEAK,
        )
        for field in LegacyFormulaRegistry.REQUIRED_FIELDS
    )
    certification = FormulaCertificationService(
        LegacyFormulaRegistry.REQUIRED_FIELDS
    ).certify(
        evidence=weak,
        parity_results=(),
        policy_version=POLICY_VERSION,
        policy_implemented=True,
        implementation_validated=True,
        reconciliation_difference=Decimal(0),
    )
    assert certification.status == FormulaCertificationStatus.NOT_FOUND
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


def test_historical_chain_is_reconstructed_from_source_orders() -> None:
    result = HistoricalParityEngine().reconstruct_and_compare(
        reconstruction_case("chain"), SyntheticCertifiedPolicy()
    )
    chain = result.calculation_chain
    assert result.reconstructed_from_source
    assert result.mismatches == 0
    assert result.total_absolute_difference == Decimal("0.00")
    assert chain is not None
    assert chain.partner_amount == Decimal("100.00")
    assert chain.commission_base == Decimal("100.00")
    assert chain.commission_amount == Decimal("20.00")
    assert chain.invoice_ht == Decimal("20.00")
    assert chain.invoice_tva == Decimal("4.00")
    assert chain.invoice_ttc == Decimal("24.00")
    assert chain.note_de_debours == Decimal("100.00")
    assert chain.final_net_payable == Decimal("76.00")
    assert [item.eligible_partner_amount for item in chain.order_calculations] == [
        Decimal("60.00"),
        Decimal("40.00"),
        None,
    ]


def test_historical_reconstruction_is_optional_for_certification() -> None:
    parity = HistoricalParityEngine()
    certification = FormulaCertificationService(
        LegacyFormulaRegistry.REQUIRED_FIELDS
    ).certify(
        evidence=authoritative_evidence(),
        parity_results=(
            parity.compare(parity_case("manual-1")),
            parity.compare(parity_case("manual-2")),
        ),
        policy_version="synthetic_test_v1",
        policy_implemented=True,
        implementation_validated=True,
        reconciliation_difference=Decimal("0.00"),
    )
    assert certification.status == FormulaCertificationStatus.CERTIFIED
    assert certification.source_reconstructed_cases == 0
    assert certification.production_ready


def test_optional_historical_mismatch_is_reported_without_downgrading_policy() -> None:
    parity = HistoricalParityEngine()
    certification = FormulaCertificationService(
        LegacyFormulaRegistry.REQUIRED_FIELDS
    ).certify(
        evidence=authoritative_evidence(),
        parity_results=(parity.compare(parity_case("optional", mismatch=True)),),
        policy_version="synthetic_test_v1",
        policy_implemented=True,
        implementation_validated=True,
        reconciliation_difference=Decimal(0),
    )
    assert certification.parity_mismatches == 1
    assert certification.status == FormulaCertificationStatus.CERTIFIED


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
        implementation_validated=True,
        reconciliation_difference=Decimal(0),
        historical_parity_required=True,
    )
    assert failed.status == FormulaCertificationStatus.PARITY_FAILED
    assert not failed.production_ready


def test_documents_stay_blocked_before_certification_and_unlock_after() -> None:
    not_certified = FormulaCertificationService(
        LegacyFormulaRegistry.REQUIRED_FIELDS
    ).certify(
        evidence=(),
        parity_results=(),
        policy_version=None,
        policy_implemented=False,
        reconciliation_difference=None,
    )
    blocked = Phase8DocumentEngine(
        certification=not_certified,
        policy=SyntheticCertifiedPolicy(),
    ).readiness(registered_restaurant(), settlement())
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


@pytest.mark.parametrize(
    "rate", ["24", "24%", "24,0%", "0.24", Decimal("0.24")]
)
def test_authoritative_commission_rate_normalization(rate: object) -> None:
    assert CashCoLegacyV1Policy().normalize_commission_rate(rate).value == Decimal(
        "0.24"
    )


@pytest.mark.parametrize(
    ("item_total", "rate", "expected"),
    [
        (
            "120 MAD",
            "20%",
            ("120", "100", "20", "4", "24", "96"),
        ),
        (
            "240 MAD",
            "24%",
            ("240", "200", "48", "9.6", "57.6", "182.4"),
        ),
    ],
)
def test_cashco_legacy_v1_authoritative_cases(
    item_total: str,
    rate: str,
    expected: tuple[str, ...],
) -> None:
    result = CashCoLegacyV1Policy().calculate(item_total, rate)
    assert (
        result.sales_ttc,
        result.sales_ht,
        result.commission_ht,
        result.tva,
        result.invoice_ttc,
        result.net_payable,
    ) == tuple(Decimal(item) for item in expected)
    assert result.tva_rate == TVA_RATE
    assert result.policy_version == POLICY_VERSION
    assert result.invoice_ttc == result.commission_ht + result.tva
    assert result.tva == result.commission_ht * Decimal("0.20")
    assert result.net_payable + result.invoice_ttc == result.sales_ttc


def test_repeating_sales_ht_has_no_intermediate_rounding() -> None:
    result = CashCoLegacyV1Policy().calculate("100", "17")
    assert result.sales_ht == Decimal(100) / Decimal("1.2")
    assert result.commission_ht == result.sales_ht * Decimal("0.17")
    assert result.commission_ht != result.commission_ht.quantize(Decimal("0.01"))
    assert result.display_value("commission_ht") == Decimal("14.17")


def test_legacy_currency_normalization_and_zero_commission() -> None:
    result = CashCoLegacyV1Policy().calculate("1 234,50 MAD", "0")
    assert result.sales_ttc == Decimal("1234.50")
    assert result.commission_rate == 0
    assert result.commission_ht == 0
    assert result.tva == 0
    assert result.invoice_ttc == 0
    assert result.net_payable == Decimal("1234.50")


def test_presentation_and_amount_to_words_round_only_at_final_boundary() -> None:
    result = CashCoLegacyV1Policy().calculate("1.005", "0")
    assert result.sales_ttc == Decimal("1.005")
    assert result.display_value("sales_ttc") == Decimal("1.00")
    assert result.amount_to_words_value("sales_ttc") == Decimal("1.00")


@pytest.mark.parametrize("value", [None, "", "not money", float("nan")])
def test_legacy_invalid_money_defaults_to_zero_with_warning(value: object) -> None:
    result = CashCoLegacyV1Policy().calculate(value, "20%")
    assert result.sales_ttc == 0
    assert result.data_quality_warnings
    assert "DEFAULTED" in result.data_quality_warnings[0]


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
