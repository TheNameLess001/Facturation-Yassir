from datetime import UTC, datetime

import pytest

from src.models.domain import SettlementPeriod


@pytest.fixture
def new_period() -> SettlementPeriod:
    return SettlementPeriod(
        period_id="2026-08-P1",
        start_at=datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 15, 23, 59, 59, tzinfo=UTC),
    )
