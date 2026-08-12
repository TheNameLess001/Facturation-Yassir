from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.google.models import DriveFile
from src.models.domain import SourceFileManifest
from src.models.enums import ChangeState, SourceType


class SourceManifestRegistry:
    """Durable metadata-only registry. It never stores file contents or credentials."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def register(
        self,
        source_type: SourceType,
        file: DriveFile,
        *,
        period_id: str | None = None,
        checked_at: datetime | None = None,
    ) -> ChangeState:
        checked_at = checked_at or datetime.now(UTC)
        previous = self.get(file.file_id)
        state = self._detect_change(previous, file)
        first_seen = previous.first_seen_at if previous else checked_at
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_manifests (
                    drive_file_id, source_type, filename, mime_type, modified_at, size,
                    checksum, period_id, first_seen_at, last_seen_at, last_checked_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drive_file_id) DO UPDATE SET
                    source_type=excluded.source_type, filename=excluded.filename,
                    mime_type=excluded.mime_type, modified_at=excluded.modified_at,
                    size=excluded.size, checksum=excluded.checksum,
                    period_id=excluded.period_id, last_seen_at=excluded.last_seen_at,
                    last_checked_at=excluded.last_checked_at, status=excluded.status
                """,
                (
                    file.file_id,
                    source_type.value,
                    file.name,
                    file.mime_type,
                    file.modified_time.isoformat(),
                    file.size,
                    file.md5_checksum,
                    period_id,
                    first_seen.isoformat(),
                    checked_at.isoformat(),
                    checked_at.isoformat(),
                    state.value,
                ),
            )
        return state

    def mark_missing(
        self,
        source_type: SourceType,
        seen_file_ids: set[str],
        *,
        checked_at: datetime | None = None,
    ) -> tuple[SourceFileManifest, ...]:
        checked_at = checked_at or datetime.now(UTC)
        known = self.list_by_source(source_type)
        missing = tuple(
            item for item in known if item.drive_file_id not in seen_file_ids
        )
        with self._connect() as connection:
            for item in missing:
                connection.execute(
                    "UPDATE source_manifests SET status=?, last_checked_at=? WHERE drive_file_id=?",
                    (
                        ChangeState.MISSING.value,
                        checked_at.isoformat(),
                        item.drive_file_id,
                    ),
                )
        return missing

    def mark_inaccessible(
        self, source_type: SourceType, *, checked_at: datetime | None = None
    ) -> tuple[SourceFileManifest, ...]:
        """Preserve known manifests when their source location cannot be checked."""
        checked_at = checked_at or datetime.now(UTC)
        known = self.list_by_source(source_type)
        with self._connect() as connection:
            for item in known:
                connection.execute(
                    "UPDATE source_manifests SET status=?, last_checked_at=? WHERE drive_file_id=?",
                    (ChangeState.INACCESSIBLE.value, checked_at.isoformat(), item.drive_file_id),
                )
        return known

    def get(self, file_id: str) -> SourceFileManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_manifests WHERE drive_file_id=?", (file_id,)
            ).fetchone()
        return self._to_model(row) if row else None

    def record_ingestion(
        self,
        file_id: str,
        *,
        rows: int,
        unique_order_ids: int,
        duplicates: int,
        imported_at: datetime,
        import_result: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE source_manifests
                SET rows=?, unique_order_ids=?, duplicates=?, imported_at=?, import_result=?
                WHERE drive_file_id=?
                """,
                (
                    rows,
                    unique_order_ids,
                    duplicates,
                    imported_at.isoformat(),
                    import_result,
                    file_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Source file is not registered: {file_id}")

    def list_all(self) -> tuple[SourceFileManifest, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_manifests ORDER BY source_type, filename"
            ).fetchall()
        return tuple(self._to_model(row) for row in rows)

    def list_by_source(self, source_type: SourceType) -> tuple[SourceFileManifest, ...]:
        return tuple(
            item for item in self.list_all() if item.source_type == source_type
        )

    @staticmethod
    def _detect_change(
        previous: SourceFileManifest | None, file: DriveFile
    ) -> ChangeState:
        if previous is None:
            return ChangeState.NEW
        changed = (
            previous.modified_at != file.modified_time
            or (
                previous.checksum is not None
                and file.md5_checksum is not None
                and previous.checksum != file.md5_checksum
            )
            or previous.size != file.size
        )
        return ChangeState.MODIFIED if changed else ChangeState.UNCHANGED

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_manifests (
                    drive_file_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    size INTEGER,
                    checksum TEXT,
                    period_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rows INTEGER,
                    unique_order_ids INTEGER,
                    duplicates INTEGER,
                    imported_at TEXT,
                    import_result TEXT
                )
                """
            )
            existing = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(source_manifests)"
                ).fetchall()
            }
            migrations = {
                "rows": "INTEGER",
                "unique_order_ids": "INTEGER",
                "duplicates": "INTEGER",
                "imported_at": "TEXT",
                "import_result": "TEXT",
            }
            for column, data_type in migrations.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE source_manifests ADD COLUMN {column} {data_type}"
                    )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _to_model(row: sqlite3.Row) -> SourceFileManifest:
        return SourceFileManifest(
            source_type=SourceType(row["source_type"]),
            drive_file_id=row["drive_file_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            modified_at=datetime.fromisoformat(row["modified_at"]),
            size=row["size"],
            checksum=row["checksum"],
            period_id=row["period_id"],
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            last_checked_at=datetime.fromisoformat(row["last_checked_at"]),
            status=row["status"],
            rows=row["rows"],
            unique_order_ids=row["unique_order_ids"],
            duplicates=row["duplicates"],
            imported_at=(
                datetime.fromisoformat(row["imported_at"])
                if row["imported_at"]
                else None
            ),
            import_result=row["import_result"],
        )
