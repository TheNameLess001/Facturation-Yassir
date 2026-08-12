from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from src.models.domain import Payment


class PaymentRegistry:
    def __init__(self, database_path: Path | str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL,
                    period_id TEXT NOT NULL, amount TEXT NOT NULL, status TEXT NOT NULL,
                    payment_date TEXT, reference TEXT,
                    UNIQUE(restaurant_id, period_id, reference)
                )
                """
            )

    def save(self, payment: Payment) -> Payment:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO payments(payment_id, restaurant_id, period_id, amount, status, payment_date, reference)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payment.payment_id),
                    payment.restaurant_id,
                    payment.period_id,
                    str(payment.amount),
                    payment.status,
                    payment.payment_date.isoformat() if payment.payment_date else None,
                    payment.reference,
                ),
            )
        return payment

    def list_for_period(self, period_id: str) -> tuple[Payment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM payments WHERE period_id=? ORDER BY restaurant_id",
                (period_id,),
            ).fetchall()
        return tuple(
            Payment(
                payment_id=UUID(row["payment_id"]),
                restaurant_id=row["restaurant_id"],
                period_id=row["period_id"],
                amount=Decimal(row["amount"]),
                status=row["status"],
                payment_date=date.fromisoformat(row["payment_date"])
                if row["payment_date"]
                else None,
                reference=row["reference"],
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
