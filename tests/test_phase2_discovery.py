from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.config import Settings
from src.google.models import AccessLevel, DriveAccessResult, DriveFile
from src.ingestion.admin_earnings_filename import parse_admin_earnings_filename
from src.ingestion.phase2_discovery import Phase2SourceDiscoveryService
from src.ingestion.phase2_models import IgnoredFileReason, ReadinessState
from src.ingestion.registry import SourceManifestRegistry
from src.models.enums import AutomationMode, ChangeState, HealthState

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def drive_file(file_id: str, name: str, *, modified_offset: int = 0) -> DriveFile:
    return DriveFile(
        file_id=file_id,
        name=name,
        mime_type="text/csv" if name.casefold().endswith(".csv") else XLSX,
        modified_time=datetime(2026, 8, 12, tzinfo=UTC) + timedelta(minutes=modified_offset),
        size=100 + modified_offset,
        md5_checksum=f"hash-{file_id}-{modified_offset}",
    )


class Phase2Drive:
    def __init__(self) -> None:
        self.admin_files = (
            drive_file("week-1", "data week 1_2026.csv"),
            drive_file("week-9", "DATA WEEK 9_2026.XLSX"),
            drive_file("week-31", "data week 31_2026.csv"),
            drive_file("week-52", "data week 52_2026.csv"),
            drive_file("week-53", "data week 53_2026.xlsx"),
            drive_file("ignored", "backup.csv"),
        )
        self.metadata = {
            "rst": drive_file("rst", "RST List.xlsx"),
            "invoice": DriveFile(
                file_id="invoice",
                name="Invoice Scope",
                mime_type="application/vnd.google-apps.spreadsheet",
                modified_time=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        }

    def list_files(self, folder_id):
        return self.admin_files if folder_id == "admin" else ()

    def check_access(self, object_id, *, location, folder, require_write):
        item = self.metadata.get(object_id)
        if item is None:
            item = DriveFile(
                file_id=object_id,
                name=location,
                mime_type="application/vnd.google-apps.folder",
                modified_time=datetime(2026, 8, 12, tzinfo=UTC),
                is_folder=True,
            )
        return DriveAccessResult(
            location=location,
            object_id=object_id,
            access=AccessLevel.READ_WRITE if require_write else AccessLevel.READABLE,
            readable=True,
            writable=True if require_write else None,
            object=item,
        )


def configured_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        admin_earnings_folder_id="admin",
        rst_list_file_id="rst",
        invoice_scope_file_id="invoice",
        config_folder_id="config",
        processed_folder_id="processed",
        partners_folder_id="partners",
        documents_folder_id="documents",
        audit_folder_id="audit",
        source_registry_path=tmp_path / "manifest.sqlite3",
    )


def discover(tmp_path, drive=None):
    settings = configured_settings(tmp_path)
    return Phase2SourceDiscoveryService(
        drive or Phase2Drive(), settings, SourceManifestRegistry(settings.source_registry_path)
    ).discover()


@pytest.mark.parametrize("week", [1, 9, 31, 52, 53])
@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_filename_accepts_valid_weeks_and_extensions(week, extension) -> None:
    parsed = parse_admin_earnings_filename(f"DaTa WeEk {week}_2026.{extension.upper()}")
    assert parsed is not None
    assert parsed.week == week
    assert parsed.extension == f".{extension}"


@pytest.mark.parametrize("name", ["data week 01_2026.csv", "data week 0_2026.csv", "data week 54_2026.xlsx"])
def test_filename_rejects_invalid_week(name) -> None:
    assert parse_admin_earnings_filename(name) is None


def test_phase2_discovers_admin_invoice_scope_rst_and_workspace(tmp_path) -> None:
    result = discover(tmp_path)
    assert len(result.valid_admin_files) == 5
    assert result.ignored_admin_files[0].reason == IgnoredFileReason.INVALID_FILENAME
    assert result.rst_list is not None
    assert result.invoice_scope is not None
    assert result.health.workspace == HealthState.HEALTHY
    assert result.health.overall == ReadinessState.READY_FOR_INGESTION
    assert AutomationMode.OFF.value == "OFF"


def test_finance_tracking_is_not_required_or_checked(tmp_path) -> None:
    drive = Phase2Drive()
    result = discover(tmp_path, drive)
    assert result.health.overall == ReadinessState.READY_FOR_INGESTION
    assert all("Finance" not in item.location for item in result.access)


def test_invoice_scope_health_is_blocking_when_not_readable(tmp_path) -> None:
    class MissingInvoiceScope(Phase2Drive):
        def check_access(self, object_id, *, location, folder, require_write):
            result = super().check_access(
                object_id,
                location=location,
                folder=folder,
                require_write=require_write,
            )
            if location == "Invoice Scope":
                return result.model_copy(
                    update={
                        "access": AccessLevel.INACCESSIBLE,
                        "readable": False,
                        "object": None,
                    }
                )
            return result

    result = discover(tmp_path, MissingInvoiceScope())
    assert result.health.invoice_scope == HealthState.BLOCKING
    assert result.health.overall == ReadinessState.BLOCKING


def test_manifest_states_new_unchanged_and_modified(tmp_path) -> None:
    drive = Phase2Drive()
    first = discover(tmp_path, drive)
    assert first.valid_admin_files[0].change_state == ChangeState.NEW
    second = discover(tmp_path, drive)
    assert second.valid_admin_files[0].change_state == ChangeState.UNCHANGED
    files = list(drive.admin_files)
    files[0] = drive_file("week-1", "data week 1_2026.csv", modified_offset=1)
    drive.admin_files = tuple(files)
    third = discover(tmp_path, drive)
    assert third.valid_admin_files[0].change_state == ChangeState.MODIFIED


def test_workspace_read_only_is_blocking(tmp_path) -> None:
    class ReadOnlyWorkspace(Phase2Drive):
        def check_access(self, object_id, *, location, folder, require_write):
            result = super().check_access(object_id, location=location, folder=folder, require_write=require_write)
            if location == "Audit":
                return result.model_copy(update={"access": AccessLevel.READ_ONLY, "writable": False})
            return result

    result = discover(tmp_path, ReadOnlyWorkspace())
    assert result.health.workspace == HealthState.BLOCKING
    assert result.health.overall == ReadinessState.BLOCKING
