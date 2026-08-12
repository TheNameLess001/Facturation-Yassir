from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.google.exceptions import SourceDiscoveryError
from src.google.interfaces import ReadOnlyDriveService
from src.google.models import DriveFile
from src.ingestion.phase2_discovery import GOOGLE_SHEETS_MIME_TYPE, XLSX_MIME_TYPE
from src.restaurants.registry_models import (
    InvoiceScopeSchemaProfile,
    RSTSchemaProfile,
    WorksheetSchemaProfile,
)

XLSX_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME_TYPES = frozenset({"text/csv", "application/csv", "text/plain"})


@dataclass(frozen=True, slots=True)
class ProfiledFrame:
    frame: pd.DataFrame
    profile: InvoiceScopeSchemaProfile | RSTSchemaProfile


class RestaurantSourceReader:
    """Read-only, in-memory reader for Invoice Scope and RST sources."""

    def __init__(self, drive: ReadOnlyDriveService) -> None:
        self.drive = drive

    def read_invoice_scope(
        self, file_id: str, *, active_worksheet: str
    ) -> ProfiledFrame:
        metadata = self.drive.get_file_metadata(file_id)
        if metadata.mime_type == GOOGLE_SHEETS_MIME_TYPE:
            content = self.drive.export_file(file_id, XLSX_EXPORT_MIME)
        elif metadata.mime_type == XLSX_MIME_TYPE:
            content = self.drive.download_file(file_id)
        else:
            raise SourceDiscoveryError(
                "Invoice Scope must be a Google Sheet or XLSX workbook."
            )
        workbook = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
        profiles: list[WorksheetSchemaProfile] = []
        active_frame: pd.DataFrame | None = None
        for worksheet in workbook.sheet_names:
            frame, profile = self._read_worksheet(workbook, worksheet)
            profiles.append(profile)
            if worksheet == active_worksheet:
                active_frame = frame
        if active_frame is None:
            raise SourceDiscoveryError(
                f"Configured Invoice Scope worksheet was not found: {active_worksheet}"
            )
        return ProfiledFrame(
            frame=active_frame,
            profile=InvoiceScopeSchemaProfile(
                file_id=metadata.file_id,
                filename=metadata.name,
                mime_type=metadata.mime_type,
                active_worksheet=active_worksheet,
                worksheets=tuple(profiles),
                profiled_at=datetime.now(UTC),
            ),
        )

    def read_rst(self, file_id: str) -> ProfiledFrame:
        metadata = self.drive.get_file_metadata(file_id)
        content = self.drive.download_file(file_id)
        if metadata.mime_type in CSV_MIME_TYPES or metadata.name.casefold().endswith(
            ".csv"
        ):
            frame = self._read_csv(content)
        elif metadata.mime_type == XLSX_MIME_TYPE or metadata.name.casefold().endswith(
            ".xlsx"
        ):
            frame = pd.read_excel(
                io.BytesIO(content), dtype=object, engine="openpyxl"
            )
        else:
            raise SourceDiscoveryError("RST List has an unsupported content type.")
        profile = self._rst_profile(metadata, frame)
        return ProfiledFrame(frame=frame, profile=profile)

    @classmethod
    def profile_invoice_frame(
        cls,
        frame: pd.DataFrame,
        *,
        file_id: str = "invoice-scope",
        filename: str = "Invoice Scope.xlsx",
        worksheet: str = "CASH-CO",
    ) -> InvoiceScopeSchemaProfile:
        return InvoiceScopeSchemaProfile(
            file_id=file_id,
            filename=filename,
            mime_type=XLSX_MIME_TYPE,
            active_worksheet=worksheet,
            worksheets=(cls._worksheet_profile(worksheet, 1, frame),),
            profiled_at=datetime.now(UTC),
        )

    @classmethod
    def profile_rst_frame(
        cls,
        frame: pd.DataFrame,
        *,
        file_id: str = "rst",
        filename: str = "RST_List.csv",
    ) -> RSTSchemaProfile:
        return cls._rst_profile(
            DriveFile(
                file_id=file_id,
                name=filename,
                mime_type="text/csv",
                modified_time=datetime.now(UTC),
            ),
            frame,
        )

    @classmethod
    def _read_worksheet(
        cls, workbook: pd.ExcelFile, worksheet: str
    ) -> tuple[pd.DataFrame, WorksheetSchemaProfile]:
        probe = pd.read_excel(
            workbook, sheet_name=worksheet, header=None, nrows=25, dtype=object
        )
        candidates = [index for index, row in probe.iterrows() if row.notna().any()]
        header_index = candidates[0] if candidates else 0
        frame = pd.read_excel(
            workbook, sheet_name=worksheet, header=header_index, dtype=object
        )
        frame.columns = tuple(str(column).strip() for column in frame.columns)
        return frame, cls._worksheet_profile(worksheet, header_index + 1, frame)

    @classmethod
    def _worksheet_profile(
        cls, worksheet: str, header_row: int, frame: pd.DataFrame
    ) -> WorksheetSchemaProfile:
        blank = cls._blank_row_mask(frame)
        populated = frame.loc[~blank]
        return WorksheetSchemaProfile(
            worksheet_name=worksheet,
            header_row=header_row,
            columns=tuple(str(column) for column in frame.columns),
            row_count=len(populated),
            blank_rows=int(blank.sum()),
            duplicate_rows=int(populated.duplicated().sum()),
            field_types=cls._field_types(populated),
        )

    @classmethod
    def _rst_profile(cls, metadata: DriveFile, frame: pd.DataFrame) -> RSTSchemaProfile:
        blank = cls._blank_row_mask(frame)
        populated = frame.loc[~blank]
        return RSTSchemaProfile(
            file_id=metadata.file_id,
            filename=metadata.name,
            mime_type=metadata.mime_type,
            columns=tuple(str(column) for column in frame.columns),
            row_count=len(populated),
            blank_rows=int(blank.sum()),
            duplicate_rows=int(populated.duplicated().sum()),
            field_types=cls._field_types(populated),
            profiled_at=datetime.now(UTC),
        )

    @staticmethod
    def _read_csv(content: bytes) -> pd.DataFrame:
        text = content.decode("utf-8-sig", errors="replace")
        sample = "\n".join(text.splitlines()[:25])
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ";"
        return pd.read_csv(
            io.StringIO(text), sep=delimiter, dtype=object, keep_default_na=False
        )

    @staticmethod
    def _blank_row_mask(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(False, index=frame.index, dtype=bool)
        return frame.apply(
            lambda row: all(
                value is None
                or (isinstance(value, float) and math.isnan(value))
                or not str(value).strip()
                for value in row
            ),
            axis=1,
        )

    @staticmethod
    def _field_types(frame: pd.DataFrame) -> dict[str, str]:
        result: dict[str, str] = {}
        for column in frame.columns:
            kinds: set[str] = set()
            for value in frame[column]:
                if (
                    value is None
                    or (isinstance(value, float) and math.isnan(value))
                    or not str(value).strip()
                ):
                    continue
                if isinstance(value, bool):
                    kinds.add("boolean")
                elif isinstance(value, (int, float, Decimal)):
                    kinds.add("number")
                elif isinstance(value, (datetime, pd.Timestamp)):
                    kinds.add("datetime")
                else:
                    kinds.add("string")
                if len(kinds) > 1:
                    break
            result[str(column)] = "empty" if not kinds else next(iter(kinds)) if len(kinds) == 1 else "mixed"
        return result
