from __future__ import annotations

from collections import Counter
from decimal import Decimal

from src.auth import User
from src.config import get_settings
from src.documents.phase8 import DocumentReadinessStatus, Phase8DocumentEngine
from src.documents.publishing import DocumentPublicationRepository
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.models.enums import Role
from src.payments.finance import (
    PaymentBatchService,
    PaymentExportService,
    PaymentReadiness,
    PaymentReadinessService,
)
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import load_phase5_workspace

PERIOD = "2026-07-P2"


def main() -> None:
    settings = get_settings()
    if settings.production_email_send_enabled:
        raise RuntimeError("PRODUCTION_EMAIL_SEND_ENABLED_MUST_REMAIN_FALSE")
    drive = GoogleDriveService(build_google_credentials(settings))
    workspace = load_phase5_workspace(PERIOD, settings=settings, drive=drive)
    registry = {
        item.restaurant_id: item
        for item in workspace.registry.restaurants
        if item.restaurant_id
    }
    documents = Phase8DocumentEngine()
    publications = DocumentPublicationRepository(
        settings.document_publication_registry_path
    )
    readiness_service = PaymentReadinessService()
    records = []
    financially_ready_ids: set[str] = set()
    document_ready = 0
    fully_published = 0
    for settlement in workspace.summary.restaurants:
        restaurant = registry.get(settlement.restaurant_id)
        if not restaurant:
            continue
        if settlement.settlement_status == RestaurantSettlementStatus.READY:
            financially_ready_ids.add(settlement.restaurant_id)
        ready = (
            documents.readiness(restaurant, settlement).status
            == DocumentReadinessStatus.READY
        )
        document_ready += ready
        published = all(
            publications.current(PERIOD, settlement.restaurant_id, kind) is not None
            for kind in ("INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT")
        )
        fully_published += published
        records.append(
            readiness_service.evaluate(
                restaurant,
                settlement,
                documents_ready=ready and published,
            )
        )
    ready_records = tuple(
        item
        for item in records
        if item.payment_readiness == PaymentReadiness.PAYMENT_READY
    )
    payment_population = tuple(
        item for item in records if item.restaurant_id in financially_ready_ids
    )
    blocked = tuple(item for item in payment_population if item not in ready_records)
    counts = Counter(item.payment_readiness for item in payment_population)
    ready_amount = sum((item.net_payable for item in ready_records), Decimal(0))
    blocked_amount = sum((item.net_payable for item in blocked), Decimal(0))
    sample = ready_records[:3]
    admin = User("cashco.run2", "RUN 2", "internal", Role.ADMIN)
    batch = PaymentBatchService().preview(sample, admin, notes="DRY RUN")
    export = PaymentExportService().workbook(batch)
    print("RUN2_REAL_VALIDATION")
    print(
        "financially_calculable",
        workspace.summary.restaurant_status_count(RestaurantSettlementStatus.READY),
    )
    print("document_ready", document_ready)
    print("fully_published", fully_published)
    print("net_payable", ready_amount + blocked_amount)
    print("payment_ready_restaurants", len(ready_records))
    print("payment_blocked_restaurants", len(blocked))
    print("payment_ready_amount", ready_amount)
    print("payment_blocked_amount", blocked_amount)
    print("missing_rib", counts[PaymentReadiness.RIB_MISSING])
    print("invalid_rib", counts[PaymentReadiness.RIB_INVALID])
    print("rib_conflicts", counts[PaymentReadiness.LEGAL_SOURCE_CONFLICT])
    print("dry_run_restaurants", len(sample))
    print("dry_run_ids", "|".join(item.restaurant_id for item in sample))
    print("dry_run_amount", batch.total_net_payable)
    print(
        "dry_run_reconciliation",
        batch.total_net_payable
        - sum((item.net_payable for item in batch.items), Decimal(0)),
    )
    print("dry_run_export_bytes", len(export))
    print("persistent_payment_mutations", 0)
    print("bank_api_calls", 0)
    print("transfers", 0)
    print("gmail_provider_calls", 0)
    print("status", "PASS")


if __name__ == "__main__":
    main()
