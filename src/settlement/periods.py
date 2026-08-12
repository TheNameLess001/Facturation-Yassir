from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.settlement.phase5_models import (
    SettlementHalf,
    SettlementPeriod,
    SettlementPeriodStatus,
)

MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class SettlementPeriodService:
    """Assign periods from the actual order calendar date, never source metadata."""

    def __init__(self, timezone_name: str = "Africa/Casablanca") -> None:
        self.timezone = ZoneInfo(timezone_name)

    def period_for(
        self,
        order_date: datetime | date,
        *,
        as_of: date | None = None,
    ) -> SettlementPeriod:
        if isinstance(order_date, datetime):
            if order_date.tzinfo is None:
                raise ValueError("order_date must be timezone-aware")
            actual_date = order_date.astimezone(self.timezone).date()
        else:
            actual_date = order_date
        half = SettlementHalf.P1 if actual_date.day <= 15 else SettlementHalf.P2
        return self.create(actual_date.year, actual_date.month, half, as_of=as_of)

    def create(
        self,
        year: int,
        month: int,
        half: SettlementHalf | str,
        *,
        as_of: date | None = None,
    ) -> SettlementPeriod:
        selected_half = SettlementHalf(half)
        if month not in range(1, 13):
            raise ValueError("Settlement month must be between 1 and 12")
        start_day = 1 if selected_half == SettlementHalf.P1 else 16
        end_day = (
            15
            if selected_half == SettlementHalf.P1
            else calendar.monthrange(year, month)[1]
        )
        start_date = date(year, month, start_day)
        end_date = date(year, month, end_day)
        comparison_date = as_of or datetime.now(self.timezone).date()
        if end_date < comparison_date:
            status = SettlementPeriodStatus.COMPLETE
        elif start_date <= comparison_date <= end_date:
            status = SettlementPeriodStatus.OPEN_INCOMPLETE
        else:
            status = SettlementPeriodStatus.FUTURE
        period_code = f"{year:04d}-{month:02d}-{selected_half.value}"
        return SettlementPeriod(
            year=year,
            month=month,
            half=selected_half,
            start_date=start_date,
            end_date=end_date,
            start_at=datetime.combine(start_date, time.min, self.timezone),
            end_at=datetime.combine(end_date, time.max, self.timezone),
            period_code=period_code,
            display_name=f"{MONTH_NAMES[month]} {year} · {selected_half.value}",
            status=status,
        )

    def get(
        self,
        period_id: str,
        *,
        as_of: date | None = None,
    ) -> SettlementPeriod:
        try:
            year_text, month_text, half = period_id.split("-")
            year, month = int(year_text), int(month_text)
            selected_half = SettlementHalf(half)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid settlement period ID: {period_id}") from exc
        return self.create(year, month, selected_half, as_of=as_of)

    def latest_complete(self, *, as_of: date | None = None) -> SettlementPeriod:
        comparison_date = as_of or datetime.now(self.timezone).date()
        if comparison_date.day > 15:
            return self.create(
                comparison_date.year,
                comparison_date.month,
                SettlementHalf.P1,
                as_of=comparison_date,
            )
        previous_month_last_day = comparison_date.replace(day=1) - timedelta(days=1)
        return self.create(
            previous_month_last_day.year,
            previous_month_last_day.month,
            SettlementHalf.P2,
            as_of=comparison_date,
        )
