from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from src.config import Settings
from src.google.drive_service import GoogleDriveService
from src.ingestion.admin_earnings_filename import parse_admin_earnings_filename
from src.ingestion.admin_earnings_normalizer import AdminEarningsNormalizer
from src.ingestion.admin_earnings_reader import AdminEarningsReader
from src.ingestion.admin_earnings_schema import CRITICAL_FIELDS
from src.ingestion.deduplication import deduplicate_orders
from src.ingestion.phase2_discovery import SUPPORTED_ADMIN_MIME_TYPES
from src.ingestion.phase3_models import (
    IngestionIssueRecord,
    IngestionRunSummary,
    IssueSeverity,
    Phase3Result,
    SourceIngestionResult,
)
from src.ingestion.processed_store import ProcessedAdminEarningsStore
from src.ingestion.schema_profiler import SchemaProfiler

LOGGER = logging.getLogger(__name__)


class Phase3AdminEarningsService:
    def __init__(
        self,
        drive: GoogleDriveService,
        settings: Settings,
        *,
        publish: bool = True,
    ) -> None:
        self.drive = drive
        self.settings = settings
        self.publish_outputs = publish
        self.reader = AdminEarningsReader(drive)
        self.normalizer = AdminEarningsNormalizer()
        self.store = ProcessedAdminEarningsStore()

    def run(self) -> Phase3Result:
        started = datetime.now(UTC)
        run_id = str(uuid4())
        files = tuple(
            file
            for file in self.drive.list_files(self.settings.admin_earnings_folder_id)
            if parse_admin_earnings_filename(file.name)
            and file.mime_type in SUPPORTED_ADMIN_MIME_TYPES
        )
        profiler = SchemaProfiler()
        normalized = []
        issues: list[IngestionIssueRecord] = []
        source_results: list[SourceIngestionResult] = []
        source_failures = 0
        raw_rows = 0
        for file in sorted(files, key=lambda item: (item.name.casefold(), item.file_id)):
            file_started = datetime.now(UTC)
            parsed = parse_admin_earnings_filename(file.name)
            assert parsed is not None
            try:
                frame = self.reader.read(file)
                raw_rows += len(frame)
                mapping, ambiguous = profiler.add(
                    file.name, frame, self.settings.admin_earnings_column_map
                )
                if ambiguous:
                    issues.append(
                        IngestionIssueRecord(
                            category="AMBIGUOUS_SCHEMA_MAPPING",
                            severity=IssueSeverity.BLOCKING,
                            message="Multiple source columns map to one canonical field.",
                            raw_value=", ".join(sorted(ambiguous)),
                        )
                    )
                missing = CRITICAL_FIELDS - mapping.keys()
                if missing:
                    issues.append(
                        IngestionIssueRecord(
                            category="SCHEMA_MISMATCH",
                            severity=IssueSeverity.BLOCKING,
                            message=f"Missing critical fields: {', '.join(sorted(missing))}.",
                        )
                    )
                records, row_issues = self.normalizer.normalize_frame(
                    frame,
                    mapping,
                    file,
                    parsed.week,
                    parsed.year,
                    file.modified_time,
                )
                normalized.extend(records)
                issues.extend(row_issues)
                order_counts = Counter(item.order_id for item in records)
                source_results.append(
                    SourceIngestionResult(
                        file_id=file.file_id,
                        filename=file.name,
                        week=parsed.week,
                        year=parsed.year,
                        modified_at=file.modified_time,
                        checksum=file.md5_checksum,
                        rows_read=len(frame),
                        rows_valid=len(records),
                        rows_with_issues=len(row_issues),
                        unique_order_ids=len(order_counts),
                        duplicate_occurrences=sum(max(0, count - 1) for count in order_counts.values()),
                        ingestion_started_at=file_started,
                        ingestion_completed_at=datetime.now(UTC),
                        status="COMPLETED_WITH_ISSUES" if row_issues or ambiguous or missing else "SUCCESS",
                    )
                )
            except Exception:
                source_failures += 1
                LOGGER.exception("admin_source_read_failed", extra={"source_file_id": file.file_id})
                issues.append(
                    IngestionIssueRecord(
                        category="SOURCE_READ_FAILURE",
                        severity=IssueSeverity.BLOCKING,
                        message=f"{file.name} could not be read after controlled retries.",
                    )
                )
                source_results.append(
                    SourceIngestionResult(
                        file_id=file.file_id,
                        filename=file.name,
                        week=parsed.week,
                        year=parsed.year,
                        modified_at=file.modified_time,
                        checksum=file.md5_checksum,
                        ingestion_started_at=file_started,
                        ingestion_completed_at=datetime.now(UTC),
                        status="FAILED",
                    )
                )
        canonical, duplicate_rows, conflicts, duplicate_issues = deduplicate_orders(normalized)
        issues.extend(duplicate_issues)
        profiles = profiler.profiles()
        completed = datetime.now(UTC)
        dates = [item.order_date for item in canonical if item.order_date]
        counts = Counter(item.category for item in issues)
        summary = IngestionRunSummary(
            run_id=run_id,
            started_at=started,
            completed_at=completed,
            sources_selected=len(files),
            sources_read=len(files) - source_failures,
            source_failures=source_failures,
            raw_rows=raw_rows,
            canonical_orders=len(canonical),
            identical_duplicate_rows=sum(x.classification.value == "IDENTICAL_DUPLICATE" and not x.retained for x in duplicate_rows),
            conflicting_order_ids=len(conflicts),
            missing_order_id_rows=counts["MISSING_ORDER_ID"],
            invalid_dates=counts["INVALID_DATE"],
            invalid_financial_values=counts["INVALID_FINANCIAL_VALUE"],
            schema_warnings=sum(bool(x.missing_critical_fields or x.ambiguous_mappings) for x in profiles),
            schema_variants=len(profiles),
            blocking_issues=sum(x.severity == IssueSeverity.BLOCKING for x in issues),
            publish_status=(
                "FAILED_NOT_PUBLISHED"
                if source_failures
                else "PUBLISHED"
                if self.publish_outputs
                else "VALIDATED_NOT_PUBLISHED"
            ),
            min_order_date=min(dates) if dates else None,
            max_order_date=max(dates) if dates else None,
        )
        result = Phase3Result(
            summary=summary,
            canonical_orders=tuple(canonical),
            duplicate_occurrences=tuple(duplicate_rows),
            conflicts=tuple(conflicts),
            issues=tuple(issues),
            schema_profiles=profiles,
            source_results=tuple(source_results),
        )
        artifacts = self.store.build(result)
        published = ()
        if self.publish_outputs and not source_failures:
            if not self.settings.processed_folder_id:
                raise ValueError("Processed folder is not configured")
            published = self.store.publish(
                self.drive, self.settings.processed_folder_id, artifacts
            )
        return result.model_copy(update={"artifacts": published or tuple(artifacts)})
