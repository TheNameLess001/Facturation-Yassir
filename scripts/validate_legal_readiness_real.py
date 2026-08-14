from __future__ import annotations

from src.config import get_settings
from src.documents.legal_readiness import (
    CashCoDocumentType,
    DocumentLegalPolicy,
    DocumentLegalStatus,
    DocumentPartnerNameSource,
)
from src.documents.phase8 import (
    Phase8DocumentEngine,
    ProductionDocumentStatus,
)
from src.emails.gmail_adapter import inspect_gmail_capability
from src.emails.runtime import build_email_center_snapshot
from src.restaurants.scope_registry import (
    INVOICE_SCOPE_ALIASES,
    RST_ALIASES,
    resolve_columns,
)
from src.settlement.phase5_runtime import load_phase5_workspace

AUDIT_FIELDS = (
    "restaurant_name",
    "legal_entity",
    "ice",
    "if_number",
    "rc",
    "address",
    "city",
    "email",
    "rib",
)


def main() -> None:
    settings = get_settings()
    workspace = load_phase5_workspace("2026-07-P2")
    registry = workspace.registry
    restaurants = registry.identity_ready_restaurants
    rst_columns = resolve_columns(list(registry.rst_profile.columns), RST_ALIASES)
    scope_columns = resolve_columns(
        list(registry.invoice_scope_profile.active.columns),
        INVOICE_SCOPE_ALIASES,
    )
    print("REAL_LEGAL_DATA_AUDIT_READ_ONLY")
    print("period", workspace.summary.period.period_code)
    print("scope_restaurants", len(registry.restaurants))
    print("identity_ready", len(restaurants))
    for field in AUDIT_FIELDS:
        if field == "email":
            populated = sum(bool(item.finance_email or item.email) for item in restaurants)
            source = "|".join(
                item
                for item in (
                    rst_columns.get("finance_email"),
                    rst_columns.get("email"),
                )
                if item
            )
        else:
            populated = sum(bool(getattr(item, field)) for item in restaurants)
            source = rst_columns.get(field, "")
            if field in {"restaurant_name", "city"}:
                fallback = scope_columns.get(field)
                if fallback:
                    source = f"{source}|{fallback}" if source else fallback
        missing = len(restaurants) - populated
        percent = populated / len(restaurants) * 100 if restaurants else 0
        source_system = (
            "RST_LIST + INVOICE_SCOPE fallback"
            if field in {"restaurant_name", "city"}
            else "RST_LIST"
        )
        print(
            "field",
            field,
            "populated",
            populated,
            "missing",
            missing,
            "percent",
            f"{percent:.2f}",
            "source_column",
            source or "NOT_MAPPED",
            "source_system",
            source_system,
        )
    print("current_global_required_fields", "legal_entity|ice|if_number|rc|address")
    print("current_legal_gate_before", 716)
    legal_policy = DocumentLegalPolicy()
    legal_by_restaurant = {
        item.restaurant_id: legal_policy.evaluate_package(item)
        for item in restaurants
    }

    def package_status(results):
        if any(item.status == DocumentLegalStatus.BLOCKED for item in results):
            return DocumentLegalStatus.BLOCKED
        if any(
            item.status == DocumentLegalStatus.READY_WITH_WARNINGS
            for item in results
        ):
            return DocumentLegalStatus.READY_WITH_WARNINGS
        return DocumentLegalStatus.READY

    statuses = tuple(package_status(item) for item in legal_by_restaurant.values())
    print("legal_ready", sum(item == DocumentLegalStatus.READY for item in statuses))
    print(
        "legal_ready_with_warnings",
        sum(item == DocumentLegalStatus.READY_WITH_WARNINGS for item in statuses),
    )
    print("legal_blocked", sum(item == DocumentLegalStatus.BLOCKED for item in statuses))
    print("missing_legal_entity", sum(not item.legal_entity for item in restaurants))
    print(
        "restaurant_name_fallback",
        sum(
            results[0].document_partner_name_source
            == DocumentPartnerNameSource.RESTAURANT_NAME_FALLBACK
            for results in legal_by_restaurant.values()
        ),
    )
    print("missing_address", sum(not item.address for item in restaurants))
    print("missing_ice", sum(not item.ice for item in restaurants))
    print(
        "invalid_ice",
        sum(any("ice" in item.invalid_fields for item in results) for results in legal_by_restaurant.values()),
    )
    print("missing_if", sum(not item.if_number for item in restaurants))
    print("missing_rc", sum(not item.rc for item in restaurants))
    print("missing_rib", sum(not item.rib for item in restaurants))

    settlements = {item.restaurant_id: item for item in workspace.summary.restaurants}
    registry_by_id = {
        item.restaurant_id: item for item in restaurants if item.restaurant_id
    }
    document_engine = Phase8DocumentEngine()
    candidates = {
        restaurant_id: tuple(
            document_engine.production_candidate(
                document_type,
                registry_by_id[restaurant_id],
                settlement,
            )
            for document_type in CashCoDocumentType
        )
        for restaurant_id, settlement in settlements.items()
        if restaurant_id in registry_by_id
    }
    for document_type in CashCoDocumentType:
        print(
            f"{document_type.value.casefold()}_ready",
            sum(
                next(
                    item
                    for item in items
                    if item.document_type == document_type
                ).status
                == ProductionDocumentStatus.PRODUCTION_READY
                for items in candidates.values()
            ),
        )
    print(
        "fully_document_ready",
        sum(
            all(item.status == ProductionDocumentStatus.PRODUCTION_READY for item in items)
            for items in candidates.values()
        ),
    )
    print(
        "financially_calculable",
        sum(
            item.financial_policy_version == "cashco_legacy_v1"
            for item in workspace.summary.restaurants
        ),
    )
    print(
        "manual_review_blocked",
        sum(item.manual_review_orders > 0 for item in workspace.summary.restaurants),
    )
    print(
        "commission_blocked",
        sum(item.settlement_status == "BLOCKED_COMMISSION" for item in workspace.summary.restaurants),
    )
    print(
        "invalid_financial_blocked",
        sum(item.settlement_status == "BLOCKED_DATA" for item in workspace.summary.restaurants),
    )
    email = build_email_center_snapshot(workspace, settings=settings)
    gmail = inspect_gmail_capability(settings)
    print("email_ready", email.email_ready)
    print("gmail_authentication", gmail.authentication.value)
    print("production_send_enabled", settings.production_email_send_enabled)
    print("emails_sent", email.sent)
    print("drive_publishing", document_engine.DRIVE_PUBLISHING_STATUS)
    print("source_writes", 0)


if __name__ == "__main__":
    main()
