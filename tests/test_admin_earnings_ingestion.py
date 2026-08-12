from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.config import Settings
from src.google.models import DriveConnectionResult, DriveFile
from src.ingestion.admin_earnings import AdminEarningsIngestionService
from src.models.enums import AuditLevel, DuplicateKind, IngestionStatus

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def source(
    file_id: str = "file-1", name: str = "earnings.csv", size: int = 100
) -> DriveFile:
    return DriveFile(
        file_id=file_id,
        name=name,
        mime_type="text/csv" if name.endswith(".csv") else XLSX,
        modified_time=datetime(2026, 8, 12, tzinfo=UTC),
        size=size,
    )


def csv_bytes(rows: list[list[object]], headers: list[str] | None = None) -> bytes:
    frame = pd.DataFrame(
        rows,
        columns=headers
        or ["Order ID", "Restaurant ID", "Order Date", "Gross Amount", "Order Status"],
    )
    return frame.to_csv(index=False).encode()


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(
        rows,
        columns=[
            "Order ID",
            "Restaurant ID",
            "Order Date",
            "Gross Amount",
            "Order Status",
        ],
    ).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


class MemoryDrive:
    def __init__(self, content: dict[str, bytes]) -> None:
        self.content = content
        self.downloaded: list[str] = []

    def download_file(self, file_id: str) -> bytes:
        self.downloaded.append(file_id)
        return self.content[file_id]

    def list_files(self, folder_id: str):
        raise AssertionError("Ingestion must receive discovered files")

    def list_child_folders(self, folder_id: str):
        raise AssertionError("Ingestion must not discover folders")

    def get_file_metadata(self, file_id: str):
        raise AssertionError("Metadata is provided by discovery")

    def get_folder_metadata(self, folder_id: str):
        raise AssertionError("Metadata is provided by discovery")

    def find_files(self, folder_id: str, *, name_contains: str | None = None):
        raise AssertionError("Ingestion must not discover files")

    def file_exists(self, file_id: str) -> bool:
        return file_id in self.content

    def test_connection(self, root_folder_id: str) -> DriveConnectionResult:
        return DriveConnectionResult(connected=True)


def service(drive: MemoryDrive, **overrides: object) -> AdminEarningsIngestionService:
    return AdminEarningsIngestionService(drive, Settings(_env_file=None, **overrides))


def test_csv_schema_normalization_preserves_source_semantics() -> None:
    content = csv_bytes(
        [["ORD-1", "RST-9", "2026-08-12 10:30", "1 234,50 MAD", "cancelled"]]
    )
    result = service(MemoryDrive({"file-1": content})).ingest((source(),))
    assert result.status == IngestionStatus.SUCCESS
    assert result.rows_read == 1
    record = result.records[0]
    assert record.order_id == "ORD-1"
    assert record.restaurant_id == "RST-9"
    assert record.gross_amount == Decimal("1234.50")
    assert record.operational_status == "cancelled"
    assert ("Order Status", "cancelled") in record.source_values
    assert not hasattr(record, "settlement_decision")


def test_xlsx_is_supported() -> None:
    content = xlsx_bytes(
        [[1, 42, datetime(2026, 8, 12), 99.5, "DELIVERED"]]  # noqa: DTZ001
    )
    result = service(MemoryDrive({"xlsx-1": content})).ingest(
        (source("xlsx-1", "earnings.xlsx", len(content)),)
    )
    assert result.status == IngestionStatus.SUCCESS
    assert result.records[0].order_id == "1"
    assert result.records[0].restaurant_id == "42"


def test_configured_column_mapping() -> None:
    headers = ["Commande", "Partenaire", "Quand", "Valeur", "Etat"]
    content = csv_bytes([["O-1", "R-1", "2026-08-01", "20.00", "DELIVERED"]], headers)
    mapping = {
        "order_id": "Commande",
        "restaurant_id": "Partenaire",
        "order_date": "Quand",
        "gross_amount": "Valeur",
        "operational_status": "Etat",
    }
    result = service(
        MemoryDrive({"file-1": content}), admin_earnings_column_map=mapping
    ).ingest((source(),))
    assert result.status == IngestionStatus.SUCCESS
    assert result.file_results[0].detected_columns == mapping


def test_missing_required_columns_blocks_file() -> None:
    content = b"Order ID,Restaurant ID\nO-1,R-1\n"
    result = service(MemoryDrive({"file-1": content})).ingest((source(),))
    assert result.status == IngestionStatus.BLOCKED
    assert result.records == ()
    assert result.issues[0].code == "MISSING_REQUIRED_COLUMNS"


def test_invalid_row_is_blocking_and_source_value_is_not_invented() -> None:
    content = csv_bytes([["O-1", "", "not-a-date", "oops", "DELIVERED"]])
    result = service(MemoryDrive({"file-1": content})).ingest((source(),))
    assert result.status == IngestionStatus.BLOCKED
    assert result.records == ()
    assert result.issues[0].source_row_number == 2


def test_exact_duplicate_is_collapsed_deterministically() -> None:
    rows = [
        ["O-1", "R-1", "2026-08-01", 20, "DELIVERED"],
        ["O-1", "R-1", "2026-08-01", 20, "DELIVERED"],
    ]
    result = service(MemoryDrive({"file-1": csv_bytes(rows)})).ingest((source(),))
    assert result.status == IngestionStatus.COMPLETED_WITH_WARNINGS
    assert len(result.records) == 1
    assert result.duplicates[0].kind == DuplicateKind.EXACT


def test_conflicting_duplicate_is_blocked_and_no_version_is_accepted() -> None:
    rows = [
        ["O-1", "R-1", "2026-08-01", 20, "DELIVERED"],
        ["O-1", "R-1", "2026-08-01", 25, "DELIVERED"],
    ]
    result = service(MemoryDrive({"file-1": csv_bytes(rows)})).ingest((source(),))
    assert result.status == IngestionStatus.BLOCKED
    assert result.records == ()
    assert result.duplicates[0].kind == DuplicateKind.CONFLICTING
    assert result.issues[-1].level == AuditLevel.BLOCKING


def test_duplicates_are_detected_across_source_files() -> None:
    row = [["O-1", "R-1", "2026-08-01", 20, "DELIVERED"]]
    drive = MemoryDrive({"a": csv_bytes(row), "b": csv_bytes(row)})
    result = service(drive).ingest((source("a", "a.csv"), source("b", "b.csv")))
    assert len(result.records) == 1
    assert result.duplicates[0].occurrences == 2
    assert result.duplicates[0].source_locations == ("a.csv:row 2", "b.csv:row 2")


def test_oversized_file_is_rejected_before_download() -> None:
    drive = MemoryDrive({"file-1": b"unused"})
    result = service(drive, admin_earnings_max_file_mb=1).ingest(
        (source(size=2 * 1024 * 1024),)
    )
    assert result.status == IngestionStatus.BLOCKED
    assert drive.downloaded == []


def test_no_source_files_is_blocking() -> None:
    result = service(MemoryDrive({})).ingest(())
    assert result.status == IngestionStatus.BLOCKED
    assert result.issues[0].code == "NO_SOURCE_FILES"


def test_ingestion_exposes_no_source_mutation_operations() -> None:
    assert not hasattr(AdminEarningsIngestionService, "upload_file")
    assert not hasattr(AdminEarningsIngestionService, "update_file")
    assert not hasattr(AdminEarningsIngestionService, "delete_file")
