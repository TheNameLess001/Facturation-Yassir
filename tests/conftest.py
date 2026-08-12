from datetime import UTC, datetime

import pytest

from src.ingestion.phase3_models import IngestionRunSummary, Phase3Result
from src.models.domain import SettlementPeriod


@pytest.fixture
def new_period() -> SettlementPeriod:
    return SettlementPeriod(
        period_id="2026-08-P1",
        start_at=datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 15, 23, 59, 59, tzinfo=UTC),
    )


@pytest.fixture
def empty_phase3_result() -> Phase3Result:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return Phase3Result(
        summary=IngestionRunSummary(
            run_id="fixed-run",
            started_at=now,
            completed_at=now,
            sources_selected=0,
            sources_read=0,
            source_failures=0,
            raw_rows=0,
            canonical_orders=0,
            identical_duplicate_rows=0,
            conflicting_order_ids=0,
            missing_order_id_rows=0,
            invalid_dates=0,
            invalid_financial_values=0,
            schema_warnings=0,
            schema_variants=0,
            blocking_issues=0,
            publish_status="VALIDATED_NOT_PUBLISHED",
        )
    )
