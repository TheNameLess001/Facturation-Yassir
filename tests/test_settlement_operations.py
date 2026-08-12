from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.audit import AuditService, InMemoryAuditRepository
from src.google.sheets_service import LEDGER_TABS, GoogleSheetsService
from src.ingestion.processed_datasets import ProcessedDatasetService
from src.models.domain import Order, Restaurant
from src.models.enums import FinancialDecision, WorkflowState
from src.restaurants.ledger import RestaurantLedgerProvisioner
from src.restaurants.registry import RestaurantRegistryService
from src.settlement.adjustments import AdjustmentService, InMemoryAdjustmentRepository
from src.settlement.calculator import SettlementCalculator
from src.settlement.validation import SettlementValidationService


def restaurant(restaurant_id: str = "R-1", *, complete: bool = True) -> Restaurant:
    return Restaurant(
        restaurant_id=restaurant_id,
        restaurant_name="One",
        legal_entity="Legal" if complete else None,
        ice="ICE" if complete else None,
        rib="RIB" if complete else None,
        commission_rate=Decimal("0.2"),
    )


def order(
    order_id: str = "O-1",
    decision: FinancialDecision = FinancialDecision.PAY_PARTNER,
    amount: str = "100",
) -> Order:
    return Order(
        order_id=order_id,
        restaurant_id="R-1",
        restaurant_name="One",
        order_date=datetime(2026, 8, 12, tzinfo=UTC),
        settlement_period="2026-08-P1",
        gross_amount=Decimal(amount),
        original_status="DELIVERED",
        automatic_settlement_decision=decision,
        final_settlement_decision=decision,
        settlement_reason="TEST",
        source_file_id="file",
        source_filename="earn.csv",
        processed_at=datetime.now(UTC),
    )


def test_settlement_calculation_uses_final_decision_and_decimal() -> None:
    summary = SettlementCalculator().summarize(
        (order(), order("O-2", FinancialDecision.EXCLUDE, "50")),
        (restaurant(),),
        "2026-08-P1",
    )[0]
    assert summary.gross_sales == Decimal("150.00")
    assert summary.commission == Decimal("20.00")
    assert summary.net_payable == Decimal("80.00")
    assert summary.state == WorkflowState.DATA_READY


def test_manual_review_routes_to_review_state() -> None:
    summary = SettlementCalculator().summarize(
        (order(decision=FinancialDecision.MANUAL_REVIEW),),
        (restaurant(),),
        "2026-08-P1",
    )[0]
    assert summary.state == WorkflowState.TO_REVIEW


def test_manual_adjustment_preserves_source_status_and_audits() -> None:
    audit_repo = InMemoryAuditRepository()
    service = AdjustmentService(
        InMemoryAdjustmentRepository(), AuditService(audit_repo)
    )
    original = order(decision=FinancialDecision.EXCLUDE)
    updated, adjustment = service.reclassify(
        original,
        FinancialDecision.PAY_PARTNER,
        reason="YASSIR_RESPONSIBILITY",
        comment="Ops evidence",
        user_id="finance-1",
    )
    assert updated.original_status == original.original_status
    assert updated.final_settlement_decision == FinancialDecision.PAY_PARTNER
    assert adjustment.previous_decision == FinancialDecision.EXCLUDE
    assert audit_repo.list_events()[0].event_type == "ORDER_RECLASSIFIED"


def test_locked_period_rejects_adjustment() -> None:
    service = AdjustmentService(
        InMemoryAdjustmentRepository(), AuditService(InMemoryAuditRepository())
    )
    with pytest.raises(PermissionError):
        service.reclassify(
            order(),
            FinancialDecision.EXCLUDE,
            reason="TEST",
            comment=None,
            user_id="finance",
            period_locked=True,
        )


def test_validation_blocks_missing_legal_data_and_manual_review() -> None:
    summary = SettlementCalculator().summarize(
        (order(decision=FinancialDecision.MANUAL_REVIEW),),
        (restaurant(complete=False),),
        "2026-08-P1",
    )[0]
    result = SettlementValidationService().validate(
        summary,
        (order(decision=FinancialDecision.MANUAL_REVIEW),),
        restaurant(complete=False),
    )
    assert not result.valid
    assert result.state == WorkflowState.BLOCKED
    assert {item.code for item in result.issues} >= {
        "MISSING_RIB",
        "UNRESOLVED_MANUAL_REVIEW",
    }


def test_processed_parquet_is_idempotently_replaced(tmp_path) -> None:
    service = ProcessedDatasetService(tmp_path)
    orders = (order(),)
    summaries = SettlementCalculator().summarize(orders, (restaurant(),), "2026-08-P1")
    first = service.write_period("2026-08-P1", orders, summaries, ("scope",))
    second = service.write_period("2026-08-P1", orders, summaries, ("scope",))
    assert first["orders_sha256"] == second["orders_sha256"]
    assert len(service.read_orders("2026-08-P1")) == 1
    assert len(service.read_summaries("2026-08-P1")) == 1


def test_google_sheet_has_required_ledger_tabs() -> None:
    request = Mock()
    request.execute.return_value = {"spreadsheetId": "sheet-1"}
    spreadsheets = Mock()
    spreadsheets.create.return_value = request
    api = Mock()
    api.spreadsheets.return_value = spreadsheets
    assert (
        GoogleSheetsService(api).create_restaurant_ledger("folder", "Title")
        == "sheet-1"
    )
    tabs = [
        item["properties"]["title"]
        for item in spreadsheets.create.call_args.kwargs["body"]["sheets"]
    ]
    assert tuple(tabs) == LEDGER_TABS


def test_ledger_provisioning_does_not_recreate_existing_sheet(tmp_path) -> None:
    registry = RestaurantRegistryService(tmp_path / "registry.sqlite3")
    registry.upsert((restaurant(),), "hash")
    sheets = Mock()
    sheets.create_restaurant_ledger.return_value = "sheet-1"
    provisioner = RestaurantLedgerProvisioner(sheets, registry)
    assert provisioner.ensure_ledger(restaurant(), "folder") == "sheet-1"
    assert provisioner.ensure_ledger(restaurant(), "folder") == "sheet-1"
    sheets.create_restaurant_ledger.assert_called_once()
