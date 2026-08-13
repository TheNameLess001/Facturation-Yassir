from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from src.documents.registry import DocumentRegistry
from src.models.domain import Document, Restaurant, RestaurantSettlement
from src.models.enums import DocumentStatus, WorkflowState


def financial_hash(settlement: RestaurantSettlement) -> str:
    payload = {
        "restaurant_id": settlement.restaurant_id,
        "period_id": settlement.period_id,
        "gross_sales": str(settlement.gross_sales),
        "commission": str(settlement.commission),
        "adjustments": str(settlement.adjustments),
        "net_payable": str(settlement.net_payable),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class DocumentRenderer:
    def render(
        self,
        document_type: str,
        number: str,
        restaurant: Restaurant,
        settlement: RestaurantSettlement,
    ) -> bytes:
        payload = {
            "document_type": document_type,
            "document_number": number,
            "restaurant_id": restaurant.restaurant_id,
            "restaurant_name": restaurant.restaurant_name,
            "legal_entity": restaurant.legal_entity,
            "ice": restaurant.ice,
            "period_id": settlement.period_id,
            "gross_sales": str(settlement.gross_sales),
            "commission": str(settlement.commission),
            "adjustments": str(settlement.adjustments),
            "net_payable": str(settlement.net_payable),
            "currency": "MAD",
        }
        return json.dumps(
            payload, sort_keys=True, ensure_ascii=False, indent=2
        ).encode()


class DocumentService:
    TYPES = ("INVOICE", "DISBURSEMENT_NOTE", "STATEMENT")

    def __init__(
        self,
        registry: DocumentRegistry,
        renderer: DocumentRenderer | None = None,
        period_locked: Callable[[str], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.renderer = renderer or DocumentRenderer()
        self.period_locked = period_locked or (lambda _period_code: False)

    def generate(
        self, restaurant: Restaurant, settlement: RestaurantSettlement
    ) -> tuple[tuple[Document, bytes], ...]:
        if self.period_locked(settlement.period_id):
            raise PermissionError("PERIOD_LOCKED")
        if settlement.state != WorkflowState.VALIDATED:
            raise PermissionError("Documents require a validated settlement")
        if not restaurant.legal_entity or not restaurant.ice:
            raise ValueError("Legal entity and ICE are required")
        result: list[tuple[Document, bytes]] = []
        finance_hash = financial_hash(settlement)
        previous = self.registry.list_for_settlement(
            settlement.restaurant_id, settlement.period_id
        )
        for document_type in self.TYPES:
            prior = next(
                (
                    item
                    for item in reversed(previous)
                    if item.document_type == document_type
                ),
                None,
            )
            number = self.registry.next_number(document_type, settlement.period_id)
            content = self.renderer.render(
                document_type, number, restaurant, settlement
            )
            document = Document(
                restaurant_id=restaurant.restaurant_id,
                period_id=settlement.period_id,
                document_type=document_type,
                document_number=number,
                status=DocumentStatus.GENERATED,
                generated_at=datetime.now(UTC),
                content_hash=hashlib.sha256(content).hexdigest(),
                financial_hash=finance_hash,
                supersedes_document_id=prior.document_id if prior else None,
            )
            self.registry.save(document)
            result.append((document, content))
        return tuple(result)

    def invalidate_if_changed(self, settlement: RestaurantSettlement) -> bool:
        if self.period_locked(settlement.period_id):
            raise PermissionError("PERIOD_LOCKED")
        documents = self.registry.list_for_settlement(
            settlement.restaurant_id, settlement.period_id
        )
        changed = any(
            item.status == DocumentStatus.GENERATED
            and item.financial_hash != financial_hash(settlement)
            for item in documents
        )
        if changed:
            self.registry.mark_stale(settlement.restaurant_id, settlement.period_id)
        return changed
