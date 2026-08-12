from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.google.exceptions import StorageArchitectureError
from src.google.models import DriveFile
from src.ingestion.admin_earnings_normalizer import (
    AdminEarningsNormalizer,
    normalize_datetime,
    normalize_decimal,
    normalize_identifier,
)
from src.ingestion.admin_earnings_reader import AdminEarningsReader
from src.ingestion.admin_earnings_schema import resolve_schema
from src.ingestion.deduplication import deduplicate_orders
from src.ingestion.phase2_discovery import (
    GOOGLE_SHEETS_MIME_TYPE,
    Phase2SourceDiscoveryService,
)
from src.ingestion.phase2_models import IgnoredFileReason
from src.ingestion.phase3_models import CanonicalAdminOrder, SourceOccurrence
from src.ingestion.processed_store import ARTIFACT_NAMES, ProcessedAdminEarningsStore
from src.ingestion.schema_profiler import SchemaProfiler

CSV = "text/csv"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def source(name="data week 2_2026", mime=CSV, file_id="f1"):
    return DriveFile(
        file_id=file_id,
        name=name,
        mime_type=mime,
        modified_time=datetime(2026, 8, 1, tzinfo=UTC),
        size=10,
        md5_checksum="hash",
    )


class MemoryReaderDrive:
    def __init__(self, content: bytes):
        self.content = content
        self.exports = []

    def download_file(self, file_id):
        return self.content

    def export_file(self, file_id, mime_type):
        self.exports.append((file_id, mime_type))
        return self.content


def test_extensionless_csv_mime_is_read() -> None:
    frame = AdminEarningsReader(MemoryReaderDrive(b"order id\n1\n")).read(source())
    assert frame.iloc[0, 0] == "1"


def test_extensionless_xlsx_mime_is_read() -> None:
    buffer = io.BytesIO()
    pd.DataFrame({"order id": [1]}).to_excel(buffer, index=False)
    frame = AdminEarningsReader(MemoryReaderDrive(buffer.getvalue())).read(source(mime=XLSX))
    assert frame.iloc[0, 0] == 1


def test_extensionless_google_sheet_exports_csv() -> None:
    drive = MemoryReaderDrive(b"order id\n1\n")
    frame = AdminEarningsReader(drive).read(source(mime=GOOGLE_SHEETS_MIME_TYPE))
    assert len(frame) == 1
    assert drive.exports == [("f1", "text/csv")]


def test_extensionless_unsupported_mime_is_ignored() -> None:
    assert Phase2SourceDiscoveryService._ignored_reason("data week 2_ 2026") == IgnoredFileReason.MALFORMED_ADMIN_FILENAME


def test_schema_profile_and_alias_resolution() -> None:
    frame = pd.DataFrame(columns=[" Order ID ", "Restaurant ID", "order day", "new column"])
    mapping, ambiguous = resolve_schema(frame)
    assert mapping == {"order_id": " Order ID ", "restaurant_id": "Restaurant ID", "order_date": "order day"}
    assert not ambiguous
    profiler = SchemaProfiler()
    profiler.add("week.csv", frame)
    profile = profiler.profiles()[0]
    assert profile.files == ("week.csv",)
    assert profile.unexpected_columns == ("new column",)


def test_identifier_normalization_never_uses_float_output() -> None:
    assert normalize_identifier(12345) == "12345"
    assert normalize_identifier(12345.0) == "12345"
    assert normalize_identifier("12345.0") == "12345"
    assert normalize_identifier("AB-12.0") == "AB-12.0"


def test_date_and_financial_normalization() -> None:
    parsed, original = normalize_datetime("2026-08-02 12:30")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed.date().isoformat() == "2026-08-02"
    assert original == "2026-08-02 12:30"
    assert normalize_decimal("1 234,50 MAD") == Decimal("1234.50")


def test_invalid_numeric_is_never_zero() -> None:
    try:
        normalize_decimal("not money")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid numeric must fail")


def occurrence(file_id="f1", row=2):
    return SourceOccurrence(
        source_file_id=file_id,
        source_filename=f"{file_id}.csv",
        source_week=2,
        source_year=2026,
        source_modified_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_row_number=row,
    )


def order(order_id="1", amount=Decimal(10), file_id="f1"):
    return CanonicalAdminOrder(
        order_id=order_id,
        restaurant_id="R1",
        order_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
        item_total=amount,
        lineage=(occurrence(file_id),),
        ingested_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_unique_and_identical_duplicate_retain_lineage() -> None:
    canonical, duplicates, conflicts, _ = deduplicate_orders([order(), order(file_id="f2")])
    assert len(canonical) == 1
    assert len(canonical[0].lineage) == 2
    assert len(duplicates) == 2
    assert not conflicts


def test_conflicting_duplicate_identifies_fields_and_is_excluded() -> None:
    canonical, _, conflicts, issues = deduplicate_orders([order(), order(amount=Decimal(11), file_id="f2")])
    assert canonical == []
    assert conflicts[0].conflicting_fields == ("item_total",)
    assert issues[0].category == "CONFLICTING_DUPLICATE"


def test_missing_order_id_and_invalid_values_are_issues() -> None:
    frame = pd.DataFrame([{"Order ID": "", "Restaurant ID": 1, "order day": "bad", "item total": "bad"}])
    mapping, _ = resolve_schema(frame)
    records, issues = AdminEarningsNormalizer().normalize_frame(
        frame, mapping, source("data week 2_2026.csv"), 2, 2026, datetime(2026, 8, 1, tzinfo=UTC)
    )
    assert records == []
    assert issues[0].category == "MISSING_ORDER_ID"


def test_processed_outputs_are_valid_and_deterministic(empty_phase3_result) -> None:
    store = ProcessedAdminEarningsStore()
    first = store.build(empty_phase3_result)
    second = store.build(empty_phase3_result)
    assert tuple(first) == ARTIFACT_NAMES
    assert first == second
    for name in ARTIFACT_NAMES:
        assert first[name]


class ExistingArtifactDrive:
    def __init__(self, *, missing: str | None = None, duplicate: str | None = None):
        self.files = []
        self.content = {}
        self.updated = []
        for index, name in enumerate(ARTIFACT_NAMES):
            if name == missing:
                continue
            item = source(name=name, file_id=f"artifact-{index}").model_copy(
                update={"capabilities": {"canEdit": True}}
            )
            self.files.append(item)
            self.content[item.file_id] = b"old"
            if name == duplicate:
                duplicate_item = item.model_copy(update={"file_id": f"duplicate-{index}"})
                self.files.append(duplicate_item)
                self.content[duplicate_item.file_id] = b"old"

    def list_files(self, folder_id):
        return tuple(self.files)

    def download_file(self, file_id):
        return self.content[file_id]

    def update_file_content(self, file_id, content, mime_type):
        self.updated.append(file_id)
        self.content[file_id] = content
        return next(item for item in self.files if item.file_id == file_id)


def test_existing_artifact_publish_updates_six_without_create() -> None:
    drive = ExistingArtifactDrive()
    artifacts = {name: f"new-{name}".encode() for name in ARTIFACT_NAMES}
    published = ProcessedAdminEarningsStore().publish(drive, "processed", artifacts)
    assert published == ARTIFACT_NAMES
    assert len(drive.updated) == 6
    assert not hasattr(drive, "create_file")


def test_missing_or_duplicate_artifact_stops_before_updates() -> None:
    artifacts = {name: b"new" for name in ARTIFACT_NAMES}
    for drive in (
        ExistingArtifactDrive(missing=ARTIFACT_NAMES[0]),
        ExistingArtifactDrive(duplicate=ARTIFACT_NAMES[0]),
    ):
        try:
            ProcessedAdminEarningsStore().publish(drive, "processed", artifacts)
        except StorageArchitectureError:
            pass
        else:
            raise AssertionError("invalid artifact architecture must block publishing")
        assert drive.updated == []
