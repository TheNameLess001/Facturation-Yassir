from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO

import pandas as pd

from src.documents.legal_readiness import DocumentLegalPolicy, DocumentLegalStatus
from src.documents.phase8 import CashCoDocumentType, Phase8DocumentEngine
from src.emails.packages import PartnerEmailPackageFactory
from src.emails.phase10_models import DocumentAttachmentRef
from src.google.models import DriveFile
from src.restaurants.legal_master import (
    PartnerLegalMasterCache,
    PartnerLegalMasterSource,
    PartnerLegalRegistryEnricher,
)
from src.restaurants.registry_models import (
    LegalMasterSyncStatus,
    PaymentReadinessStatus,
)
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader
from src.settlement.phase5_models import (
    CommissionResolution,
    CommissionResolutionStatus,
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)


def base_registry():
    scope = pd.DataFrame(
        [{"Restaurant ID": "1001", "Restaurant": "Alpha", "Commission": "20"}]
    )
    rst = pd.DataFrame(
        [
            {
                "Restaurant ID": "1001",
                "Restaurant Name": "Alpha",
                "Address": "RST Address",
                "Main City": "RST City",
                "Email": "rst@example.test",
                "Phone": "0500000000",
            }
        ]
    )
    result = RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
    )
    return result, {"1001"}


def legal_frame(**updates: object) -> pd.DataFrame:
    row: dict[str, object] = {
        "Restaurant ID": "1001",
        "Restaurant Name": "Alpha",
        "Raison Sociale": "Alpha SARL",
        "Adresse": "Legal Address",
        "Ville": "Legal City",
        "ICE": "001234567890123",
        "IF": "IF-1",
        "RC": "RC-1",
        "Finance Email": "FINANCE@EXAMPLE.TEST ",
        "Phone": "0600000000",
        "RIB": "123456789012345678901234",
        "Bank": "Bank",
        "Finance Contact": "Finance Team",
        "Review Status": "CONFIRMED",
        "Data Status": "DO NOT TRUST",
        "Payment Status": "DO NOT TRUST",
        "Legal Completeness %": "0%",
        "RIB Status": "DO NOT TRUST",
    }
    row.update(updates)
    return pd.DataFrame([row])


def enrich(frame: pd.DataFrame):
    registry, rst_ids = base_registry()
    snapshot = PartnerLegalMasterSource.from_frame(frame)
    return PartnerLegalRegistryEnricher().enrich(
        registry, snapshot, rst_ids=rst_ids
    )


def settlement() -> RestaurantSettlementEvaluation:
    return RestaurantSettlementEvaluation(
        period_code="2026-07-P2",
        restaurant_id="1001",
        restaurant_name="Alpha",
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
        gross_order_value=Decimal(120),
        eligible_partner_amount=Decimal(120),
        excluded_amount=Decimal(0),
        compensation_amount=Decimal(0),
        settlement_status=RestaurantSettlementStatus.READY,
    )


def test_source_profiles_partners_and_preserves_id_as_text() -> None:
    frame = legal_frame(**{"Restaurant ID": 1001.0})
    snapshot = PartnerLegalMasterSource.from_frame(
        frame,
        worksheet_names=("README", "PARTNERS"),
        selected_worksheet="PARTNERS",
    )
    assert snapshot.status == LegalMasterSyncStatus.CONNECTED
    assert snapshot.profile is not None
    assert snapshot.profile.selected_worksheet == "PARTNERS"
    assert snapshot.profile.row_count == 1
    assert snapshot.records[0].restaurant_id == "1001"
    assert snapshot.records[0].finance_email == "finance@example.test"


def test_source_fetch_selects_partners_read_only() -> None:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Instructions": ["read only"]}).to_excel(
            writer, sheet_name="README", index=False
        )
        legal_frame().to_excel(writer, sheet_name="PARTNERS", index=False)

    class ReadOnlyDrive:
        writes = 0

        def get_file_metadata(self, file_id):
            return DriveFile(
                file_id=file_id,
                name="Partner Legal Master",
                mime_type="application/vnd.google-apps.spreadsheet",
                modified_time=datetime(2026, 8, 14, tzinfo=UTC),
                capabilities={"canDownload": True, "canEdit": False},
            )

        def export_file(self, file_id, mime_type):
            return buffer.getvalue()

    drive = ReadOnlyDrive()
    snapshot = PartnerLegalMasterSource(drive).fetch("legal", "PARTNERS")  # type: ignore[arg-type]
    assert snapshot.profile is not None
    assert snapshot.profile.worksheet_names == ("README", "PARTNERS")
    assert snapshot.profile.selected_worksheet == "PARTNERS"
    assert not snapshot.profile.capabilities["canEdit"]
    assert drive.writes == 0


def test_helper_columns_are_not_business_truth_or_fingerprint_inputs() -> None:
    first = PartnerLegalMasterSource.from_frame(legal_frame())
    second = PartnerLegalMasterSource.from_frame(
        legal_frame(
            **{
                "Data Status": "READY",
                "Payment Status": "READY",
                "Legal Completeness %": "100%",
                "RIB Status": "READY",
            }
        )
    )
    assert first.fingerprint == second.fingerprint


def test_fingerprint_detects_relevant_change_with_same_row_count() -> None:
    first = PartnerLegalMasterSource.from_frame(legal_frame())
    second = PartnerLegalMasterSource.from_frame(
        legal_frame(**{"Raison Sociale": "Alpha Company"})
    )
    assert first.fingerprint != second.fingerprint


def test_exact_id_enrichment_and_field_precedence() -> None:
    result = enrich(legal_frame())
    restaurant = result.restaurants[0]
    assert restaurant.restaurant_name == "Alpha"
    assert restaurant.legal_entity == "Alpha SARL"
    assert restaurant.address == "Legal Address"
    assert restaurant.city == "Legal City"
    assert restaurant.finance_email == "finance@example.test"
    assert restaurant.phone == "0600000000"
    assert restaurant.invoice_scope_commission_rate == Decimal(20)
    assert restaurant.field_lineage["legal_entity"].source == "PARTNER_LEGAL_MASTER"
    assert restaurant.field_lineage["address"].source_field == "Adresse"
    assert result.partner_legal_master.audit_events[0].matched_ids == 1  # type: ignore[union-attr]


def test_address_and_finance_email_fall_back_safely() -> None:
    result = enrich(legal_frame(**{"Adresse": None, "Finance Email": None}))
    restaurant = result.restaurants[0]
    assert restaurant.address == "RST Address"
    assert restaurant.finance_email is None
    assert restaurant.email == "rst@example.test"
    assert restaurant.readiness.email_ready


def test_duplicate_id_and_conflict_are_not_applied_arbitrarily() -> None:
    rows = pd.concat(
        [legal_frame(), legal_frame(**{"Raison Sociale": "Wrong Company"})],
        ignore_index=True,
    )
    result = enrich(rows)
    snapshot = result.partner_legal_master
    assert snapshot is not None and snapshot.profile is not None
    assert snapshot.profile.duplicate_id_groups == 1
    assert snapshot.profile.conflict_groups == 1
    assert result.restaurants[0].legal_entity is None
    assert "LEGAL_SOURCE_CONFLICT" in result.restaurants[0].issue_codes


def test_name_mismatch_is_reviewed_and_not_enriched() -> None:
    result = enrich(legal_frame(**{"Restaurant Name": "Completely Different"}))
    snapshot = result.partner_legal_master
    assert snapshot is not None and snapshot.profile is not None
    assert snapshot.profile.name_mismatches == 1
    assert result.restaurants[0].legal_entity is None
    assert "LEGAL_MASTER_NAME_MISMATCH" in result.restaurants[0].issue_codes


def test_missing_and_unknown_ids_are_classified() -> None:
    frame = pd.concat(
        [
            legal_frame(**{"Restaurant ID": None, "Restaurant Name": "Missing"}),
            legal_frame(**{"Restaurant ID": "9999", "Restaurant Name": "Unknown"}),
        ],
        ignore_index=True,
    )
    result = enrich(frame)
    codes = {item.code for item in result.partner_legal_master.issues}  # type: ignore[union-attr]
    assert "MISSING_ID" in codes
    assert "ID_NOT_IN_RST" in codes


def test_optional_legal_fields_and_rib_do_not_block_documents() -> None:
    result = enrich(
        legal_frame(
            **{"Raison Sociale": None, "ICE": None, "IF": None, "RC": None, "RIB": None}
        )
    )
    restaurant = result.restaurants[0]
    legal = DocumentLegalPolicy().evaluate_package(restaurant)
    assert all(item.status == DocumentLegalStatus.READY_WITH_WARNINGS for item in legal)
    assert not restaurant.readiness.payment_ready
    assert restaurant.payment_readiness_status == PaymentReadinessStatus.RIB_MISSING
    assert (
        Phase8DocumentEngine()
        .production_candidate(CashCoDocumentType.INVOICE, restaurant, settlement())
        .status.value
        == "PRODUCTION_READY"
    )


def test_rib_validation_masking_and_payment_readiness() -> None:
    valid = enrich(legal_frame()).restaurants[0]
    invalid = enrich(legal_frame(RIB="not-a-rib")).restaurants[0]
    assert valid.readiness.payment_ready
    assert valid.payment_readiness_status == PaymentReadinessStatus.PAYMENT_READY
    assert PartnerLegalMasterSource.mask_rib(valid.rib) == "****************1234"
    assert not invalid.readiness.payment_ready
    assert invalid.payment_readiness_status == PaymentReadinessStatus.RIB_INVALID


def test_cache_ttl_force_refresh_and_stale_last_success() -> None:
    ticks = iter((0.0, 1.0, 2.0, 3.0, 400.0, 401.0, 402.0, 403.0))
    cache = PartnerLegalMasterCache(monotonic=lambda: next(ticks))
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("temporary outage")
        return PartnerLegalMasterSource.from_frame(
            legal_frame(Comment=str(calls)),
            now=datetime(2026, 8, 14, tzinfo=UTC),
        )

    first = cache.load("key", loader, ttl_seconds=300)
    cached = cache.load("key", loader, ttl_seconds=300)
    forced = cache.load("key", loader, ttl_seconds=300, force=True)
    cache.expire("key")
    stale = cache.load("key", loader, ttl_seconds=300)
    assert first is cached
    assert forced.fingerprint != first.fingerprint
    assert stale.status == LegalMasterSyncStatus.STALE_SOURCE
    assert stale.fingerprint == forced.fingerprint
    assert stale.last_successful_sync == forced.last_successful_sync


def test_legal_or_recipient_change_changes_authorization_bound_hashes() -> None:
    first_restaurant = enrich(legal_frame()).restaurants[0]
    second_restaurant = enrich(
        legal_frame(
            **{
                "Raison Sociale": "Alpha Company",
                "Finance Email": "other@example.test",
            }
        )
    ).restaurants[0]
    engine = Phase8DocumentEngine()
    first_document = engine.production_candidate(
        CashCoDocumentType.INVOICE, first_restaurant, settlement()
    )
    second_document = engine.production_candidate(
        CashCoDocumentType.INVOICE, second_restaurant, settlement()
    )
    assert first_document.content_hash != second_document.content_hash
    attachment = lambda item: (
        DocumentAttachmentRef(
            document_type="INVOICE",
            document_id=item.document_reference,
            version=item.document_version,
            content_hash=item.content_hash,
            status=item.status.value,
        ),
    )
    factory = PartnerEmailPackageFactory()
    first_package = factory.create(
        period_code="2026-07-P2",
        restaurant=first_restaurant,
        financial_status="READY",
        settlement_snapshot=settlement().model_dump(mode="json"),
        document_refs=attachment(first_document),
    )
    second_package = factory.create(
        period_code="2026-07-P2",
        restaurant=second_restaurant,
        financial_status="READY",
        settlement_snapshot=settlement().model_dump(mode="json"),
        document_refs=attachment(second_document),
    )
    assert first_package.package_hash != second_package.package_hash
