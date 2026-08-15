from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from src.emails.phase10_models import (
    AuthorizationStatus,
    EmailAutomationMode,
    EmailWorkflowStatus,
    PartnerEmailPackage,
    PeriodAuthorization,
    SendAttempt,
)
from src.emails.sandbox import SandboxDraftRecord, SandboxDraftStatus
from src.models.domain import AuditEvent


class EmailWorkflowRepository:
    """Local append-oriented operational state; runtime data is ignored by Git."""

    def __init__(self, database_path: Path | str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_package(self, package: PartnerEmailPackage) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO email_packages(package_id, period_code, "
                "restaurant_id, package_hash, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(package.package_id),
                    package.period_code,
                    package.restaurant_id,
                    package.package_hash,
                    package.model_dump_json(),
                    package.created_at.isoformat(),
                ),
            )

    def list_packages(self, period_code: str) -> tuple[PartnerEmailPackage, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM email_packages WHERE period_code=? "
                "ORDER BY restaurant_id, created_at",
                (period_code,),
            ).fetchall()
        return tuple(PartnerEmailPackage.model_validate_json(row["payload"]) for row in rows)

    def save_authorization(self, authorization: PeriodAuthorization) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT authorization_id FROM period_authorizations "
                "WHERE period_code=? AND current_status='ACTIVE'",
                (authorization.period_code,),
            ).fetchall()
            for row in active:
                self._append_authorization_status(
                    connection, row["authorization_id"], AuthorizationStatus.REVOKED
                )
                connection.execute(
                    "UPDATE period_authorizations SET current_status='REVOKED' "
                    "WHERE authorization_id=?",
                    (row["authorization_id"],),
                )
            connection.execute(
                "INSERT INTO period_authorizations(authorization_id, period_code, "
                "payload, current_status, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(authorization.authorization_id),
                    authorization.period_code,
                    authorization.model_dump_json(),
                    authorization.status.value,
                    authorization.authorized_at.isoformat(),
                ),
            )
            self._append_authorization_status(
                connection, str(authorization.authorization_id), authorization.status
            )

    def active_authorization(self, period_code: str) -> PeriodAuthorization | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM period_authorizations WHERE period_code=? "
                "AND current_status='ACTIVE' ORDER BY created_at DESC LIMIT 1",
                (period_code,),
            ).fetchone()
        return PeriodAuthorization.model_validate_json(row["payload"]) if row else None

    def latest_authorization(self, period_code: str) -> PeriodAuthorization | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM period_authorizations WHERE period_code=? "
                "ORDER BY created_at DESC LIMIT 1",
                (period_code,),
            ).fetchone()
        return PeriodAuthorization.model_validate_json(row["payload"]) if row else None

    def mode_for_period(self, period_code: str) -> EmailAutomationMode:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT mode FROM email_period_modes WHERE period_code=?",
                (period_code,),
            ).fetchone()
        return EmailAutomationMode(row["mode"]) if row else EmailAutomationMode.OFF

    def set_period_mode(self, period_code: str, mode: EmailAutomationMode) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO email_period_modes(period_code, mode) VALUES (?, ?) "
                "ON CONFLICT(period_code) DO UPDATE SET mode=excluded.mode",
                (period_code, mode.value),
            )

    def set_authorization_status(
        self, authorization_id: UUID, status: AuthorizationStatus
    ) -> None:
        with self._connect() as connection:
            self._append_authorization_status(connection, str(authorization_id), status)
            connection.execute(
                "UPDATE period_authorizations SET current_status=? WHERE authorization_id=?",
                (status.value, str(authorization_id)),
            )

    def authorization_history(self, period_code: str) -> tuple[tuple[str, str, str], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT authorization_id, current_status, created_at "
                "FROM period_authorizations WHERE period_code=? ORDER BY created_at",
                (period_code,),
            ).fetchall()
        return tuple((row[0], row[1], row[2]) for row in rows)

    def latest_send(self, send_key: str) -> SendAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM send_attempts WHERE send_key=? "
                "ORDER BY attempt_id DESC LIMIT 1",
                (send_key,),
            ).fetchone()
        return SendAttempt.model_validate_json(row["payload"]) if row else None

    def claim_send(self, attempt: SendAttempt, *, retry: bool) -> SendAttempt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM send_attempts WHERE send_key=? "
                "ORDER BY attempt_id DESC LIMIT 1",
                (attempt.send_key,),
            ).fetchone()
            if row:
                current = SendAttempt.model_validate_json(row["payload"])
                if current.status in {
                    EmailWorkflowStatus.SENT,
                    EmailWorkflowStatus.SENDING,
                }:
                    return current
                if current.status == EmailWorkflowStatus.FAILED and not retry:
                    return current
            connection.execute(
                "INSERT INTO send_attempts(send_key, period_code, restaurant_id, "
                "status, payload, attempted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt.send_key,
                    attempt.period_code,
                    attempt.restaurant_id,
                    attempt.status.value,
                    attempt.model_dump_json(),
                    attempt.attempted_at.isoformat(),
                ),
            )
        return attempt

    def record_send(self, attempt: SendAttempt) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO send_attempts(send_key, period_code, restaurant_id, "
                "status, payload, attempted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt.send_key,
                    attempt.period_code,
                    attempt.restaurant_id,
                    attempt.status.value,
                    attempt.model_dump_json(),
                    attempt.attempted_at.isoformat(),
                ),
            )

    def list_latest_sends(self, period_code: str) -> tuple[SendAttempt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM send_attempts WHERE attempt_id IN "
                "(SELECT MAX(attempt_id) FROM send_attempts WHERE period_code=? "
                "GROUP BY send_key) ORDER BY restaurant_id",
                (period_code,),
            ).fetchall()
        return tuple(SendAttempt.model_validate_json(row["payload"]) for row in rows)

    def claim_sandbox_draft(
        self, record: SandboxDraftRecord
    ) -> SandboxDraftRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM sandbox_drafts WHERE draft_key=? "
                "ORDER BY attempt_id DESC LIMIT 1",
                (record.draft_key,),
            ).fetchone()
            if row:
                current = self._sandbox_record(row["payload"])
                if current.status in {
                    SandboxDraftStatus.CREATED,
                    SandboxDraftStatus.PENDING,
                }:
                    return current
            connection.execute(
                "INSERT INTO sandbox_drafts(draft_key, period_code, status, payload, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    record.draft_key,
                    record.period_code,
                    record.status.value,
                    self._sandbox_payload(record),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def record_sandbox_draft(self, record: SandboxDraftRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sandbox_drafts(draft_key, period_code, status, payload, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    record.draft_key,
                    record.period_code,
                    record.status.value,
                    self._sandbox_payload(record),
                    record.created_at.isoformat(),
                ),
            )

    def list_latest_sandbox_drafts(
        self, period_code: str
    ) -> tuple[SandboxDraftRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM sandbox_drafts WHERE attempt_id IN "
                "(SELECT MAX(attempt_id) FROM sandbox_drafts WHERE period_code=? "
                "GROUP BY draft_key) ORDER BY created_at",
                (period_code,),
            ).fetchall()
        return tuple(self._sandbox_record(row["payload"]) for row in rows)

    def append_audit(self, event: AuditEvent) -> None:
        safe_event = event.model_copy(
            update={"details": self._safe_details(event.details)}
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO email_audit_events(event_id, period_code, event_type, "
                "payload, occurred_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(safe_event.event_id),
                    safe_event.period_id,
                    safe_event.event_type,
                    safe_event.model_dump_json(),
                    safe_event.occurred_at.isoformat(),
                ),
            )

    def list_audit(self, period_code: str) -> tuple[AuditEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM email_audit_events WHERE period_code=? "
                "ORDER BY occurred_at",
                (period_code,),
            ).fetchall()
        return tuple(AuditEvent.model_validate_json(row["payload"]) for row in rows)

    def set_period_lock(self, period_code: str, locked: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO email_period_state(period_code, locked) VALUES (?, ?) "
                "ON CONFLICT(period_code) DO UPDATE SET locked=excluded.locked",
                (period_code, int(locked)),
            )

    def period_locked(self, period_code: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT locked FROM email_period_state WHERE period_code=?",
                (period_code,),
            ).fetchone()
        return bool(row["locked"]) if row else False

    @staticmethod
    def _safe_details(details: dict[str, object]) -> dict[str, object]:
        forbidden = {"body", "rib", "token", "credentials", "private_key"}
        return {
            key: value
            for key, value in details.items()
            if key.casefold() not in forbidden
        }

    @staticmethod
    def _append_authorization_status(
        connection: sqlite3.Connection,
        authorization_id: str,
        status: AuthorizationStatus,
    ) -> None:
        connection.execute(
            "INSERT INTO authorization_status_events(authorization_id, status, "
            "changed_at) VALUES (?, ?, ?)",
            (authorization_id, status.value, datetime.now(UTC).isoformat()),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_packages (
                    package_id TEXT PRIMARY KEY, period_code TEXT NOT NULL,
                    restaurant_id TEXT NOT NULL, package_hash TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS period_authorizations (
                    authorization_id TEXT PRIMARY KEY, period_code TEXT NOT NULL,
                    payload TEXT NOT NULL, current_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorization_status_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    authorization_id TEXT NOT NULL, status TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS send_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    send_key TEXT NOT NULL, period_code TEXT NOT NULL,
                    restaurant_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, attempted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_audit_events (
                    event_id TEXT PRIMARY KEY, period_code TEXT,
                    event_type TEXT NOT NULL, payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_drafts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_key TEXT NOT NULL, period_code TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_period_state (
                    period_code TEXT PRIMARY KEY, locked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS email_period_modes (
                    period_code TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'OFF'
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _sandbox_payload(record: SandboxDraftRecord) -> str:
        payload = asdict(record)
        payload["status"] = record.status.value
        payload["created_at"] = record.created_at.isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _sandbox_record(payload: str) -> SandboxDraftRecord:
        value = json.loads(payload)
        value["status"] = SandboxDraftStatus(value["status"])
        value["created_at"] = datetime.fromisoformat(value["created_at"])
        return SandboxDraftRecord(**value)
