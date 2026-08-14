from __future__ import annotations

import csv
import hashlib
import io
from enum import StrEnum
from pathlib import PurePath

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class ReferenceArtifactType(StrEnum):
    PDF_INVOICE = "PDF_INVOICE"
    PDF_NOTE_DE_DEBOURS = "PDF_NOTE_DE_DEBOURS"
    EXCEL_SETTLEMENT = "EXCEL_SETTLEMENT"
    CSV_ORDER_EXPORT = "CSV_ORDER_EXPORT"


class ReferenceInspectionStatus(StrEnum):
    STRUCTURED_PROFILED = "STRUCTURED_PROFILED"
    UNSTRUCTURED_ACCEPTED = "UNSTRUCTURED_ACCEPTED"
    INVALID = "INVALID"


class ReferenceSheetProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    columns: tuple[str, ...]
    row_count: int = Field(ge=0)


class HistoricalReferenceProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str
    artifact_type: ReferenceArtifactType
    content_sha256: str
    size: int = Field(ge=0)
    status: ReferenceInspectionStatus
    sheets: tuple[ReferenceSheetProfile, ...] = ()
    notes: tuple[str, ...] = ()


class HistoricalReferenceImporter:
    """Memory-only schema inspection. It stores neither source bytes nor row values."""

    MAX_BYTES = 50 * 1024 * 1024

    def inspect(
        self,
        filename: str,
        content: bytes,
        *,
        pdf_type: ReferenceArtifactType = ReferenceArtifactType.PDF_INVOICE,
    ) -> HistoricalReferenceProfile:
        safe_name = PurePath(filename).name
        if not safe_name or not content:
            raise ValueError("A non-empty reference file is required")
        if len(content) > self.MAX_BYTES:
            raise ValueError("Reference file exceeds the 50 MB inspection limit")
        digest = hashlib.sha256(content).hexdigest()
        suffix = PurePath(safe_name).suffix.casefold()
        if suffix == ".pdf":
            if pdf_type not in {
                ReferenceArtifactType.PDF_INVOICE,
                ReferenceArtifactType.PDF_NOTE_DE_DEBOURS,
            }:
                raise ValueError("PDF type must be invoice or note de debours")
            if not content.startswith(b"%PDF"):
                raise ValueError("Invalid PDF signature")
            return HistoricalReferenceProfile(
                filename=safe_name,
                artifact_type=pdf_type,
                content_sha256=digest,
                size=len(content),
                status=ReferenceInspectionStatus.UNSTRUCTURED_ACCEPTED,
                notes=(
                    "PDF retained only for future human evidence review; no OCR was run.",
                ),
            )
        if suffix == ".csv":
            return self._csv(safe_name, content, digest)
        if suffix in {".xlsx", ".xls"}:
            return self._excel(safe_name, content, digest)
        raise ValueError("Supported references are PDF, XLSX/XLS, and CSV")

    @staticmethod
    def _csv(
        filename: str, content: bytes, digest: str
    ) -> HistoricalReferenceProfile:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV reference must use UTF-8 encoding") from exc
        rows = csv.reader(io.StringIO(text))
        try:
            header = next(rows)
        except StopIteration as exc:
            raise ValueError("CSV reference is empty") from exc
        columns = tuple(item.strip() for item in header)
        row_count = sum(1 for row in rows if any(cell.strip() for cell in row))
        return HistoricalReferenceProfile(
            filename=filename,
            artifact_type=ReferenceArtifactType.CSV_ORDER_EXPORT,
            content_sha256=digest,
            size=len(content),
            status=ReferenceInspectionStatus.STRUCTURED_PROFILED,
            sheets=(ReferenceSheetProfile(name="CSV", columns=columns, row_count=row_count),),
            notes=("Only schema and row count were retained; row values were discarded.",),
        )

    @staticmethod
    def _excel(
        filename: str, content: bytes, digest: str
    ) -> HistoricalReferenceProfile:
        try:
            workbook = pd.ExcelFile(io.BytesIO(content))
            sheets = []
            for sheet_name in workbook.sheet_names:
                frame = workbook.parse(sheet_name=sheet_name, dtype=object)
                sheets.append(
                    ReferenceSheetProfile(
                        name=str(sheet_name),
                        columns=tuple(str(item).strip() for item in frame.columns),
                        row_count=int(frame.dropna(how="all").shape[0]),
                    )
                )
        except Exception as exc:
            raise ValueError("Excel reference could not be profiled") from exc
        return HistoricalReferenceProfile(
            filename=filename,
            artifact_type=ReferenceArtifactType.EXCEL_SETTLEMENT,
            content_sha256=digest,
            size=len(content),
            status=ReferenceInspectionStatus.STRUCTURED_PROFILED,
            sheets=tuple(sheets),
            notes=("Only sheet schemas and row counts were retained; values were discarded.",),
        )
