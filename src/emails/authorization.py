from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.auth import Permission, RBACService, User
from src.models.domain import AdminAuthorization, RestaurantSettlement
from src.models.enums import AutomationMode, WorkflowState


class AutomationAuthorizationService:
    def __init__(
        self, database_path: Path | str, rbac: RBACService | None = None
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rbac = rbac or RBACService()
        self._initialize()

    def mode_for_period(self, period_id: str) -> AutomationMode:
        authorization = self.active_for_period(period_id)
        return authorization.automation_mode if authorization else AutomationMode.OFF

    def authorize(
        self,
        user: User,
        period_id: str,
        mode: AutomationMode,
        settlements: tuple[RestaurantSettlement, ...],
        *,
        confirmed: bool,
        typed_confirmation: str | None = None,
    ) -> AdminAuthorization:
        self.rbac.require(user, Permission.AUTHORIZE_AUTOMATION)
        if mode == AutomationMode.OFF:
            raise ValueError("OFF does not create an authorization")
        if not confirmed:
            raise PermissionError("Explicit Admin confirmation is required")
        expected = f"SEND {period_id}"
        if mode == AutomationMode.SEND_EMAILS and typed_confirmation != expected:
            raise PermissionError(f"Typed confirmation must match {expected} exactly")
        if any(item.period_id != period_id for item in settlements):
            raise ValueError("Authorization snapshot must contain one period only")
        eligible = tuple(
            item
            for item in settlements
            if item.state
            in {
                WorkflowState.EMAIL_READY,
                WorkflowState.DOCUMENTS_GENERATED,
                WorkflowState.VALIDATED,
            }
        )
        snapshot = tuple(
            {
                "restaurant_id": item.restaurant_id,
                "period_id": item.period_id,
                "gross_sales": str(item.gross_sales),
                "commission": str(item.commission),
                "adjustments": str(item.adjustments),
                "net_payable": str(item.net_payable),
                "state": item.state.value,
            }
            for item in sorted(eligible, key=lambda value: value.restaurant_id)
        )
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        authorization = AdminAuthorization(
            period_id=period_id,
            automation_mode=mode,
            admin_user=user.name,
            admin_id=user.user_id,
            authorized_at=datetime.now(UTC),
            partners_authorized=len(snapshot),
            settlement_total=sum((item.net_payable for item in eligible), Decimal(0)),
            authorization_snapshot=snapshot,
            authorization_hash=digest,
        )
        with self._connect() as connection:
            connection.execute(
                "UPDATE authorizations SET status='SUPERSEDED' WHERE period_id=? AND status='ACTIVE'",
                (period_id,),
            )
            connection.execute(
                """
                INSERT INTO authorizations (
                    confirmation_id, period_id, automation_mode, admin_user, admin_id,
                    authorized_at, partners_authorized, settlement_total,
                    authorization_snapshot, authorization_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(authorization.confirmation_id),
                    period_id,
                    mode.value,
                    user.name,
                    user.user_id,
                    authorization.authorized_at.isoformat(),
                    authorization.partners_authorized,
                    str(authorization.settlement_total),
                    json.dumps(snapshot, sort_keys=True),
                    digest,
                    authorization.status,
                ),
            )
        return authorization

    def active_for_period(self, period_id: str) -> AdminAuthorization | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM authorizations WHERE period_id=? AND status='ACTIVE' ORDER BY authorized_at DESC LIMIT 1",
                (period_id,),
            ).fetchone()
        return self._model(row) if row else None

    def validate_settlement(self, settlement: RestaurantSettlement) -> bool:
        authorization = self.active_for_period(settlement.period_id)
        if authorization is None:
            return False
        approved = next(
            (
                item
                for item in authorization.authorization_snapshot
                if item["restaurant_id"] == settlement.restaurant_id
            ),
            None,
        )
        if approved is None:
            return False
        current = {
            "restaurant_id": settlement.restaurant_id,
            "period_id": settlement.period_id,
            "gross_sales": str(settlement.gross_sales),
            "commission": str(settlement.commission),
            "adjustments": str(settlement.adjustments),
            "net_payable": str(settlement.net_payable),
            "state": approved["state"],
        }
        return current == approved

    def invalidate_restaurant(self, period_id: str, restaurant_id: str) -> None:
        authorization = self.active_for_period(period_id)
        if authorization is None:
            return
        snapshot = [dict(item) for item in authorization.authorization_snapshot]
        for item in snapshot:
            if item["restaurant_id"] == restaurant_id:
                item["authorization_status"] = "AUTHORIZATION_STALE"
        with self._connect() as connection:
            connection.execute(
                "UPDATE authorizations SET authorization_snapshot=?, status='STALE' WHERE confirmation_id=?",
                (
                    json.dumps(snapshot, sort_keys=True),
                    str(authorization.confirmation_id),
                ),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authorizations (
                    confirmation_id TEXT PRIMARY KEY, period_id TEXT NOT NULL,
                    automation_mode TEXT NOT NULL, admin_user TEXT NOT NULL,
                    admin_id TEXT NOT NULL, authorized_at TEXT NOT NULL,
                    partners_authorized INTEGER NOT NULL, settlement_total TEXT NOT NULL,
                    authorization_snapshot TEXT NOT NULL, authorization_hash TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _model(row: sqlite3.Row) -> AdminAuthorization:
        return AdminAuthorization(
            confirmation_id=row["confirmation_id"],
            period_id=row["period_id"],
            automation_mode=AutomationMode(row["automation_mode"]),
            admin_user=row["admin_user"],
            admin_id=row["admin_id"],
            authorized_at=datetime.fromisoformat(row["authorized_at"]),
            partners_authorized=row["partners_authorized"],
            settlement_total=Decimal(row["settlement_total"]),
            authorization_snapshot=tuple(json.loads(row["authorization_snapshot"])),
            authorization_hash=row["authorization_hash"],
            status=row["status"],
        )
