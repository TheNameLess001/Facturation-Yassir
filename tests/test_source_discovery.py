from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.config import Settings
from src.google.exceptions import DriveConnectionError
from src.google.models import FOLDER_MIME_TYPE, DriveConnectionResult, DriveFile
from src.ingestion.registry import SourceManifestRegistry
from src.ingestion.source_discovery import SourceDiscoveryService
from src.models.enums import (
    ConnectionState,
    HealthState,
    SourceStatus,
    SourceType,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def file(
    file_id: str, name: str, *, mime: str = XLSX, parent: str = "root"
) -> DriveFile:
    return DriveFile(
        file_id=file_id,
        name=name,
        mime_type=mime,
        modified_time=datetime(2026, 8, 12, 10, tzinfo=UTC),
        size=1024,
        md5_checksum=f"hash-{file_id}",
        parent_ids=(parent,),
    )


def folder(file_id: str, name: str, *, parent: str = "root") -> DriveFile:
    return DriveFile(
        file_id=file_id,
        name=name,
        mime_type=FOLDER_MIME_TYPE,
        modified_time=datetime(2026, 8, 1, tzinfo=UTC),
        parent_ids=(parent,),
        is_folder=True,
    )


class FakeReadOnlyDrive:
    def __init__(self) -> None:
        self.files: dict[str, tuple[DriveFile, ...]] = {
            "admin": (file("earn-1", "Admin Earnings W31.xlsx", parent="admin"),),
            "payment": (),
            "period-folder": (file("scope-1", "payment.xlsx", parent="period-folder"),),
        }
        self.folders: dict[str, tuple[DriveFile, ...]] = {
            "payment": (folder("year-folder", "2026", parent="payment"),),
            "year-folder": (
                folder("period-folder", "2026-08-P1", parent="year-folder"),
            ),
        }
        self.metadata = {
            "rst-file": file("rst-file", "RST List.xlsx"),
            "root": folder("root", "CashCo Control Tower"),
        }
        self.download_calls = 0

    def list_files(self, folder_id: str) -> tuple[DriveFile, ...]:
        return self.files.get(folder_id, ())

    def list_child_folders(self, folder_id: str) -> tuple[DriveFile, ...]:
        return self.folders.get(folder_id, ())

    def get_file_metadata(self, file_id: str) -> DriveFile:
        return self.metadata[file_id]

    def get_folder_metadata(self, folder_id: str) -> DriveFile:
        return self.metadata[folder_id]

    def find_files(
        self, folder_id: str, *, name_contains: str | None = None
    ) -> tuple[DriveFile, ...]:
        return self.list_files(folder_id)

    def file_exists(self, file_id: str) -> bool:
        return file_id in self.metadata

    def download_file(self, file_id: str) -> bytes:
        self.download_calls += 1
        return b"should never be read by discovery"

    def test_connection(self, root_folder_id: str) -> DriveConnectionResult:
        return DriveConnectionResult(connected=True, root_name="CashCo Control Tower")


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values = {
        "google_auth_mode": "MOCK",
        "drive_root_folder_id": "root",
        "admin_earnings_folder_id": "admin",
        "payment_scope_folder_id": "payment",
        "rst_list_file_id": "rst-file",
        "source_registry_path": tmp_path / "registry.sqlite3",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def service(
    tmp_path: Path, drive: FakeReadOnlyDrive, **overrides: object
) -> SourceDiscoveryService:
    config = settings(tmp_path, **overrides)
    return SourceDiscoveryService(
        drive, config, SourceManifestRegistry(config.source_registry_path)
    )


def test_discovers_all_three_source_responsibilities(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    result = service(tmp_path, drive).discover(selected_period="2026-08-P1")
    assert [item.file.file_id for item in result.admin_earnings_files] == ["earn-1"]
    assert [
        (item.file.file_id, item.period_id) for item in result.payment_scope_files
    ] == [("scope-1", "2026-08-P1")]
    assert result.rst_list_file is not None
    assert result.rst_list_file.file.file_id == "rst-file"
    assert result.overall_health == HealthState.HEALTHY


def test_discovery_never_downloads_or_mutates_source_files(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    service(tmp_path, drive).discover(selected_period="2026-08-P1")
    assert drive.download_calls == 0
    assert not hasattr(SourceDiscoveryService, "delete_file")
    assert not hasattr(SourceDiscoveryService, "update_file")
    assert not hasattr(SourceDiscoveryService, "upload_file")


def test_payment_scope_missing_for_selected_period_is_blocking(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    drive.files["period-folder"] = ()
    result = service(tmp_path, drive).discover(selected_period="2026-08-P1")
    assert any(
        "missing for 2026-08-P1" in issue.message for issue in result.blocking_errors
    )


def test_multiple_payment_scope_candidates_are_not_silently_selected(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    drive.files["period-folder"] = (
        file("scope-1", "payment-a.xlsx", parent="period-folder"),
        file("scope-2", "payment-b.xlsx", parent="period-folder"),
    )
    result = service(tmp_path, drive).discover(selected_period="2026-08-P1")
    assert {item.source_status for item in result.payment_scope_files} == {
        SourceStatus.AMBIGUOUS
    }
    assert result.overall_health == HealthState.BLOCKING


def test_manual_period_mapping_required_when_structure_and_filename_are_unclear(
    tmp_path,
) -> None:
    drive = FakeReadOnlyDrive()
    drive.files["payment"] = (file("scope-x", "payment.xlsx", parent="payment"),)
    drive.folders["payment"] = ()
    result = service(tmp_path, drive).discover()
    assert (
        result.payment_scope_files[0].source_status
        == SourceStatus.MANUAL_MAPPING_REQUIRED
    )
    assert result.payment_scope_files[0].period_id is None


def test_explicit_period_mapping_beats_filename_guessing(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    drive.files["payment"] = (file("scope-x", "payment.xlsx", parent="payment"),)
    drive.folders["payment"] = ()
    result = service(
        tmp_path, drive, payment_scope_period_map={"2026-08-P2": "scope-x"}
    ).discover(selected_period="2026-08-P2")
    assert result.payment_scope_files[0].period_id == "2026-08-P2"


def test_rst_list_missing_is_blocking(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    result = service(tmp_path, drive, rst_list_file_id=None).discover()
    assert result.rst_list_file is None
    assert any(
        issue.source_type == SourceType.RST_LIST for issue in result.blocking_errors
    )


def test_rst_folder_with_multiple_candidates_is_ambiguous(tmp_path) -> None:
    drive = FakeReadOnlyDrive()
    drive.files["rst-folder"] = (
        file("rst-1", "RST A.xlsx"),
        file("rst-2", "RST B.xlsx"),
    )
    result = service(
        tmp_path, drive, rst_list_file_id=None, rst_list_folder_id="rst-folder"
    ).discover()
    assert result.rst_list_file is None
    assert any("Multiple RST List" in issue.message for issue in result.blocking_errors)


def test_drive_connection_error_is_sanitized(tmp_path) -> None:
    class BrokenDrive(FakeReadOnlyDrive):
        def test_connection(self, root_folder_id: str) -> DriveConnectionResult:
            raise DriveConnectionError("token=secret")

    result = service(tmp_path, BrokenDrive()).discover()
    assert result.connection_state == ConnectionState.ERROR
    assert "secret" not in result.blocking_errors[0].message


def test_drive_not_configured() -> None:
    result = SourceDiscoveryService.not_configured_result()
    assert result.connection_state == ConnectionState.NOT_CONFIGURED
    assert result.overall_health == HealthState.WARNING


def test_rst_list_is_enrichment_not_eligibility(tmp_path) -> None:
    result = service(tmp_path, FakeReadOnlyDrive()).discover(
        selected_period="2026-08-P1"
    )
    assert result.rst_list_file.source_type == SourceType.RST_LIST  # type: ignore[union-attr]
    assert SourceType.RST_LIST != SourceType.PAYMENT_SCOPE
    assert not hasattr(result.rst_list_file, "eligible")
