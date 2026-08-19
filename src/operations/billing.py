from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.auth import Permission, RBACService, User
from src.settlement.phase5_models import SettlementSummary


class BillingPeriodStatus(StrEnum):
    DRAFT = "DRAFT"
    DATA_READY = "DATA_READY"
    TO_REVIEW = "TO_REVIEW"
    VALIDATED = "VALIDATED"
    DOCUMENTS_GENERATED = "DOCUMENTS_GENERATED"
    DOCUMENTS_PUBLISHED = "DOCUMENTS_PUBLISHED"
    LOCKED = "LOCKED"
    BLOCKED = "BLOCKED"


class BillingImpactPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period_code: str
    restaurant_count: int
    document_count: int
    sales_ttc: Decimal
    sales_ht: Decimal
    commission_ht: Decimal
    tva: Decimal
    invoice_ttc: Decimal
    net_payable: Decimal

    @classmethod
    def from_summary(
        cls, summary: SettlementSummary, *, document_count: int
    ) -> BillingImpactPreview:
        ready = tuple(
            item
            for item in summary.restaurants
            if item.financial_policy_version == "cashco_legacy_v1"
            and all(
                value is not None
                for value in (
                    item.sales_ttc,
                    item.sales_ht,
                    item.commission_amount,
                    item.invoice_tva,
                    item.invoice_ttc,
                    item.net_payable,
                )
            )
        )

        def total(field: str) -> Decimal:
            return sum((getattr(item, field) for item in ready), Decimal(0))

        return cls(
            period_code=summary.period.period_code,
            restaurant_count=len(ready),
            document_count=document_count,
            sales_ttc=total("sales_ttc"),
            sales_ht=total("sales_ht"),
            commission_ht=total("commission_amount"),
            tva=total("invoice_tva"),
            invoice_ttc=total("invoice_ttc"),
            net_payable=total("net_payable"),
        )

    @property
    def reconciliation_difference(self) -> Decimal:
        return self.sales_ttc - (self.net_payable + self.invoice_ttc)


class BillingPeriodRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period_code: str
    status: BillingPeriodStatus
    source_fingerprint: str
    impact: BillingImpactPreview
    actor_id: str
    occurred_at: datetime
    reason: str | None = None
    source_changed_after_lock: bool = False


class BillingOperationsRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS billing_period_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_billing_period_events
                ON billing_period_events(period_code, sequence);
                """
            )

    def append(self, record: BillingPeriodRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO billing_period_events(period_code, status, payload, occurred_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    record.period_code,
                    record.status.value,
                    record.model_dump_json(),
                    record.occurred_at.isoformat(),
                ),
            )

    def latest(self, period_code: str) -> BillingPeriodRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM billing_period_events WHERE period_code=? "
                "ORDER BY sequence DESC LIMIT 1",
                (period_code,),
            ).fetchone()
        return BillingPeriodRecord.model_validate_json(row[0]) if row else None

    def history(self) -> tuple[BillingPeriodRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM billing_period_events ORDER BY sequence"
            ).fetchall()
        latest: dict[str, BillingPeriodRecord] = {}
        for row in rows:
            item = BillingPeriodRecord.model_validate_json(row[0])
            latest[item.period_code] = item
        return tuple(latest[key] for key in sorted(latest, reverse=True))

    def audit_events(self, period_code: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, payload FROM billing_period_events WHERE period_code=? "
                "ORDER BY sequence",
                (period_code,),
            ).fetchall()
        return tuple(
            {
                "event": {
                    BillingPeriodStatus.VALIDATED.value: "PERIOD_VALIDATED",
                    BillingPeriodStatus.LOCKED.value: "PERIOD_LOCKED",
                }.get(
                    row[0],
                    "PERIOD_REOPENED"
                    if index and row[0] != "LOCKED"
                    else "PERIOD_STATUS_CHANGED",
                ),
                **json.loads(row[1]),
            }
            for index, row in enumerate(rows)
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class BillingPeriodControlService:
    def __init__(
        self,
        repository: BillingOperationsRepository,
        rbac: RBACService | None = None,
    ) -> None:
        self.repository = repository
        self.rbac = rbac or RBACService()

    def validate(
        self,
        *,
        user: User,
        impact: BillingImpactPreview,
        source_fingerprint: str,
        financial_policy_certified: bool,
        source_snapshot_available: bool,
        document_readiness_evaluated: bool,
        critical_structural_blockers: int,
        review_items_classified: bool,
        confirmation_text: str,
    ) -> BillingPeriodRecord:
        self.rbac.require(user, Permission.AUTHORIZE_AUTOMATION)
        expected = f"VALIDATE {impact.period_code}"
        if confirmation_text != expected:
            raise PermissionError(f"Type {expected} exactly")
        gates = (
            financial_policy_certified,
            source_snapshot_available,
            document_readiness_evaluated,
            critical_structural_blockers == 0,
            review_items_classified,
            impact.reconciliation_difference == Decimal(0),
        )
        if not all(gates):
            raise PermissionError("PERIOD_VALIDATION_GATES_FAILED")
        return self._append(
            user,
            impact,
            source_fingerprint,
            BillingPeriodStatus.VALIDATED,
        )

    def lock(
        self,
        *,
        user: User,
        impact: BillingImpactPreview,
        source_fingerprint: str,
        publication_state_known: bool,
        confirmation_text: str,
        reason: str,
    ) -> BillingPeriodRecord:
        self.rbac.require(user, Permission.LOCK_PERIOD)
        current = self.repository.latest(impact.period_code)
        if current is None or current.status != BillingPeriodStatus.VALIDATED:
            raise PermissionError("PERIOD_NOT_VALIDATED")
        if current.source_fingerprint != source_fingerprint:
            raise PermissionError("SOURCE_CHANGED_BEFORE_LOCK")
        if not publication_state_known:
            raise PermissionError("PUBLICATION_STATE_UNKNOWN")
        if confirmation_text != f"LOCK {impact.period_code}":
            raise PermissionError(f"Type LOCK {impact.period_code} exactly")
        if not reason.strip():
            raise ValueError("Lock reason is required")
        return self._append(
            user,
            impact,
            source_fingerprint,
            BillingPeriodStatus.LOCKED,
            reason,
        )

    def reopen(
        self,
        *,
        user: User,
        impact: BillingImpactPreview,
        source_fingerprint: str,
        confirmation_text: str,
        reason: str,
    ) -> BillingPeriodRecord:
        self.rbac.require(user, Permission.LOCK_PERIOD)
        current = self.repository.latest(impact.period_code)
        if current is None or current.status != BillingPeriodStatus.LOCKED:
            raise PermissionError("PERIOD_NOT_LOCKED")
        if confirmation_text != f"REOPEN {impact.period_code}":
            raise PermissionError(f"Type REOPEN {impact.period_code} exactly")
        if not reason.strip():
            raise ValueError("Reopen reason is required")
        return self._append(
            user,
            impact,
            source_fingerprint,
            BillingPeriodStatus.DATA_READY,
            reason,
        )

    def source_changed_after_lock(
        self, period_code: str, source_fingerprint: str
    ) -> bool:
        current = self.repository.latest(period_code)
        return bool(
            current
            and current.status == BillingPeriodStatus.LOCKED
            and current.source_fingerprint != source_fingerprint
        )

    def _append(
        self,
        user: User,
        impact: BillingImpactPreview,
        source_fingerprint: str,
        status: BillingPeriodStatus,
        reason: str | None = None,
    ) -> BillingPeriodRecord:
        record = BillingPeriodRecord(
            period_code=impact.period_code,
            status=status,
            source_fingerprint=source_fingerprint,
            impact=impact,
            actor_id=user.user_id,
            occurred_at=datetime.now(UTC),
            reason=reason,
        )
        self.repository.append(record)
        return record
