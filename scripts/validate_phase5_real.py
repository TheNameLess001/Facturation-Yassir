from __future__ import annotations

from datetime import date

from src.config import get_settings
from src.google.auth import build_google_credentials
from src.google.drive_service import GoogleDriveService
from src.restaurants.registry_runtime import run_restaurant_registry
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import load_phase5_processed_inputs
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
    profile = results[0].status_profile
    print("STATUS_PROFILE")
    for item in profile.operational_statuses:
        print("status", repr(item.value), item.count)
    print("distinct_cancellation_reasons", len(profile.cancellation_reasons))
    print("cancellation_fields", "|".join(profile.cancellation_fields))
    print("legacy_policy_identified", service.legacy_policy.identified)


if __name__ == "__main__":
    main()
