from __future__ import annotations

import io
import json

import pandas as pd

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.ingestion.admin_earnings_filename import parse_admin_earnings_filename
from src.ingestion.admin_earnings_normalizer import normalize_identifier
from src.ingestion.admin_earnings_reader import AdminEarningsReader
from src.ingestion.admin_earnings_schema import resolve_schema
from src.ingestion.conflict_diagnostics import ConflictDiagnosticsService
from src.ingestion.conflict_diagnostics_models import ConflictDiagnostics
from src.ingestion.phase2_discovery import SUPPORTED_ADMIN_MIME_TYPES

PHASE3_ARTIFACT_IDS = {
    "canonical_orders.parquet": "1x7V6as3ujbWvgKkAOzGXQ1sgwlPj1LD8",
    "duplicate_occurrences.parquet": "15X8lUQpNE8s7Kw8Dqvh1i14GXUB-bFfQ",
    "conflicting_duplicates.parquet": "1hBi1XbtMJv6qKlRiYADZbpYhAODeGV-t",
    "ingestion_issues.parquet": "1Cut9xhL1_s5CXztYh-1Uc0DvQfb6yKxG",
    "schema_report.json": "1LFqqvnbTWW8Ws9mpVYkTKZbpF_Ttiyhb",
}


def run_conflict_diagnostics(
    settings: Settings | None = None,
) -> ConflictDiagnostics:
    settings = settings or get_settings()
    drive = GoogleDriveService(build_google_credentials(settings))
    frames = {
        name: pd.read_parquet(io.BytesIO(drive.download_file(file_id)))
        for name, file_id in PHASE3_ARTIFACT_IDS.items()
        if name.endswith(".parquet")
    }
    conflicts = frames["conflicting_duplicates.parquet"]
    canonical = frames["canonical_orders.parquet"]
    conflict_ids = set(conflicts["order_id"].astype(str))
    if conflict_ids.intersection(canonical["order_id"].astype(str)):
        raise ValueError("Conflicting orders unexpectedly appear in canonical output")
    schema_report = json.loads(
        drive.download_file(PHASE3_ARTIFACT_IDS["schema_report.json"])
    )
    source_columns = _source_column_index(schema_report)
    observations = _restaurant_observations(drive, settings)
    return ConflictDiagnosticsService().analyze(
        conflicts,
        frames["duplicate_occurrences.parquet"],
        frames["ingestion_issues.parquet"],
        restaurant_observations=observations,
        source_columns=source_columns,
    )


def _source_column_index(schema_report: object) -> dict[tuple[str, str], str]:
    if not isinstance(schema_report, list):
        return {}
    result: dict[tuple[str, str], str] = {}
    for profile in schema_report:
        if not isinstance(profile, dict):
            continue
        files = profile.get("files", [])
        mapping = profile.get("canonical_mapping", {})
        if not isinstance(files, list) or not isinstance(mapping, dict):
            continue
        for filename in files:
            for field, column in mapping.items():
                result[(str(filename), str(field))] = str(column)
    return result


def _restaurant_observations(
    drive: GoogleDriveService, settings: Settings
) -> pd.DataFrame:
    reader = AdminEarningsReader(drive)
    observations: list[pd.DataFrame] = []
    files = tuple(
        file
        for file in drive.list_files(settings.admin_earnings_folder_id)
        if parse_admin_earnings_filename(file.name)
        and file.mime_type in SUPPORTED_ADMIN_MIME_TYPES
    )
    for file in files:
        frame = reader.read(file)
        mapping, ambiguous = resolve_schema(
            frame, settings.admin_earnings_column_map
        )
        if {"order_id", "restaurant_id"}.intersection(ambiguous) or not {
            "order_id",
            "restaurant_id",
        }.issubset(mapping):
            continue
        subset = pd.DataFrame(
            {
                "order_id": frame[mapping["order_id"]].map(normalize_identifier),
                "restaurant_id": frame[mapping["restaurant_id"]].map(
                    normalize_identifier
                ),
                "restaurant_name": (
                    frame[mapping["restaurant_name"]].map(_optional_text)
                    if "restaurant_name" in mapping
                    else None
                ),
            }
        )
        observations.append(subset.dropna(subset=["order_id", "restaurant_id"]))
    if not observations:
        return pd.DataFrame(
            columns=["order_id", "restaurant_id", "restaurant_name"]
        )
    return pd.concat(observations, ignore_index=True).drop_duplicates()


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
