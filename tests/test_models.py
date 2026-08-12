from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.models.domain import SettlementPeriod
from src.models.enums import AutomationMode


def test_new_period_default_automation_state(new_period: SettlementPeriod) -> None:
    assert new_period.automation_mode == AutomationMode.OFF


def test_period_rejects_invalid_range() -> None:
    with pytest.raises(ValidationError, match="end_at must be after start_at"):
        SettlementPeriod(
            period_id="bad",
            start_at=datetime(2026, 8, 15, tzinfo=UTC),
            end_at=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_models_are_immutable(new_period: SettlementPeriod) -> None:
    with pytest.raises(ValidationError):
        new_period.automation_mode = AutomationMode.SEND_EMAILS
