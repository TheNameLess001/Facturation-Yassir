from __future__ import annotations

import io
import time

import pandas as pd

from src.google.exceptions import GoogleIntegrationError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import DriveFile
from src.ingestion.phase2_discovery import (
    CSV_MIME_TYPES,
    GOOGLE_SHEETS_MIME_TYPE,
    XLSX_MIME_TYPE,
)


class AdminEarningsReader:
    def __init__(self, drive: ReadOnlyDriveService, *, attempts: int = 3) -> None:
        self.drive = drive
        self.attempts = attempts

    def read(self, file: DriveFile) -> pd.DataFrame:
        error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return self._read_once(file)
            except (GoogleIntegrationError, OSError, TypeError, ValueError) as exc:
                error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(0.25 * (attempt + 1))
        raise RuntimeError("Admin Earnings source could not be read safely") from error

    def _read_once(self, file: DriveFile) -> pd.DataFrame:
        if file.mime_type == GOOGLE_SHEETS_MIME_TYPE:
            content = self.drive.export_file(file.file_id, "text/csv")
            return self._csv(content)
        content = self.drive.download_file(file.file_id)
        if file.mime_type in CSV_MIME_TYPES:
            return self._csv(content)
        if file.mime_type == XLSX_MIME_TYPE:
            return pd.read_excel(io.BytesIO(content), dtype=object, engine="openpyxl")
        raise ValueError("Unsupported Admin Earnings content type")

    @staticmethod
    def _csv(content: bytes) -> pd.DataFrame:
        return pd.read_csv(
            io.BytesIO(content),
            dtype=object,
            keep_default_na=False,
            encoding="utf-8-sig",
            sep=None,
            engine="python",
        )
