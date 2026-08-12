from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from src.models.domain import AuditEvent
from src.models.enums import AuditLevel, FinancialDecision


class OverrideReasonCode(StrEnum):
    RESTAURANT_CONFIRMED = "RESTAURANT_CONFIRMED"
    CUSTOMER_CANCELLATION = "CUSTOMER_CANCELLATION"
    YASSIR_OPERATIONAL_ERROR = "YASSIR_OPERATIONAL_ERROR"
    COURIER_ERROR = "COURIER_ERROR"
    COMPENSATION_APPROVED = "COMPENSATION_APPROVED"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    INVALID_ORDER = "INVALID_ORDER"
    PARTNER_DISPUTE = "PARTNER_DISPUTE"
    FINANCE_ADJUSTMENT = "FINANCE_ADJUSTMENT"
    OTHER = "OTHER"


class FinancialOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    override_id: UUID
    period_code: str
    restaurant_id: str
    order_id: str
    previous_decision: FinancialDecision
    new_decision: FinancialDecision
    reason_code: OverrideReasonCode
    comment: str | None = None
    created_by: str
    created_at: datetime
    source_engine_version: str
    source_decision_rule: str
    supersedes_override_id: UUID | None = None

    @model_validator(mode="after")
    def validate_override(self) -> FinancialOverride:
        if self.previous_decision == self.new_decision:
            raise ValueError("Override must change the final financial decision")
        if self.reason_code == OverrideReasonCode.OTHER and not (
            self.comment and self.comment.strip()
        ):
            raise ValueError("OTHER override reason requires a comment")
        if not self.created_by.strip():
            raise ValueError("Override creator is required")
        return self


class FinancialOverrideRepository:
    """Append-only SQLite history. No update or delete operation is exposed."""

    def __init__(self, database_path: Path | str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append(self, override: FinancialOverride) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO financial_overrides (
                    override_id, period_code, restaurant_id, order_id,
                    previous_decision, new_decision, reason_code, comment,
                    created_by, created_at, source_engine_version,
                    source_decision_rule, supersedes_override_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(override.override_id),
                    override.period_code,
                    override.restaurant_id,
                    override.order_id,
                    override.previous_decision.value,
                    override.new_decision.value,
                    override.reason_code.value,
                    override.comment,
                    override.created_by,
                    override.created_at.isoformat(),
                    override.source_engine_version,
                    override.source_decision_rule,
                    str(override.supersedes_override_id)
                    if override.supersedes_override_id
                    else None,
                ),
            )

    def list_for_period(self, period_code: str) -> tuple[FinancialOverride, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM financial_overrides
                WHERE period_code=? ORDER BY created_at, override_id
                """,
                (period_code,),
            ).fetchall()
        return tuple(self._model(row) for row in rows)

    def list_for_order(
        self, period_code: str, order_id: str
    ) -> tuple[FinancialOverride, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM financial_overrides
                WHERE period_code=? AND order_id=? ORDER BY created_at, override_id
                """,
                (period_code, order_id),
            ).fetchall()
        return tuple(self._model(row) for row in rows)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_overrides (
                    override_id TEXT PRIMARY KEY,
                    period_code TEXT NOT NULL,
                    restaurant_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    previous_decision TEXT NOT NULL,
                    new_decision TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    comment TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_engine_version TEXT NOT NULL,
                    source_decision_rule TEXT NOT NULL,
                    supersedes_override_id TEXT,
                    FOREIGN KEY(supersedes_override_id)
                        REFERENCES financial_overrides(override_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _model(row: sqlite3.Row) -> FinancialOverride:
        return FinancialOverride(
            override_id=UUID(row["override_id"]),
            period_code=row["period_code"],
            restaurant_id=row["restaurant_id"],
            order_id=row["order_id"],
            previous_decision=FinancialDecision(row["previous_decision"]),
            new_decision=FinancialDecision(row["new_decision"]),
            reason_code=OverrideReasonCode(row["reason_code"]),
            comment=row["comment"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            source_engine_version=row["source_engine_version"],
            source_decision_rule=row["source_decision_rule"],
            supersedes_override_id=(
                UUID(row["supersedes_override_id"])
                if row["supersedes_override_id"]
                else None
            ),
        )


class FinancialOverrideService:
    def __init__(self, repository: FinancialOverrideRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        period_code: str,
        restaurant_id: str,
        order_id: str,
        system_decision: FinancialDecision,
        new_decision: FinancialDecision,
        reason_code: OverrideReasonCode,
        comment: str | None,
        created_by: str,
        source_engine_version: str,
        source_decision_rule: str,
        created_at: datetime | None = None,
    ) -> tuple[FinancialOverride, AuditEvent]:
        history = self.repository.list_for_order(period_code, order_id)
        latest = history[-1] if history else None
        previous = latest.new_decision if latest else system_decision
        override = FinancialOverride(
            override_id=uuid4(),
            period_code=period_code,
            restaurant_id=restaurant_id,
            order_id=order_id,
            previous_decision=previous,
            new_decision=new_decision,
            reason_code=reason_code,
            comment=comment,
            created_by=created_by,
            created_at=created_at or datetime.now(UTC),
            source_engine_version=source_engine_version,
            source_decision_rule=source_decision_rule,
            supersedes_override_id=latest.override_id if latest else None,
        )
        self.repository.append(override)
        audit = AuditEvent(
            event_type="FINANCIAL_OVERRIDE_CREATED",
            level=AuditLevel.WARNING,
            actor_id=created_by,
            period_id=period_code,
            restaurant_id=restaurant_id,
            entity_type="ORDER",
            entity_id=order_id,
            occurred_at=override.created_at,
            details={
                "override_id": str(override.override_id),
                "previous_decision": previous.value,
                "new_decision": new_decision.value,
                "reason_code": reason_code.value,
                "supersedes_override_id": (
                    str(override.supersedes_override_id)
                    if override.supersedes_override_id
                    else None
                ),
            },
        )
        return override, audit


def latest_overrides(
    overrides: tuple[FinancialOverride, ...],
) -> dict[str, FinancialOverride]:
    latest: dict[str, FinancialOverride] = {}
    for override in sorted(
        overrides,
        key=lambda item: (item.created_at, str(item.override_id)),
    ):
        latest[override.order_id] = override
    return latest
