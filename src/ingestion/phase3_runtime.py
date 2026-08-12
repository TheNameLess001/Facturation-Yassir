from __future__ import annotations

import json

from pydantic import ValidationError

from src.config import Settings, get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleIntegrationError
from src.ingestion.phase3_models import IngestionRunSummary, Phase3Result
from src.ingestion.phase3_service import Phase3AdminEarningsService


def run_phase3_ingestion(settings: Settings | None = None) -> Phase3Result:
    settings = settings or get_settings()
    drive = GoogleDriveService(build_google_credentials(settings))
    return Phase3AdminEarningsService(drive, settings, publish=True).run()


def load_latest_ingestion_summary(
    settings: Settings | None = None,
) -> IngestionRunSummary | None:
    settings = settings or get_settings()
    if not settings.google_credentials_configured or not settings.processed_folder_id:
        return None
    try:
        drive = GoogleDriveService(build_google_credentials(settings))
        summaries = tuple(
            item
            for item in drive.list_files(settings.processed_folder_id)
            if item.name == "ingestion_summary.json"
        )
        if not summaries:
            return None
        payload = json.loads(drive.download_file(summaries[0].file_id))
        return IngestionRunSummary.model_validate(payload)
    except (GoogleIntegrationError, json.JSONDecodeError, ValidationError, UnicodeDecodeError):
        return None
