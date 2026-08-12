from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.audit import AuditService
from src.auth import Permission, RBACService, User
from src.ingestion.admin_earnings_models import IngestionIssue
from src.models.domain import AuditEvent
from src.models.enums import AuditLevel


class PeriodLockService:
    def __init__(
        self,
        database_path: Path | str,
        audit: AuditService,
        rbac: RBACService | None = None,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit = audit
        self.rbac = rbac or RBACService()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS period_locks (
                    period_id TEXT PRIMARY KEY, locked INTEGER NOT NULL,
                    changed_by TEXT NOT NULL, changed_at TEXT NOT NULL, reason TEXT NOT NULL
                )
                """
            )

    def is_locked(self, period_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT locked FROM period_locks WHERE period_id=?", (period_id,)
            ).fetchone()
        return bool(row["locked"]) if row else False

    def lock(
        self,
        user: User,
        period_id: str,
        *,
        reason: str,
        blocking_issues: tuple[IngestionIssue, ...] = (),
    ) -> None:
        self.rbac.require(user, Permission.LOCK_PERIOD)
        if not reason.strip():
            raise ValueError("Lock reason is required")
        if any(item.level == AuditLevel.BLOCKING for item in blocking_issues):
            raise ValueError("Period cannot be locked with blocking issues")
        self._set(period_id, True, user.user_id, reason)
        self._audit("PERIOD_LOCKED", user, period_id, reason)

    def unlock(self, user: User, period_id: str, *, reason: str) -> None:
        self.rbac.require(user, Permission.LOCK_PERIOD)
        if not reason.strip():
            raise ValueError("Unlock reason is required")
        self._set(period_id, False, user.user_id, reason)
        self._audit("PERIOD_UNLOCKED", user, period_id, reason)

    def _set(self, period_id: str, locked: bool, actor: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO period_locks(period_id, locked, changed_by, changed_at, reason)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(period_id) DO UPDATE SET
                    locked=excluded.locked, changed_by=excluded.changed_by,
                    changed_at=excluded.changed_at, reason=excluded.reason
                """,
                (period_id, int(locked), actor, datetime.now(UTC).isoformat(), reason),
            )

    def _audit(self, event: str, user: User, period_id: str, reason: str) -> None:
        self.audit.record(
            AuditEvent(
                event_type=event,
                level=AuditLevel.WARNING,
                actor_id=user.user_id,
                period_id=period_id,
                entity_type="SETTLEMENT_PERIOD",
                entity_id=period_id,
                details={"reason": reason},
            )
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
