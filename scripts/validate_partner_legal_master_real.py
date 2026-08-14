from __future__ import annotations

from collections import Counter

from src.config import get_settings
from src.documents.legal_readiness import DocumentLegalPolicy, DocumentLegalStatus
from src.documents.phase8 import (
    CashCoDocumentType,
    Phase8DocumentEngine,
    ProductionDocumentStatus,
)
from src.emails.gmail_adapter import inspect_gmail_capability
from src.emails.runtime import build_email_center_snapshot
from src.restaurants.legal_master import SOURCE_NAME
from src.restaurants.registry_runtime import expire_partner_legal_master_cache
from src.settlement.phase5_runtime import load_phase5_workspace


def package_status(restaurant) -> DocumentLegalStatus:
    results = DocumentLegalPolicy().evaluate_package(restaurant)
    if any(item.status == DocumentLegalStatus.BLOCKED for item in results):
        return DocumentLegalStatus.BLOCKED
    if any(item.status == DocumentLegalStatus.READY_WITH_WARNINGS for item in results):
        return DocumentLegalStatus.READY_WITH_WARNINGS
    return DocumentLegalStatus.READY


def main() -> None:
    settings = get_settings()
    expire_partner_legal_master_cache(settings)
    workspace = load_phase5_workspace("2026-07-P2", settings=settings)
    registry = workspace.registry
    snapshot = registry.partner_legal_master
    if not snapshot or not snapshot.profile:
        raise RuntimeError("Partner Legal Master has no successful snapshot")
    profile = snapshot.profile
    restaurants = registry.identity_ready_restaurants
    legal_statuses = tuple(package_status(item) for item in restaurants)
    settlements = {item.restaurant_id: item for item in workspace.summary.restaurants}
    restaurant_by_id = {
        item.restaurant_id: item
        for item in registry.restaurants
        if item.restaurant_id in settlements
    }
    engine = Phase8DocumentEngine()
    candidates = {
        restaurant_id: tuple(
            engine.production_candidate(
                document_type,
                restaurant_by_id[restaurant_id],
                settlement,
            )
            for document_type in CashCoDocumentType
        )
        for restaurant_id, settlement in settlements.items()
        if restaurant_id in restaurant_by_id
    }
    email = build_email_center_snapshot(workspace, settings=settings)
    gmail = inspect_gmail_capability(settings)
    issues = Counter(item.code for item in snapshot.issues)

    def master_source(item, field: str) -> bool:
        lineage = item.field_lineage.get(field)
        return bool(lineage and lineage.source == SOURCE_NAME)

    print("PARTNER_LEGAL_MASTER_REAL_VALIDATION_READ_ONLY")
    print("google_access", "PASS")
    print("file_name", profile.filename)
    print("file_id", profile.file_id)
    print("worksheet_names", "|".join(profile.worksheet_names))
    print("selected_worksheet", profile.selected_worksheet)
    print("modified_time", profile.modified_at.isoformat())
    print("capabilities", "|".join(sorted(k for k, v in profile.capabilities.items() if v)))
    print("rows", profile.row_count)
    print("columns", profile.column_count)
    print("unique_ids", profile.unique_restaurant_ids)
    print("invoice_scope_matches", profile.matched_invoice_scope)
    print("rst_matches", profile.matched_rst)
    print("missing_ids", profile.missing_ids)
    print("duplicate_id_groups", profile.duplicate_id_groups)
    print("name_mismatches", profile.name_mismatches)
    print("legal_conflicts", profile.conflict_groups)
    print("invalid_ice", issues["INVALID_ICE"])
    print("invalid_rib", issues["INVALID_RIB"])
    print("invalid_finance_email", issues["INVALID_FINANCE_EMAIL"])
    print("sync_status", snapshot.status.value)
    print("fingerprint", snapshot.fingerprint)
    print("last_successful_sync", snapshot.last_successful_sync.isoformat())
    print("scope", len(registry.restaurants))
    print("identity_ready", len(restaurants))
    print("raison_sociale", sum(bool(item.legal_entity) for item in restaurants))
    print("restaurant_name_fallback", sum(not item.legal_entity for item in restaurants))
    print("ice", sum(bool(item.ice) for item in restaurants))
    print("if", sum(bool(item.if_number) for item in restaurants))
    print("rc", sum(bool(item.rc) for item in restaurants))
    print("address_from_legal_master", sum(master_source(item, "address") for item in restaurants))
    print("address_from_rst_fallback", sum(bool(item.address) and not master_source(item, "address") for item in restaurants))
    print("finance_email", sum(bool(item.finance_email) for item in restaurants))
    print("email_fallback", sum(not item.finance_email and bool(item.email) for item in restaurants))
    print("rib", sum(bool(item.rib) for item in restaurants))
    print("payment_ready", sum(item.readiness.payment_ready for item in restaurants))
    print("financially_calculable", sum(item.financial_policy_version == "cashco_legacy_v1" for item in workspace.summary.restaurants))
    print("manual_review_blocked", sum(item.manual_review_orders > 0 for item in workspace.summary.restaurants))
    print("commission_blocked", sum(item.settlement_status == "BLOCKED_COMMISSION" for item in workspace.summary.restaurants))
    print("invalid_financial_blocked", sum(item.settlement_status == "BLOCKED_DATA" for item in workspace.summary.restaurants))
    print("legal_ready", sum(item == DocumentLegalStatus.READY for item in legal_statuses))
    print("legal_ready_with_warnings", sum(item == DocumentLegalStatus.READY_WITH_WARNINGS for item in legal_statuses))
    print("legal_blocked", sum(item == DocumentLegalStatus.BLOCKED for item in legal_statuses))
    for document_type in CashCoDocumentType:
        print(
            f"{document_type.value.casefold()}_ready",
            sum(
                any(
                    item.document_type == document_type
                    and item.status == ProductionDocumentStatus.PRODUCTION_READY
                    for item in items
                )
                for items in candidates.values()
            ),
        )
    fully_ready = sum(
        all(item.status == ProductionDocumentStatus.PRODUCTION_READY for item in items)
        for items in candidates.values()
    )
    print("fully_document_ready", fully_ready)
    print("document_readiness_delta", fully_ready - 103)
    print("email_ready", email.email_ready)
    print("email_readiness_delta", email.email_ready - 103)
    print("drive_publishing", engine.DRIVE_PUBLISHING_STATUS)
    print("gmail_authentication", gmail.authentication.value)
    print("production_send_enabled", settings.production_email_send_enabled)
    print("emails_sent", email.sent)
    print("source_writes", 0)


if __name__ == "__main__":
    main()
