from __future__ import annotations

import io
import json
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError, StorageArchitectureError
from src.google.models import DriveFile
from src.ingestion.phase3_models import Phase3Result

ARTIFACT_NAMES = (
    "canonical_orders.parquet",
    "duplicate_occurrences.parquet",
    "conflicting_duplicates.parquet",
    "ingestion_issues.parquet",
    "schema_report.json",
    "ingestion_summary.json",
)


def _json_default(value):
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


class ProcessedAdminEarningsStore:
    def build(self, result: Phase3Result) -> dict[str, bytes]:
        artifacts = {
            "canonical_orders.parquet": self._parquet(result.canonical_orders),
            "duplicate_occurrences.parquet": self._parquet(result.duplicate_occurrences),
            "conflicting_duplicates.parquet": self._parquet(result.conflicts),
            "ingestion_issues.parquet": self._parquet(result.issues),
            "schema_report.json": json.dumps(
                [item.model_dump(mode="json") for item in result.schema_profiles],
                indent=2,
                sort_keys=True,
            ).encode(),
            "ingestion_summary.json": result.summary.model_dump_json(indent=2).encode(),
        }
        if set(artifacts) != set(ARTIFACT_NAMES) or any(not content for content in artifacts.values()):
            raise ValueError("Processed artifact validation failed")
        return artifacts

    def publish(
        self,
        drive: GoogleDriveService,
        processed_folder_id: str,
        artifacts: dict[str, bytes],
    ) -> tuple[str, ...]:
        resolved = self.resolve_existing_artifacts(drive, processed_folder_id)
        previous = {name: drive.download_file(item.file_id) for name, item in resolved.items()}
        updated: list[str] = []
        try:
            for name in ARTIFACT_NAMES:
                mime = self._mime_type(name)
                drive.update_file_content(resolved[name].file_id, artifacts[name], mime)
                updated.append(name)
            for name in ARTIFACT_NAMES:
                if drive.download_file(resolved[name].file_id) != artifacts[name]:
                    raise StorageArchitectureError(
                        f"Processed artifact failed read-back validation: {name}"
                    )
        except GoogleIntegrationError:
            self._restore(drive, resolved, previous, updated)
            raise
        return tuple(updated)

    @staticmethod
    def resolve_existing_artifacts(
        drive: GoogleDriveService, processed_folder_id: str
    ) -> dict[str, DriveFile]:
        files = drive.list_files(processed_folder_id)
        resolved: dict[str, DriveFile] = {}
        for name in ARTIFACT_NAMES:
            matches = tuple(item for item in files if item.name == name)
            if len(matches) != 1:
                reason = "missing" if not matches else "duplicated"
                raise StorageArchitectureError(
                    f"Required Processed artifact is {reason}: {name}"
                )
            if not matches[0].capabilities.get("canEdit", False):
                raise StorageArchitectureError(
                    f"Required Processed artifact is not writable: {name}"
                )
            resolved[name] = matches[0]
        return resolved

    @classmethod
    def _restore(
        cls,
        drive: GoogleDriveService,
        resolved: dict[str, DriveFile],
        previous: dict[str, bytes],
        updated: list[str],
    ) -> None:
        for name in reversed(updated):
            drive.update_file_content(
                resolved[name].file_id, previous[name], cls._mime_type(name)
            )

    @staticmethod
    def _mime_type(name: str) -> str:
        return "application/json" if name.endswith(".json") else "application/octet-stream"

    @staticmethod
    def _parquet(models) -> bytes:
        rows = []
        for model in models:
            row = model.model_dump(mode="json")
            for key, value in tuple(row.items()):
                if isinstance(value, (dict, list)):
                    row[key] = json.dumps(value, default=_json_default, sort_keys=True)
            rows.append(row)
        frame = pd.DataFrame(rows)
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False)
        return buffer.getvalue()
