from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from src.models.enums import EmailStatus


class EmailRegistry:
    def __init__(self, database_path: Path | str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS communications (
                    communication_key TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL,
                    period_id TEXT NOT NULL, status TEXT NOT NULL, provider_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                    sent_at TEXT, resend_reason TEXT
                )
                """
            )

    def status(self, key: str) -> EmailStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM communications WHERE communication_key=?", (key,)
            ).fetchone()
        return EmailStatus(row["status"]) if row else None

    def record(
        self,
        key: str,
        restaurant_id: str,
        period_id: str,
        status: EmailStatus,
        *,
        provider_id: str | None = None,
        error: str | None = None,
        sent_at: datetime | None = None,
        resend_reason: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO communications (
                    communication_key, restaurant_id, period_id, status, provider_id,
                    attempt_count, last_error, sent_at, resend_reason
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(communication_key) DO UPDATE SET
                    status=excluded.status, provider_id=excluded.provider_id,
                    attempt_count=communications.attempt_count+1,
                    last_error=excluded.last_error, sent_at=excluded.sent_at,
                    resend_reason=excluded.resend_reason
                """,
                (
                    key,
                    restaurant_id,
                    period_id,
                    status.value,
                    provider_id,
                    error,
                    sent_at.isoformat() if sent_at else None,
                    resend_reason,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
