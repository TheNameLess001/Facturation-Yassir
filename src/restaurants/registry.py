from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.models.domain import Restaurant


class RestaurantRegistryService:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert(self, restaurants: tuple[Restaurant, ...], source_hash: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            for restaurant in restaurants:
                existing = connection.execute(
                    "SELECT first_seen_at, spreadsheet_id FROM restaurants WHERE restaurant_id=?",
                    (restaurant.restaurant_id,),
                ).fetchone()
                first_seen = existing["first_seen_at"] if existing else now
                spreadsheet_id = existing["spreadsheet_id"] if existing else None
                connection.execute(
                    """
                    INSERT INTO restaurants (
                        restaurant_id, restaurant_name, chain, classification, payload,
                        source_hash, first_seen_at, last_seen_at, spreadsheet_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(restaurant_id) DO UPDATE SET
                        restaurant_name=excluded.restaurant_name, chain=excluded.chain,
                        classification=excluded.classification, payload=excluded.payload,
                        source_hash=excluded.source_hash, last_seen_at=excluded.last_seen_at
                    """,
                    (
                        restaurant.restaurant_id,
                        restaurant.restaurant_name,
                        restaurant.chain,
                        "CHAIN" if restaurant.chain else "STANDALONE",
                        restaurant.model_dump_json(),
                        source_hash,
                        first_seen,
                        now,
                        spreadsheet_id,
                    ),
                )

    def get(self, restaurant_id: str) -> Restaurant | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM restaurants WHERE restaurant_id=?",
                (restaurant_id,),
            ).fetchone()
        return Restaurant.model_validate_json(row["payload"]) if row else None

    def list_all(self) -> tuple[Restaurant, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM restaurants ORDER BY restaurant_id"
            ).fetchall()
        return tuple(Restaurant.model_validate_json(row["payload"]) for row in rows)

    def folder_path(self, restaurant_id: str) -> tuple[str, ...]:
        restaurant = self.get(restaurant_id)
        if restaurant is None:
            raise KeyError(restaurant_id)
        leaf = f"{restaurant.restaurant_id}_{restaurant.restaurant_name}"
        return (
            ("CHAINS", restaurant.chain, leaf)
            if restaurant.chain
            else ("STANDALONE", leaf)
        )

    def set_spreadsheet_id(self, restaurant_id: str, spreadsheet_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE restaurants SET spreadsheet_id=? WHERE restaurant_id=?",
                (spreadsheet_id, restaurant_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(restaurant_id)

    def get_spreadsheet_id(self, restaurant_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spreadsheet_id FROM restaurants WHERE restaurant_id=?",
                (restaurant_id,),
            ).fetchone()
        if row is None:
            raise KeyError(restaurant_id)
        return row["spreadsheet_id"]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS restaurants (
                    restaurant_id TEXT PRIMARY KEY, restaurant_name TEXT NOT NULL,
                    chain TEXT, classification TEXT NOT NULL, payload TEXT NOT NULL,
                    source_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, spreadsheet_id TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
