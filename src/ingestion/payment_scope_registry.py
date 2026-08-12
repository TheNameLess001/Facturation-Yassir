from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from src.ingestion.payment_scope_models import PaymentScopeSnapshot


class PaymentScopeSnapshotRegistry:
    """Append-only local snapshots of the eligibility source version and ID population."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, snapshot: PaymentScopeSnapshot) -> PaymentScopeSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO payment_scope_snapshots (
                    snapshot_id, period_id, drive_file_id, filename, drive_modified_at,
                    drive_checksum, content_hash, snapshot_at, restaurant_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.period_id,
                    snapshot.drive_file_id,
                    snapshot.filename,
                    snapshot.drive_modified_at.isoformat(),
                    snapshot.drive_checksum,
                    snapshot.content_hash,
                    snapshot.snapshot_at.isoformat(),
                    snapshot.restaurant_count,
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO payment_scope_restaurants (snapshot_id, restaurant_id)
                VALUES (?, ?)
                """,
                ((snapshot.snapshot_id, item) for item in snapshot.restaurant_ids),
            )
        return self.get(snapshot.snapshot_id) or snapshot

    def get(self, snapshot_id: str) -> PaymentScopeSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_scope_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if not row:
                return None
            ids = connection.execute(
                """
                SELECT restaurant_id FROM payment_scope_restaurants
                WHERE snapshot_id=? ORDER BY restaurant_id
                """,
                (snapshot_id,),
            ).fetchall()
        return self._to_model(row, tuple(item["restaurant_id"] for item in ids))

    def list_for_period(self, period_id: str) -> tuple[PaymentScopeSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_id FROM payment_scope_snapshots
                WHERE period_id=? ORDER BY snapshot_at, snapshot_id
                """,
                (period_id,),
            ).fetchall()
        return tuple(
            snapshot
            for row in rows
            if (snapshot := self.get(row["snapshot_id"])) is not None
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_scope_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    period_id TEXT NOT NULL,
                    drive_file_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    drive_modified_at TEXT NOT NULL,
                    drive_checksum TEXT,
                    content_hash TEXT NOT NULL,
                    snapshot_at TEXT NOT NULL,
                    restaurant_count INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_scope_restaurants (
                    snapshot_id TEXT NOT NULL,
                    restaurant_id TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, restaurant_id),
                    FOREIGN KEY (snapshot_id) REFERENCES payment_scope_snapshots(snapshot_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scope_period
                ON payment_scope_snapshots(period_id)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _to_model(
        row: sqlite3.Row, restaurant_ids: tuple[str, ...]
    ) -> PaymentScopeSnapshot:
        return PaymentScopeSnapshot(
            snapshot_id=row["snapshot_id"],
            period_id=row["period_id"],
            drive_file_id=row["drive_file_id"],
            filename=row["filename"],
            drive_modified_at=datetime.fromisoformat(row["drive_modified_at"]),
            drive_checksum=row["drive_checksum"],
            content_hash=row["content_hash"],
            snapshot_at=datetime.fromisoformat(row["snapshot_at"]),
            restaurant_ids=restaurant_ids,
        )
