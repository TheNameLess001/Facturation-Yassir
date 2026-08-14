from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from src.config import Settings
from src.documents.legal_readiness import (
    CashCoDocumentType,
    DocumentLegalPolicy,
    DocumentLegalStatus,
    DocumentPartnerNameSource,
    LegalFieldStatus,
)
from src.documents.phase8 import (
    DocumentReadinessStatus,
    Phase8DocumentEngine,
    ProductionDocumentStatus,
)
from src.emails.runtime import build_email_center_snapshot
from src.restaurants.registry_models import (
    DataQualityStatus,
    MappingStatus,
    RegisteredRestaurant,
    RestaurantReadiness,
)
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import (
    CommissionResolution,
    CommissionResolutionStatus,
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)
from src.settlement.phase5_runtime import Phase5Workspace
from src.settlement.phase5_service import Phase5SettlementService


def restaurant(
    *,
    legal_entity: str | None = None,
    address: str | None = "1 Approved Address",
    ice: str | None = None,
    if_number: str | None = None,
    rc: str | None = None,
    rib: str | None = None,
    identity_ready: bool = True,
) -> RegisteredRestaurant:
    sources = {
        "restaurant_name": "RST_LIST:Restaurant Name:row_2",
        "address": "RST_LIST:Address:row_2",
    }
    for field, value in {
        "legal_entity": legal_entity,
        "ice": ice,
        "if_number": if_number,
        "rc": rc,
        "rib": rib,
    }.items():
        if value:
            sources[field] = f"RST_LIST:{field}:row_2"
    return RegisteredRestaurant(
        restaurant_id="R1",
        restaurant_name="Restaurant One",
        legal_entity=legal_entity,
        address=address,
        ice=ice,
        if_number=if_number,
        rc=rc,
        rib=rib,
        email="finance@example.test",
        invoice_scope_commission_rate=Decimal("0.20"),
        scope_source_row=2,
        rst_source_reference="RST row 2",
        mapping_method="EXACT_RESTAURANT_ID",
        mapping_status=MappingStatus.MATCHED_BY_ID,
        data_quality_status=DataQualityStatus.HEALTHY,
        field_sources=sources,
        readiness=RestaurantReadiness(
            identity_ready=identity_ready,
            orders_available=True,
            settlement_ready=True,
            document_ready=False,
            email_ready=True,
            payment_ready=bool(rib),
        ),
    )


def settlement(
    status: RestaurantSettlementStatus = RestaurantSettlementStatus.READY,
) -> RestaurantSettlementEvaluation:
    return RestaurantSettlementEvaluation(
        period_code="2026-07-P2",
        restaurant_id="R1",
        restaurant_name="Restaurant One",
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
        manual_review_orders=(
            1 if status == RestaurantSettlementStatus.REVIEW_REQUIRED else 0
        ),
        pay_partner_orders=1,
        excluded_orders=0,
        yassir_compensation_orders=0,
        gross_order_value=Decimal(120),
        eligible_partner_amount=Decimal(120),
        excluded_amount=Decimal(0),
        compensation_amount=Decimal(0),
        settlement_status=status,
    )


def test_document_specific_requirements_do_not_use_one_global_gate() -> None:
    policy = DocumentLegalPolicy()
    invoice = policy.requirements(CashCoDocumentType.INVOICE)
    debours = policy.requirements(CashCoDocumentType.NOTE_DE_DEBOURS)
    statement = policy.requirements(CashCoDocumentType.PARTNER_STATEMENT)
    assert invoice.required_fields == ("document_partner_name", "address")
    assert debours.required_fields == ("document_partner_name", "address")
    assert statement.required_fields == ("document_partner_name",)
    assert "ice" in invoice.optional_fields
    assert "if_number" in invoice.optional_fields
    assert "rc" in invoice.optional_fields
    assert "rib" in invoice.optional_fields


def test_legal_entity_precedence_and_restaurant_name_fallback_are_traced() -> None:
    policy = DocumentLegalPolicy()
    legal = policy.evaluate(
        restaurant(legal_entity="Restaurant One SARL"),
        CashCoDocumentType.INVOICE,
    )
    fallback = policy.evaluate(restaurant(), CashCoDocumentType.INVOICE)
    assert legal.document_partner_name == "Restaurant One SARL"
    assert legal.document_partner_name_source == DocumentPartnerNameSource.LEGAL_ENTITY
    assert fallback.document_partner_name == "Restaurant One"
    assert (
        fallback.document_partner_name_source
        == DocumentPartnerNameSource.RST_RESTAURANT_NAME
    )
    assert fallback.status == DocumentLegalStatus.READY_WITH_WARNINGS


def test_missing_address_blocks_invoice_and_debours_but_not_statement() -> None:
    policy = DocumentLegalPolicy()
    target = restaurant(address=None)
    invoice = policy.evaluate(target, CashCoDocumentType.INVOICE)
    debours = policy.evaluate(target, CashCoDocumentType.NOTE_DE_DEBOURS)
    statement = policy.evaluate(target, CashCoDocumentType.PARTNER_STATEMENT)
    assert invoice.status == DocumentLegalStatus.BLOCKED
    assert debours.status == DocumentLegalStatus.BLOCKED
    assert invoice.missing_required_fields == ("address",)
    assert statement.status == DocumentLegalStatus.READY_WITH_WARNINGS
    assert "address" in statement.optional_missing_fields


def test_ice_quality_and_if_rc_rib_remain_optional() -> None:
    policy = DocumentLegalPolicy()
    valid = policy.evaluate(
        restaurant(ice="001 234-567 890 123"),
        CashCoDocumentType.INVOICE,
    )
    invalid = policy.evaluate(
        restaurant(ice="ICE-INVALID"),
        CashCoDocumentType.INVOICE,
    )
    missing = policy.evaluate(restaurant(), CashCoDocumentType.INVOICE)
    valid_ice = next(item for item in valid.field_traces if item.field == "ice")
    invalid_ice = next(item for item in invalid.field_traces if item.field == "ice")
    assert valid_ice.status == LegalFieldStatus.AVAILABLE
    assert valid_ice.value == "001234567890123"
    assert invalid_ice.status == LegalFieldStatus.INVALID
    assert invalid.invalid_fields == ("ice",)
    assert missing.status == DocumentLegalStatus.READY_WITH_WARNINGS
    assert {"if_number", "rc", "rib"} <= set(missing.optional_missing_fields)


def test_rib_is_masked_and_does_not_control_document_readiness() -> None:
    result = DocumentLegalPolicy().evaluate(
        restaurant(rib="123456789012345678901234"),
        CashCoDocumentType.INVOICE,
    )
    rib = next(item for item in result.field_traces if item.field == "rib")
    assert rib.value == "•••• 1234"
    assert not rib.required
    assert result.status == DocumentLegalStatus.READY_WITH_WARNINGS


def test_production_document_uses_certified_financials_and_versioned_hashes() -> None:
    engine = Phase8DocumentEngine()
    first = engine.production_candidate(
        CashCoDocumentType.INVOICE,
        restaurant(),
        settlement(),
        version=1,
    )
    second = engine.production_candidate(
        CashCoDocumentType.INVOICE,
        restaurant(),
        settlement(),
        version=2,
    )
    assert first.status == ProductionDocumentStatus.PRODUCTION_READY
    assert first.legal_status == DocumentLegalStatus.READY_WITH_WARNINGS
    assert first.financial_policy_version == "cashco_legacy_v1"
    assert first.content["sales_ttc"] == "120.00"
    assert first.content["sales_ht"] == "100.00"
    assert first.content["commission_amount"] == "20.00"
    assert first.content["invoice_tva"] == "4.00"
    assert first.content["invoice_ttc"] == "24.00"
    assert first.content["note_de_debours"] == "96.00"
    assert first.content["final_net_payable"] == "96.00"
    assert first.settlement_snapshot_hash == second.settlement_snapshot_hash
    assert first.document_reference != second.document_reference
    assert first.content_hash != second.content_hash


def test_production_document_stays_draft_for_legal_or_financial_blocker() -> None:
    engine = Phase8DocumentEngine()
    legal_blocked = engine.production_candidate(
        CashCoDocumentType.INVOICE,
        restaurant(address=None),
        settlement(),
    )
    review_blocked = engine.production_candidate(
        CashCoDocumentType.INVOICE,
        restaurant(),
        settlement(RestaurantSettlementStatus.REVIEW_REQUIRED),
    )
    assert legal_blocked.status == ProductionDocumentStatus.DRAFT
    assert "MISSING_REQUIRED_ADDRESS" in legal_blocked.validation_issues
    assert review_blocked.status == ProductionDocumentStatus.DRAFT
    assert "FINANCIAL_REVIEW_REQUIRED" in review_blocked.validation_issues


def test_identity_blocked_restaurant_cannot_become_production_ready() -> None:
    candidate = Phase8DocumentEngine().production_candidate(
        CashCoDocumentType.INVOICE,
        restaurant(identity_ready=False),
        settlement(),
    )
    assert candidate.status == ProductionDocumentStatus.DRAFT
    assert "IDENTITY_BLOCKED" in candidate.validation_issues


def test_email_ready_recalculates_from_production_ready_documents(tmp_path) -> None:
    scope = pd.DataFrame(
        [{"Restaurant": "Restaurant One", "Restaurant ID": "R1", "Commission": "20"}]
    )
    rst = pd.DataFrame(
        [
            {
                "Restaurant ID": "R1",
                "Restaurant Name": "Restaurant One",
                "Address": "1 Approved Address",
                "Email": "finance@example.test",
            }
        ]
    )
    registry = RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
    )
    assert registry.restaurants[0].field_sources["address"].startswith(
        "RST_LIST:Address:row_"
    )
    orders = pd.DataFrame(
        [
            {
                "order_id": "O1",
                "restaurant_id": "R1",
                "restaurant_name": "Restaurant One",
                "order_date": "2026-07-20",
                "operational_status": "Delivered",
                "cancellation_reason": None,
                "item_total": "120",
            }
        ]
    )
    summary = Phase5SettlementService().evaluate(
        SettlementPeriodService().get("2026-07-P2", as_of=date(2026, 8, 12)),
        orders,
        registry,
    )
    snapshot = build_email_center_snapshot(
        Phase5Workspace(summary=summary, registry=registry),
        settings=Settings(
            _env_file=None,
            email_workflow_registry_path=tmp_path / "email.sqlite3",
        ),
    )
    assert snapshot.document_ready == 1
    assert snapshot.email_ready == 1
    assert snapshot.production_send_eligible == 0
    assert all(
        item.status == ProductionDocumentStatus.PRODUCTION_READY
        for item in (
            Phase8DocumentEngine().production_candidate(
                document_type,
                registry.restaurants[0],
                summary.restaurants[0],
            )
            for document_type in CashCoDocumentType
        )
    )
    assert Phase8DocumentEngine().readiness(
        registry.restaurants[0], summary.restaurants[0]
    ).status == DocumentReadinessStatus.READY
