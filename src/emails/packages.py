from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.emails.phase10_models import (
    DocumentAttachmentRef,
    EmailWorkflowStatus,
    PartnerEmailPackage,
    RecipientResolution,
    RecipientStatus,
)
from src.restaurants.registry_models import RegisteredRestaurant

EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)
PRODUCTION_DOCUMENT_STATUSES = frozenset({"VALIDATED", "PRODUCTION_READY"})


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def resolve_recipient(restaurant: RegisteredRestaurant) -> RecipientResolution:
    source_field = None
    raw = None
    if restaurant.finance_email and restaurant.finance_email.strip():
        raw = restaurant.finance_email
        source_field = "finance_email"
    elif restaurant.email and restaurant.email.strip():
        raw = restaurant.email
        source_field = "email"
    recipient = normalize_email(raw)
    if recipient is None:
        status = RecipientStatus.EMAIL_MISSING
    elif not EMAIL_PATTERN.fullmatch(recipient):
        status = RecipientStatus.EMAIL_INVALID
    else:
        status = RecipientStatus.EMAIL_VALID
    return RecipientResolution(
        recipient_to=recipient,
        recipient_cc=(),
        status=status,
        source_field=source_field,
    )


class PartnerEmailTemplate:
    @staticmethod
    def subject(period_code: str, restaurant_name: str) -> str:
        return f"Yassir CashCo — Facturation {period_code} — {restaurant_name}"

    @staticmethod
    def body(period_code: str, restaurant_name: str) -> str:
        return (
            f"Bonjour {restaurant_name},\n\n"
            f"Veuillez trouver les documents de facturation CashCo relatifs à la "
            f"période {period_code}.\n\n"
            "Cordialement,\nL’équipe Yassir CashCo"
        )


class PartnerEmailPackageFactory:
    def __init__(self, template: PartnerEmailTemplate | None = None) -> None:
        self.template = template or PartnerEmailTemplate()

    def create(
        self,
        *,
        period_code: str,
        restaurant: RegisteredRestaurant,
        financial_status: str,
        settlement_snapshot: dict[str, Any],
        document_refs: tuple[DocumentAttachmentRef, ...],
        package_version: int = 1,
        now: datetime | None = None,
    ) -> PartnerEmailPackage:
        if not restaurant.restaurant_id or not restaurant.restaurant_name:
            raise ValueError("A mapped restaurant ID and name are required")
        recipient = resolve_recipient(restaurant)
        subject = self.template.subject(period_code, restaurant.restaurant_name)
        body = self.template.body(period_code, restaurant.restaurant_name)
        settlement_hash = stable_hash(settlement_snapshot)
        document_hash = stable_hash(
            [item.model_dump(mode="json") for item in document_refs]
        )
        content_hash = stable_hash(
            {
                "recipient_to": recipient.recipient_to,
                "recipient_cc": recipient.recipient_cc,
                "subject": subject,
                "body": body,
            }
        )
        package_hash = stable_hash(
            {
                "period_code": period_code,
                "restaurant_id": restaurant.restaurant_id,
                "package_version": package_version,
                "settlement_snapshot_hash": settlement_hash,
                "document_snapshot_hash": document_hash,
                "content_hash": content_hash,
            }
        )
        send_key = stable_hash(
            {
                "period_code": period_code,
                "restaurant_id": restaurant.restaurant_id,
                "document_versions": [
                    (item.document_id, item.version) for item in document_refs
                ],
                "package_version": package_version,
            }
        )
        document_ready = bool(document_refs) and all(
            item.status in PRODUCTION_DOCUMENT_STATUSES for item in document_refs
        )
        workflow_status = (
            EmailWorkflowStatus.READY
            if recipient.status == RecipientStatus.EMAIL_VALID and document_ready
            else EmailWorkflowStatus.BLOCKED
        )
        timestamp = now or datetime.now(UTC)
        return PartnerEmailPackage(
            package_id=uuid5(NAMESPACE_URL, package_hash),
            period_code=period_code,
            restaurant_id=restaurant.restaurant_id,
            restaurant_name=restaurant.restaurant_name,
            recipient_to=recipient.recipient_to,
            recipient_cc=(),
            subject=subject,
            body=body,
            document_refs=document_refs,
            financial_status=financial_status,
            document_status="PRODUCTION_READY" if document_ready else "NOT_READY",
            email_status=recipient.status,
            workflow_status=workflow_status,
            package_version=package_version,
            created_at=timestamp,
            updated_at=timestamp,
            settlement_snapshot_hash=settlement_hash,
            document_snapshot_hash=document_hash,
            content_hash=content_hash,
            package_hash=package_hash,
            send_key=send_key,
        )
