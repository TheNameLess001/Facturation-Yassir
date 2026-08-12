from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from src.models.domain import Document
from src.models.enums import DocumentStatus


class DocumentRegistry:
    def __init__(self, database_path: Path | str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def next_number(self, document_type: str, period_id: str) -> str:
        prefix = {"INVOICE": "INV", "DISBURSEMENT_NOTE": "DN", "STATEMENT": "STMT"}[
            document_type
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT next_value FROM document_sequences WHERE document_type=? AND period_id=?",
                (document_type, period_id),
            ).fetchone()
            value = row["next_value"] if row else 1
            connection.execute(
                """
                INSERT INTO document_sequences(document_type, period_id, next_value)
                VALUES (?, ?, ?) ON CONFLICT(document_type, period_id)
                DO UPDATE SET next_value=excluded.next_value
                """,
                (document_type, period_id, value + 1),
            )
        return f"{prefix}-{period_id}-{value:06d}"

    def save(self, document: Document) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, restaurant_id, period_id, document_type,
                    document_number, status, drive_file_id, generated_at,
                    content_hash, financial_hash, supersedes_document_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(document.document_id),
                    document.restaurant_id,
                    document.period_id,
                    document.document_type,
                    document.document_number,
                    document.status.value,
                    document.drive_file_id,
                    document.generated_at.isoformat()
                    if document.generated_at
                    else None,
                    document.content_hash,
                    document.financial_hash,
                    str(document.supersedes_document_id)
                    if document.supersedes_document_id
                    else None,
                ),
            )

    def list_for_settlement(
        self, restaurant_id: str, period_id: str
    ) -> tuple[Document, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE restaurant_id=? AND period_id=? ORDER BY generated_at",
                (restaurant_id, period_id),
            ).fetchall()
        return tuple(self._model(row) for row in rows)

    def mark_stale(self, restaurant_id: str, period_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE documents SET status=? WHERE restaurant_id=? AND period_id=? AND status=?
                """,
                (
                    DocumentStatus.STALE.value,
                    restaurant_id,
                    period_id,
                    DocumentStatus.GENERATED.value,
                ),
            )
        return cursor.rowcount

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_sequences (
                    document_type TEXT NOT NULL, period_id TEXT NOT NULL,
                    next_value INTEGER NOT NULL, PRIMARY KEY(document_type, period_id)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL,
                    period_id TEXT NOT NULL, document_type TEXT NOT NULL,
                    document_number TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    drive_file_id TEXT, generated_at TEXT, content_hash TEXT,
                    financial_hash TEXT, supersedes_document_id TEXT
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _model(row: sqlite3.Row) -> Document:
        return Document(
            document_id=UUID(row["document_id"]),
            restaurant_id=row["restaurant_id"],
            period_id=row["period_id"],
            document_type=row["document_type"],
            document_number=row["document_number"],
            status=DocumentStatus(row["status"]),
            drive_file_id=row["drive_file_id"],
            generated_at=datetime.fromisoformat(row["generated_at"])
            if row["generated_at"]
            else None,
            content_hash=row["content_hash"],
            financial_hash=row["financial_hash"],
            supersedes_document_id=(
                UUID(row["supersedes_document_id"])
                if row["supersedes_document_id"]
                else None
            ),
        )
