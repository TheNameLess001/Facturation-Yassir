from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.config import Settings
from src.google.models import DriveConnectionResult, DriveFile
from src.ingestion.admin_earnings_models import (
    AdminEarningsIngestionResult,
    NormalizedAdminEarningsRow,
)
from src.ingestion.eligibility_runtime import run_configured_eligibility
from src.ingestion.payment_scope import (
    PaymentScopeEligibilityService,
    PaymentScopeIngestionService,
)
from src.ingestion.payment_scope_registry import PaymentScopeSnapshotRegistry
from src.models.enums import EligibilityState, IngestionStatus


def source(name: str = "scope.csv", content_size: int = 100) -> DriveFile:
    return DriveFile(
        file_id="scope-file",
        name=name,
        mime_type=(
            "text/csv"
            if name.endswith(".csv")
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        modified_time=datetime(2026, 8, 10, 9, tzinfo=UTC),
        size=content_size,
        md5_checksum="drive-md5",
    )


def csv(rows: list[list[object]], columns: list[str] | None = None) -> bytes:
    return (
        pd.DataFrame(rows, columns=columns or ["Restaurant ID", "Restaurant Name"])
        .to_csv(index=False)
        .encode()
    )


def xlsx(rows: list[list[object]]) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(rows, columns=["Restaurant ID", "Restaurant Name"]).to_excel(
        buffer, index=False, engine="openpyxl"
    )
    return buffer.getvalue()


class ScopeDrive:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.downloads = 0

    def download_file(self, file_id: str) -> bytes:
        self.downloads += 1
        return self.content

    def list_files(self, folder_id: str):
        raise AssertionError

    def list_child_folders(self, folder_id: str):
        raise AssertionError

    def get_file_metadata(self, file_id: str):
        raise AssertionError

    def get_folder_metadata(self, folder_id: str):
        raise AssertionError

    def find_files(self, folder_id: str, *, name_contains: str | None = None):
        raise AssertionError

    def file_exists(self, file_id: str) -> bool:
        return True

    def test_connection(self, root_folder_id: str) -> DriveConnectionResult:
        return DriveConnectionResult(connected=True)


def scope_result(content: bytes, name: str = "scope.csv"):
    drive = ScopeDrive(content)
    return PaymentScopeIngestionService(drive, Settings(_env_file=None)).ingest(
        source(name, len(content)), "2026-08-P1"
    )


def order(
    order_id: str, restaurant_id: str, restaurant_name: str
) -> NormalizedAdminEarningsRow:
    return NormalizedAdminEarningsRow(
        order_id=order_id,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_name,
        order_date=datetime(2026, 8, 12, tzinfo=UTC),
        gross_amount=Decimal(100),
        operational_status="DELIVERED",
        source_file_id="earnings",
        source_filename="earnings.csv",
        source_row_number=2,
    )


def admin_result(*records: NormalizedAdminEarningsRow) -> AdminEarningsIngestionResult:
    now = datetime.now(UTC)
    return AdminEarningsIngestionResult(
        status=IngestionStatus.SUCCESS,
        records=records,
        started_at=now,
        completed_at=now,
    )


def test_payment_scope_csv_creates_versioned_snapshot() -> None:
    result = scope_result(csv([["RST-001", "One"], ["RST-002", "Two"]]))
    assert result.status == IngestionStatus.SUCCESS
    assert result.snapshot is not None
    assert result.snapshot.period_id == "2026-08-P1"
    assert result.snapshot.restaurant_ids == ("RST-001", "RST-002")
    assert result.snapshot.restaurant_count == 2
    assert len(result.snapshot.content_hash) == 64


def test_payment_scope_xlsx_is_supported() -> None:
    content = xlsx([[1, "One"], [2, "Two"]])
    result = scope_result(content, "scope.xlsx")
    assert result.status == IngestionStatus.SUCCESS
    assert tuple(item.restaurant_id for item in result.entries) == ("1", "2")


def test_missing_restaurant_id_column_is_blocking() -> None:
    result = scope_result(csv([["One"]], ["Restaurant Name"]))
    assert result.status == IngestionStatus.BLOCKED
    assert result.snapshot is None
    assert result.issues[0].code == "MISSING_RESTAURANT_ID"


def test_blank_restaurant_id_row_is_blocking() -> None:
    result = scope_result(csv([["", "Named but missing ID"]]))
    assert result.status == IngestionStatus.BLOCKED
    assert result.issues[0].source_row_number == 2


def test_duplicate_restaurant_ids_are_collapsed_by_identity() -> None:
    result = scope_result(csv([["RST-1", "First"], ["RST-1", "Different Name"]]))
    assert result.status == IngestionStatus.COMPLETED_WITH_WARNINGS
    assert result.duplicate_restaurant_ids == ("RST-1",)
    assert result.snapshot is not None
    assert result.snapshot.restaurant_ids == ("RST-1",)


def test_eligibility_uses_restaurant_id_only() -> None:
    scope = scope_result(csv([["RST-1", "Completely Different Name"]]))
    admin = admin_result(
        order("O-1", "RST-1", "Name does not match"),
        order("O-2", "RST-2", "Completely Different Name"),
    )
    result = PaymentScopeEligibilityService().apply(admin, scope)
    assert [item.order.order_id for item in result.eligible_orders] == ["O-1"]
    assert [item.order.order_id for item in result.out_of_scope_orders] == ["O-2"]
    assert result.eligible_orders[0].state == EligibilityState.ELIGIBLE
    assert result.out_of_scope_orders[0].reason == "RESTAURANT_ID_NOT_IN_PAYMENT_SCOPE"


def test_rst_list_is_not_an_eligibility_input() -> None:
    parameters = PaymentScopeEligibilityService.apply.__annotations__
    assert "rst_list" not in parameters
    assert "restaurant_name" not in parameters


def test_blocked_admin_ingestion_prevents_eligibility() -> None:
    now = datetime.now(UTC)
    blocked_admin = AdminEarningsIngestionResult(
        status=IngestionStatus.BLOCKED, started_at=now, completed_at=now
    )
    result = PaymentScopeEligibilityService().apply(
        blocked_admin, scope_result(csv([["RST-1", "One"]]))
    )
    assert result.status == IngestionStatus.BLOCKED
    assert result.eligible_orders == ()


def test_snapshot_registry_is_append_only_and_idempotent(tmp_path) -> None:
    snapshot = scope_result(csv([["RST-2", "Two"], ["RST-1", "One"]])).snapshot
    assert snapshot is not None
    registry = PaymentScopeSnapshotRegistry(tmp_path / "scope.sqlite3")
    first = registry.save(snapshot)
    second = registry.save(snapshot)
    assert first == second
    assert first.restaurant_ids == ("RST-1", "RST-2")
    assert registry.list_for_period("2026-08-P1") == (first,)


def test_changed_scope_content_creates_new_snapshot_version(tmp_path) -> None:
    first = scope_result(csv([["RST-1", "One"]])).snapshot
    second = scope_result(csv([["RST-1", "One"], ["RST-2", "Two"]])).snapshot
    assert first is not None and second is not None
    assert first.snapshot_id != second.snapshot_id
    registry = PaymentScopeSnapshotRegistry(tmp_path / "scope.sqlite3")
    registry.save(first)
    registry.save(second)
    assert len(registry.list_for_period("2026-08-P1")) == 2


def test_not_configured_runtime_is_safely_blocked() -> None:
    result = run_configured_eligibility(
        "2026-08-P1", Settings(_env_file=None, google_auth_mode="NOT_CONFIGURED")
    )
    assert result.status == IngestionStatus.BLOCKED
    assert result.issues[0].code == "DRIVE_NOT_CONNECTED"
