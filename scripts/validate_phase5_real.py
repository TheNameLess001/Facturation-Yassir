from __future__ import annotations

from datetime import date

from src.config import get_settings
from src.documents.phase8 import Phase8DocumentEngine
from src.emails.runtime import build_email_center_snapshot
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.restaurants.registry_runtime import run_restaurant_registry
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import Phase5Workspace, load_phase5_processed_inputs
from src.settlement.phase5_service import Phase5SettlementService


def main() -> None:
    settings = get_settings()
    drive = GoogleDriveService(build_google_credentials(settings))
    canonical, issues = load_phase5_processed_inputs(drive, settings)
    registry = run_restaurant_registry(
        settings=settings,
        drive=drive,
        canonical_orders_frame=canonical,
    )
    periods = SettlementPeriodService(settings.timezone)
    service = Phase5SettlementService()
    selected = (
        periods.get("2026-07-P2", as_of=date(2026, 8, 12)),
        periods.get("2026-07-P1", as_of=date(2026, 8, 12)),
    )
    print("REAL_PHASE5")
    print("scope_rows", registry.scope_rows)
    print("scope_restaurants", len(registry.restaurants))
    print("scope_rows_with_id", registry.scope_rows_with_restaurant_id)
    print("scope_rows_without_id", registry.scope_rows_without_restaurant_id)
    print("registry_identity_ready", registry.mapped_count)
    print("registry_identity_blocked", registry.blocking_mapping_issues)
    results = []
    for period in selected:
        result = service.evaluate(
            period,
            canonical,
            registry,
            invalid_financial_issues=issues,
        )
        results.append(result)
        print("PERIOD", period.period_code, period.status.value)
        print("identity_ready", result.identity_ready_restaurants)
        print("identity_blocked", result.identity_blocked_restaurants)
        print("restaurants_with_orders", result.restaurants_with_orders)
        print("no_orders", result.no_orders_restaurants)
        print("canonical_orders", result.canonical_orders_in_period)
        print("invoice_scope_orders", result.invoice_scope_orders)
        print("evaluated_orders", result.settlement_evaluated_orders)
        print("identity_blocked_orders", result.identity_blocked_orders)
        print("outside_scope_orders", result.outside_invoice_scope_orders)
        print("pay_partner", result.pay_partner_orders)
        print("exclude", result.excluded_orders)
        print("yassir_compensation", result.yassir_compensation_orders)
        print("manual_review", result.manual_review_orders)
        print("unknown_statuses", result.unknown_statuses)
        print(
            "unknown_responsibilities",
            result.unknown_cancellation_responsibilities,
        )
        print("commission_mismatches", result.commission_mismatches)
        print("invalid_financial_rows", result.invalid_financial_rows)
        for status in RestaurantSettlementStatus:
            print(
                "restaurant_status_" + status.value.casefold(),
                result.restaurant_status_count(status),
            )
        print(
            "review_required_any_manual",
            sum(item.manual_review_orders > 0 for item in result.restaurants),
        )
        print(
            "blocked_restaurants",
            sum(
                item.settlement_status
                in {
                    RestaurantSettlementStatus.BLOCKED_IDENTITY,
                    RestaurantSettlementStatus.BLOCKED_COMMISSION,
                    RestaurantSettlementStatus.BLOCKED_DATA,
                }
                for item in result.restaurants
            )
            + result.identity_blocked_restaurants,
        )
        print("money_difference", result.money_reconciliation[0].difference)
        print("money_blocking_rows", result.money_reconciliation[0].blocking_rows)
        print("blocked_order_gmv", result.identity_blocked.blocked_gmv)
        if period.period_code == "2026-07-P2":
            registry_by_id = {
                item.restaurant_id: item
                for item in registry.restaurants
                if item.restaurant_id is not None
            }
            document_readiness = [
                Phase8DocumentEngine().readiness(
                    registry_by_id[item.restaurant_id], item
                )
                for item in result.restaurants
                if item.restaurant_id in registry_by_id
            ]
            print(
                "documents_potentially_eligible",
                sum(item.potentially_eligible for item in document_readiness),
            )
            print(
                "documents_blocked_manual_review",
                sum(item.manual_review_orders > 0 for item in result.restaurants),
            )
            print(
                "documents_blocked_commission",
                sum(
                    item.commission_resolution.effective_commission is None
                    and item.total_orders > 0
                    for item in result.restaurants
                ),
            )
            print(
                "documents_blocked_invalid_financial",
                sum(
                    any(
                        code.startswith("INVALID_") for code in item.issue_codes
                    )
                    for item in result.restaurants
                ),
            )
            print(
                "documents_blocked_legal",
                sum(not item.legal_ready for item in document_readiness),
            )
            print(
                "documents_blocked_formula",
                sum(
                    "LEGACY_FORMULA_VALIDATION_REQUIRED" in item.issue_codes
                    for item in document_readiness
                ),
            )
            print(
                "financially_ready_with_orders",
                result.restaurant_status_count(RestaurantSettlementStatus.READY),
            )
            email_snapshot = build_email_center_snapshot(
                Phase5Workspace(summary=result, registry=registry),
                settings=settings,
            )
            print("email_ready", email_snapshot.email_ready)
            print("missing_email", email_snapshot.missing_email)
            print("invalid_email", email_snapshot.invalid_email)
            print("formula_blocked_email", email_snapshot.formula_blocked)
            print("legal_blocked_email", email_snapshot.legal_blocked)
            print("admin_authorized", email_snapshot.authorized)
            print(
                "production_send_eligible",
                email_snapshot.production_send_eligible,
            )
            print("production_emails_sent", email_snapshot.sent)
    profile = results[0].status_profile
    print("STATUS_PROFILE")
    for item in profile.operational_statuses:
        print("status", repr(item.value), item.count)
    print("distinct_cancellation_reasons", len(profile.cancellation_reasons))
    print("cancellation_fields", "|".join(profile.cancellation_fields))
    print("legacy_policy_identified", service.legacy_policy.identified)
    certification = LegacyFormulaRegistry().certification()
    print("financial_formula_certification", certification.status.value)
    print("financial_policy_implemented", certification.policy_implemented)
    print("financial_policy_version", certification.policy_version or "NOT_ASSIGNED")
    print("financial_formulas_production_ready", certification.production_ready)


if __name__ == "__main__":
    main()
