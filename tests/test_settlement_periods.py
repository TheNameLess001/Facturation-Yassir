from datetime import UTC, datetime

import pytest

from src.settlement.periods import SettlementPeriodService


@pytest.mark.parametrize(
    ("value", "period"),
    [
        (datetime(2026, 8, 1, tzinfo=UTC), "2026-08-P1"),
        (datetime(2026, 8, 15, 22, 59, 59, tzinfo=UTC), "2026-08-P1"),
        (datetime(2026, 8, 15, 23, tzinfo=UTC), "2026-08-P2"),
        (datetime(2026, 8, 31, 22, 59, 59, tzinfo=UTC), "2026-08-P2"),
        (datetime(2026, 2, 28, 12, tzinfo=UTC), "2026-02-P2"),
        (datetime(2024, 2, 29, 12, tzinfo=UTC), "2024-02-P2"),
        (datetime(2026, 12, 31, 12, tzinfo=UTC), "2026-12-P2"),
    ],
)
def test_period_assignment_uses_morocco_local_time(
    value: datetime, period: str
) -> None:
    assert SettlementPeriodService().period_for(value).period_id == period


def test_period_boundaries_cover_full_local_days() -> None:
    period = SettlementPeriodService().get("2026-02-P2")
    assert period.start_at.day == 16
    assert period.end_at.day == 28
    assert period.end_at.hour == 23
    assert period.end_at.microsecond == 999999


def test_naive_order_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SettlementPeriodService().period_for(datetime(2026, 8, 1))  # noqa: DTZ001
