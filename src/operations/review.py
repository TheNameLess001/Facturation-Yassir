from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from src.documents.publishing import DocumentPublication, DocumentPublicationStatus
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import Phase5Workspace


class ReviewIssueType(StrEnum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    INVALID_FINANCIAL = "INVALID_FINANCIAL"
    COMMISSION_BLOCKER = "COMMISSION_BLOCKER"
    IDENTITY_BLOCKER = "IDENTITY_BLOCKER"
    LEGAL_MASTER_ISSUE = "LEGAL_MASTER_ISSUE"
    DOCUMENT_GENERATION_FAILURE = "DOCUMENT_GENERATION_FAILURE"
    R2_PUBLICATION_FAILURE = "R2_PUBLICATION_FAILURE"
    DATA_QUALITY_WARNING = "DATA_QUALITY_WARNING"


class ReviewSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"


class ReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_id: UUID
    period: str
    restaurant_id: str | None
    restaurant_name: str | None
    city: str | None = None
    account_manager: str | None = None
    issue_type: ReviewIssueType
    severity: ReviewSeverity
    blocking_dimension: str
    description: str
    source: str
    current_value: str | None = None
    recommended_action: str
    status: ReviewStatus = ReviewStatus.OPEN
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    audit_reference: str | None = None
    retryable: bool = False


class ReviewRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_status_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id TEXT NOT NULL,
                    period_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    def status_for(
        self, issue_id: UUID
    ) -> tuple[ReviewStatus, datetime | None, str | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, occurred_at, actor_id FROM review_status_events "
                "WHERE issue_id=? ORDER BY sequence DESC LIMIT 1",
                (str(issue_id),),
            ).fetchone()
        if not row:
            return ReviewStatus.OPEN, None, None
        status = ReviewStatus(row[0])
        resolved_at = (
            datetime.fromisoformat(row[1])
            if status
            in {
                ReviewStatus.RESOLVED,
                ReviewStatus.WAIVED,
                ReviewStatus.NOT_APPLICABLE,
            }
            else None
        )
        return status, resolved_at, row[2] if resolved_at else None

    def transition(
        self,
        item: ReviewItem,
        status: ReviewStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> ReviewItem:
        if not reason.strip():
            raise ValueError("Review transition reason is required")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO review_status_events(issue_id, period_code, status, actor_id, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(item.issue_id),
                    item.period,
                    status.value,
                    actor_id,
                    reason,
                    datetime.now(UTC).isoformat(),
                ),
            )
        current, resolved_at, resolved_by = self.status_for(item.issue_id)
        return item.model_copy(
            update={
                "status": current,
                "resolved_at": resolved_at,
                "resolved_by": resolved_by,
                "audit_reference": f"REVIEW_STATUS:{item.issue_id}",
            }
        )

    def audit_events(self, period_code: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT issue_id, status, actor_id, reason, occurred_at "
                "FROM review_status_events WHERE period_code=? ORDER BY sequence",
                (period_code,),
            ).fetchall()
        return tuple(
            {
                "event": "REVIEW_STATUS_CHANGED",
                "issue_id": row[0],
                "status": row[1],
                "actor_id": row[2],
                "reason": row[3],
                "occurred_at": row[4],
            }
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class ReviewCenterBuilder:
    def build(
        self,
        workspace: Phase5Workspace,
        publications: tuple[DocumentPublication, ...],
        repository: ReviewRepository,
    ) -> tuple[ReviewItem, ...]:
        period = workspace.summary.period.period_code
        registry = {
            item.restaurant_id: item
            for item in workspace.registry.restaurants
            if item.restaurant_id
        }
        items: list[ReviewItem] = []
        for settlement in workspace.summary.restaurants:
            restaurant = registry.get(settlement.restaurant_id)
            common = {
                "period": period,
                "restaurant_id": settlement.restaurant_id,
                "restaurant_name": settlement.restaurant_name,
                "city": restaurant.city if restaurant else None,
                "account_manager": restaurant.account_manager if restaurant else None,
            }
            if settlement.manual_review_orders:
                items.append(
                    self._item(
                        **common,
                        issue_type=ReviewIssueType.MANUAL_REVIEW,
                        severity=ReviewSeverity.WARNING,
                        blocking_dimension="FINANCIAL",
                        description=f"{settlement.manual_review_orders} order(s) require a financial decision.",
                        source="Admin Earnings settlement",
                        current_value=str(settlement.manual_review_orders),
                        recommended_action="Review orders and create an append-only override where justified.",
                    )
                )
            if (
                settlement.settlement_status
                == RestaurantSettlementStatus.BLOCKED_COMMISSION
            ):
                resolution = settlement.commission_resolution
                items.append(
                    self._item(
                        **common,
                        issue_type=ReviewIssueType.COMMISSION_BLOCKER,
                        severity=ReviewSeverity.CRITICAL,
                        blocking_dimension="COMMISSION",
                        description="Invoice Scope and RST commission require review.",
                        source="Invoice Scope / RST",
                        current_value=(
                            f"Scope={resolution.scope_commission}; RST={resolution.rst_commission}; "
                            f"effective={resolution.effective_commission}"
                        ),
                        recommended_action="Correct the authoritative Invoice Scope source; never silently override.",
                    )
                )
            if settlement.settlement_status == RestaurantSettlementStatus.BLOCKED_DATA:
                items.append(
                    self._item(
                        **common,
                        issue_type=ReviewIssueType.INVALID_FINANCIAL,
                        severity=ReviewSeverity.CRITICAL,
                        blocking_dimension="FINANCIAL_DATA",
                        description="Invalid financial input prevents certified settlement.",
                        source="Admin Earnings",
                        current_value=" · ".join(settlement.issue_codes[:5]),
                        recommended_action="Correct the source financial fields and recalculate readiness.",
                    )
                )
        for restaurant in workspace.registry.restaurants:
            if restaurant.restaurant_id and not restaurant.readiness.identity_ready:
                items.append(
                    self._item(
                        period=period,
                        restaurant_id=restaurant.restaurant_id,
                        restaurant_name=restaurant.restaurant_name,
                        city=restaurant.city,
                        account_manager=restaurant.account_manager,
                        issue_type=ReviewIssueType.IDENTITY_BLOCKER,
                        severity=ReviewSeverity.CRITICAL,
                        blocking_dimension="IDENTITY",
                        description=f"Identity mapping is {restaurant.mapping_status.value}.",
                        source="Invoice Scope / RST",
                        current_value=restaurant.mapping_status.value,
                        recommended_action="Resolve the identity in Invoice Scope and refresh sources.",
                    )
                )
        legal = workspace.registry.partner_legal_master
        if legal:
            for issue in legal.issues:
                restaurant = registry.get(issue.restaurant_id or "")
                items.append(
                    self._item(
                        period=period,
                        restaurant_id=issue.restaurant_id,
                        restaurant_name=issue.restaurant_name,
                        city=restaurant.city if restaurant else None,
                        account_manager=restaurant.account_manager
                        if restaurant
                        else None,
                        issue_type=ReviewIssueType.LEGAL_MASTER_ISSUE,
                        severity=(
                            ReviewSeverity.CRITICAL
                            if issue.code in {"DUPLICATE_ID", "CONFLICT"}
                            else ReviewSeverity.WARNING
                        ),
                        blocking_dimension="LEGAL",
                        description=f"Partner Legal Master issue: {issue.code}.",
                        source="Partner Legal Master",
                        current_value=" · ".join(issue.fields) or issue.review_status,
                        recommended_action="Review the read-only legal master source record.",
                    )
                )
        for publication in publications:
            if publication.status in {
                DocumentPublicationStatus.FAILED,
                DocumentPublicationStatus.STORAGE_VERIFICATION_FAILED,
            }:
                restaurant = registry.get(publication.restaurant_id)
                retryable = publication.error_code not in {
                    "PDF_VALIDATION_FAILED",
                    "DOCUMENT_HASH_MISMATCH",
                    "ACCESSDENIED",
                }
                items.append(
                    self._item(
                        period=period,
                        restaurant_id=publication.restaurant_id,
                        restaurant_name=(
                            restaurant.restaurant_name
                            if restaurant
                            else publication.restaurant_id
                        ),
                        city=restaurant.city if restaurant else None,
                        account_manager=restaurant.account_manager
                        if restaurant
                        else None,
                        issue_type=ReviewIssueType.R2_PUBLICATION_FAILURE,
                        severity=ReviewSeverity.CRITICAL,
                        blocking_dimension="DOCUMENT_STORAGE",
                        description=f"{publication.document_type} v{publication.document_version} publication failed.",
                        source="Cloudflare R2 publication registry",
                        current_value=publication.error_code,
                        recommended_action="Retry only when the failure is transient."
                        if retryable
                        else "Correct the non-retryable document/storage condition.",
                        retryable=retryable,
                    )
                )
        resolved: list[ReviewItem] = []
        for item in items:
            status, resolved_at, resolved_by = repository.status_for(item.issue_id)
            resolved.append(
                item.model_copy(
                    update={
                        "status": status,
                        "resolved_at": resolved_at,
                        "resolved_by": resolved_by,
                    }
                )
            )
        return tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.status.value,
                    item.severity.value,
                    str(item.issue_id),
                ),
            )
        )

    @staticmethod
    def _item(**values: object) -> ReviewItem:
        identity = "|".join(
            str(values.get(key) or "")
            for key in ("period", "restaurant_id", "issue_type", "description")
        )
        return ReviewItem(
            issue_id=uuid5(NAMESPACE_URL, identity),
            created_at=datetime.now(UTC),
            **values,
        )
