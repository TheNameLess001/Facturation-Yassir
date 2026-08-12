from decimal import Decimal

import pytest

from src.documents import DocumentRegistry, DocumentService
from src.models.domain import Restaurant, RestaurantSettlement
from src.models.enums import DocumentStatus, WorkflowState


def restaurant() -> Restaurant:
    return Restaurant(
        restaurant_id="R-1", restaurant_name="One", legal_entity="One SARL", ice="ICE1"
    )


def settlement(net: str = "80", state: WorkflowState = WorkflowState.VALIDATED):
    return RestaurantSettlement(
        restaurant_id="R-1",
        period_id="2026-08-P1",
        gross_sales=Decimal(100),
        commission=Decimal(20),
        net_payable=Decimal(net),
        state=state,
    )


def test_documents_require_validated_settlement(tmp_path) -> None:
    service = DocumentService(DocumentRegistry(tmp_path / "docs.sqlite3"))
    with pytest.raises(PermissionError):
        service.generate(restaurant(), settlement(state=WorkflowState.DATA_READY))


def test_document_numbers_are_unique_and_deterministic(tmp_path) -> None:
    service = DocumentService(DocumentRegistry(tmp_path / "docs.sqlite3"))
    first = service.generate(restaurant(), settlement())
    numbers = [item[0].document_number for item in first]
    assert numbers == [
        "INV-2026-08-P1-000001",
        "DN-2026-08-P1-000001",
        "STMT-2026-08-P1-000001",
    ]
    second = service.generate(restaurant(), settlement())
    assert second[0][0].document_number == "INV-2026-08-P1-000002"
    assert second[0][0].supersedes_document_id == first[0][0].document_id


def test_financial_change_marks_documents_stale_without_overwrite(tmp_path) -> None:
    registry = DocumentRegistry(tmp_path / "docs.sqlite3")
    service = DocumentService(registry)
    generated = service.generate(restaurant(), settlement())
    assert service.invalidate_if_changed(settlement("81"))
    stored = registry.list_for_settlement("R-1", "2026-08-P1")
    assert all(item.status == DocumentStatus.STALE for item in stored)
    assert {item.document_id for item in stored} == {
        item[0].document_id for item in generated
    }


def test_unchanged_financials_do_not_invalidate_documents(tmp_path) -> None:
    service = DocumentService(DocumentRegistry(tmp_path / "docs.sqlite3"))
    service.generate(restaurant(), settlement())
    assert not service.invalidate_if_changed(settlement())
