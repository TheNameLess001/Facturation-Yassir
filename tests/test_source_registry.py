from datetime import UTC, datetime, timedelta

from src.google.models import DriveFile
from src.ingestion.registry import SourceManifestRegistry
from src.models.enums import ChangeState, SourceType


def drive_file(
    *, modified: datetime | None = None, size: int = 100, checksum: str = "hash-1"
) -> DriveFile:
    return DriveFile(
        file_id="file-1",
        name="earnings.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        modified_time=modified or datetime(2026, 8, 12, tzinfo=UTC),
        size=size,
        md5_checksum=checksum,
    )


def test_new_and_unchanged_file_detection(tmp_path) -> None:
    registry = SourceManifestRegistry(tmp_path / "registry.sqlite3")
    assert registry.register(SourceType.ADMIN_EARNINGS, drive_file()) == ChangeState.NEW
    assert (
        registry.register(SourceType.ADMIN_EARNINGS, drive_file())
        == ChangeState.UNCHANGED
    )


def test_modified_file_detection(tmp_path) -> None:
    registry = SourceManifestRegistry(tmp_path / "registry.sqlite3")
    registry.register(SourceType.ADMIN_EARNINGS, drive_file())
    changed = drive_file(
        modified=datetime(2026, 8, 12, tzinfo=UTC) + timedelta(minutes=1),
        size=101,
        checksum="hash-2",
    )
    assert registry.register(SourceType.ADMIN_EARNINGS, changed) == ChangeState.MODIFIED


def test_missing_file_detection(tmp_path) -> None:
    registry = SourceManifestRegistry(tmp_path / "registry.sqlite3")
    registry.register(SourceType.ADMIN_EARNINGS, drive_file())
    missing = registry.mark_missing(SourceType.ADMIN_EARNINGS, set())
    assert [item.drive_file_id for item in missing] == ["file-1"]
    assert registry.get("file-1").status == ChangeState.MISSING  # type: ignore[union-attr]


def test_ingestion_statistics_update_registered_manifest(tmp_path) -> None:
    registry = SourceManifestRegistry(tmp_path / "registry.sqlite3")
    registry.register(SourceType.ADMIN_EARNINGS, drive_file())
    imported_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    registry.record_ingestion(
        "file-1",
        rows=10,
        unique_order_ids=9,
        duplicates=1,
        imported_at=imported_at,
        import_result="VALIDATED",
    )
    manifest = registry.get("file-1")
    assert manifest is not None
    assert manifest.rows == 10
    assert manifest.unique_order_ids == 9
    assert manifest.duplicates == 1
    assert manifest.imported_at == imported_at
    assert manifest.import_result == "VALIDATED"
