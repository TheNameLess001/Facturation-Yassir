from __future__ import annotations

import calendar
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.models.domain import SettlementPeriod


class SettlementPeriodService:
    def __init__(self, timezone_name: str = "Africa/Casablanca") -> None:
        self.timezone = ZoneInfo(timezone_name)

    def period_for(self, order_date: datetime) -> SettlementPeriod:
        if order_date.tzinfo is None:
            raise ValueError("order_date must be timezone-aware")
        local = order_date.astimezone(self.timezone)
        cycle = "P1" if local.day <= 15 else "P2"
        start_day = 1 if cycle == "P1" else 16
        end_day = (
            15 if cycle == "P1" else calendar.monthrange(local.year, local.month)[1]
        )
        return SettlementPeriod(
            period_id=f"{local.year:04d}-{local.month:02d}-{cycle}",
            start_at=datetime.combine(
                local.date().replace(day=start_day), time.min, self.timezone
            ),
            end_at=datetime.combine(
                local.date().replace(day=end_day), time.max, self.timezone
            ),
        )

    def get(self, period_id: str) -> SettlementPeriod:
        try:
            year_text, month_text, cycle = period_id.split("-")
            year, month = int(year_text), int(month_text)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid settlement period ID: {period_id}") from exc
        if cycle not in {"P1", "P2"} or month not in range(1, 13):
            raise ValueError(f"Invalid settlement period ID: {period_id}")
        anchor = datetime(year, month, 1, tzinfo=self.timezone)
        if cycle == "P2":
            anchor = anchor.replace(day=16)
        return self.period_for(anchor)
