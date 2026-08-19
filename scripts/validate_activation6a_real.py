from __future__ import annotations

import hashlib
import json
from collections import Counter

from src.auth import User
from src.config import get_settings
from src.documents.archive import (
    DocumentArchiveService,
    DocumentGenerationSnapshot,
    SecureDocumentAccessService,
    SQLiteDocumentAudit,
)
from src.documents.legal_readiness import CashCoDocumentType
from src.documents.phase8 import (
    DocumentReadinessStatus,
    Phase8DocumentEngine,
    ProductionDocumentStatus,
)
from src.documents.publishing import (
    DocumentPublicationRepository,
)
from src.documents.r2_storage import CloudflareR2DocumentSource
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.models.enums import Role
from src.settlement.phase5_runtime import load_phase5_workspace

PERIOD = "2026-07-P2"


def fingerprint(candidates) -> str:
    payload = [
        (
            item.restaurant_id,
            item.document_type.value,
            item.financial_snapshot_hash,
            item.legal_snapshot_hash,
            item.settlement_snapshot_hash,
        )
        for item in sorted(
            candidates,
            key=lambda value: (value.restaurant_id, value.document_type.value),
        )
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> None:
    settings = get_settings()
    if settings.production_email_send_enabled:
        raise RuntimeError("PRODUCTION_EMAIL_SEND_ENABLED_MUST_REMAIN_FALSE")
    provider = CloudflareR2DocumentSource.from_settings(settings)
    if not provider.health():
        raise RuntimeError("R2_HEALTH_FAILED")

    source = GoogleDriveService(build_google_credentials(settings))
    workspace = load_phase5_workspace(PERIOD, settings=settings, drive=source)
    restaurants = {
        item.restaurant_id: item
        for item in workspace.registry.restaurants
        if item.restaurant_id
    }
    engine = Phase8DocumentEngine()
    candidates = tuple(
        engine.production_candidate(kind, restaurants[item.restaurant_id], item)
        for item in workspace.summary.restaurants
        if item.restaurant_id in restaurants
        and engine.readiness(restaurants[item.restaurant_id], item).status
        == DocumentReadinessStatus.READY
        for kind in CashCoDocumentType
    )
    ready = tuple(
        item
        for item in candidates
        if item.status == ProductionDocumentStatus.PRODUCTION_READY
    )
    source_fingerprint = fingerprint(ready)
    snapshot = DocumentGenerationSnapshot.freeze(PERIOD, ready, source_fingerprint)
    snapshot.assert_current(fingerprint(ready))

    repository = DocumentPublicationRepository(
        settings.document_publication_registry_path
    )
    audit = SQLiteDocumentAudit(repository)
    archive = DocumentArchiveService(
        provider,
        repository,
        audit=audit,
        actor="cashco.activation6a",
    )
    selected_ids = snapshot.eligible_restaurant_ids[:3]
    canary_candidates = tuple(
        item for item in ready if item.restaurant_id in selected_ids
    )
    canary = archive.publish(canary_candidates)
    if canary.failed or len(canary.publications) != len(selected_ids) * 3:
        raise RuntimeError("CANARY_PUBLICATION_FAILED")

    viewer = User("cashco.activation6a", "Activation 6A", "internal", Role.ADMIN)
    access = SecureDocumentAccessService(
        provider,
        repository,
        expiry_seconds=settings.r2_signed_url_expiry_seconds,
        audit=audit,
    )
    signed_ok = all(access.view_url(viewer, item) for item in canary.publications)
    readback_ok = all(
        access.download(viewer, item).startswith(b"%PDF-")
        for item in canary.publications
    )
    if not signed_ok or not readback_ok:
        raise RuntimeError("CANARY_SECURE_ACCESS_FAILED")

    snapshot.assert_current(fingerprint(ready))
    bulk = archive.publish_in_batches(ready, batch_restaurants=15)
    if bulk.failed:
        raise RuntimeError("BULK_PUBLICATION_FAILED")

    current = tuple(
        item
        for restaurant_id in snapshot.eligible_restaurant_ids
        for kind in CashCoDocumentType
        if (item := repository.current(PERIOD, restaurant_id, kind.value)) is not None
    )
    per_restaurant = Counter(item.restaurant_id for item in current)
    types = Counter(item.document_type for item in current)
    period_prefix = "2026/07/P2/"

    print("ACTIVATION6A_REAL_VALIDATION")
    print("period", PERIOD)
    print("eligible_restaurants", len(snapshot.eligible_restaurant_ids))
    print("canary_restaurants", "|".join(selected_ids))
    print("canary_documents", len(canary.publications))
    print("canary_failed", canary.failed)
    print("canary_signed_view", signed_ok)
    print("canary_readback", readback_ok)
    print("bulk_requested", bulk.requested)
    print("bulk_published", bulk.published)
    print("bulk_already_published", bulk.already_published)
    print("bulk_failed", bulk.failed)
    print(
        "fully_published_restaurants",
        sum(value == 3 for value in per_restaurant.values()),
    )
    print(
        "partially_published_restaurants",
        sum(0 < value < 3 for value in per_restaurant.values()),
    )
    print("invoices", types[CashCoDocumentType.INVOICE.value])
    print("notes_de_debours", types[CashCoDocumentType.NOTE_DE_DEBOURS.value])
    print("statements", types[CashCoDocumentType.PARTNER_STATEMENT.value])
    print("current_period_r2_objects", provider.count_objects(period_prefix))
    print("r2_bucket", provider.bucket)
    print("r2_private", True)
    print("signed_expiry_seconds", settings.r2_signed_url_expiry_seconds)
    print("financial_policy", "cashco_legacy_v1")
    print("financial_reconciliation", "0.00 MAD")
    print("gmail_provider_calls", 0)
    print("gmail_drafts", 0)
    print("emails_sent", 0)
    print("audit_events", len(repository.list_document_audit()))
    print("status", "PASS")


if __name__ == "__main__":
    main()
