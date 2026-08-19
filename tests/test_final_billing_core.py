from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.auth import User
from src.documents.publishing import DocumentPublicationRepository
from src.models.enums import Role
from src.operations.billing import (
    BillingImpactPreview,
    BillingOperationsRepository,
    BillingPeriodControlService,
    BillingPeriodStatus,
)
from src.operations.reporting import BillingExportService, BillingReportingService
from src.operations.review import (
    ReviewCenterBuilder,
    ReviewIssueType,
    ReviewRepository,
    ReviewStatus,
)
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import RestaurantSettlementStatus


def admin() -> User:
    return User("admin", "Admin", "admin@example.test", Role.ADMIN)


def settlement(**updates):
    values = {
        "restaurant_id": "R1",
        "restaurant_name": "Restaurant One",
        "financial_policy_version": "cashco_legacy_v1",
        "sales_ttc": Decimal(120),
        "sales_ht": Decimal(100),
        "commission_amount": Decimal(20),
        "invoice_tva": Decimal(4),
        "invoice_ttc": Decimal(24),
        "net_payable": Decimal(96),
        "total_orders": 2,
        "manual_review_orders": 0,
        "settlement_status": RestaurantSettlementStatus.READY,
        "issue_codes": (),
        "commission_resolution": SimpleNamespace(
            scope_commission=Decimal(".2"),
            rst_commission=Decimal(".2"),
            effective_commission=Decimal(".2"),
        ),
    }
    values.update(updates)
    return SimpleNamespace(**values)


def summary(period="2026-07-P2", restaurants=None):
    return SimpleNamespace(
        period=SimpleNamespace(period_code=period),
        restaurants=tuple(restaurants or (settlement(),)),
    )


def impact() -> BillingImpactPreview:
    return BillingImpactPreview.from_summary(summary(), document_count=3)


def test_p1_p2_month_end_and_leap_year_are_actual_date_ranges() -> None:
    periods = SettlementPeriodService()
    p1 = periods.get("2026-07-P1", as_of=date(2026, 8, 1))
    p2 = periods.get("2026-07-P2", as_of=date(2026, 8, 1))
    leap = periods.get("2028-02-P2", as_of=date(2028, 3, 1))
    assert (p1.start_date.day, p1.end_date.day) == (1, 15)
    assert (p2.start_date.day, p2.end_date.day) == (16, 31)
    assert (leap.start_date.day, leap.end_date.day) == (16, 29)


def test_period_validation_lock_reopen_and_source_change(tmp_path) -> None:
    repository = BillingOperationsRepository(tmp_path / "billing.sqlite3")
    service = BillingPeriodControlService(repository)
    validated = service.validate(
        user=admin(),
        impact=impact(),
        source_fingerprint="source-a",
        financial_policy_certified=True,
        source_snapshot_available=True,
        document_readiness_evaluated=True,
        critical_structural_blockers=0,
        review_items_classified=True,
        confirmation_text="VALIDATE 2026-07-P2",
    )
    assert validated.status == BillingPeriodStatus.VALIDATED
    locked = service.lock(
        user=admin(),
        impact=impact(),
        source_fingerprint="source-a",
        publication_state_known=True,
        confirmation_text="LOCK 2026-07-P2",
        reason="Business owner approved",
    )
    assert locked.status == BillingPeriodStatus.LOCKED
    assert service.source_changed_after_lock("2026-07-P2", "source-b")
    reopened = service.reopen(
        user=admin(),
        impact=impact(),
        source_fingerprint="source-b",
        confirmation_text="REOPEN 2026-07-P2",
        reason="Controlled source refresh",
    )
    assert reopened.status == BillingPeriodStatus.DATA_READY


def test_period_validation_rejects_reconciliation_and_lock_without_validation(
    tmp_path,
) -> None:
    repository = BillingOperationsRepository(tmp_path / "billing.sqlite3")
    service = BillingPeriodControlService(repository)
    broken = impact().model_copy(update={"net_payable": Decimal(95)})
    with pytest.raises(PermissionError, match="GATES_FAILED"):
        service.validate(
            user=admin(),
            impact=broken,
            source_fingerprint="a",
            financial_policy_certified=True,
            source_snapshot_available=True,
            document_readiness_evaluated=True,
            critical_structural_blockers=0,
            review_items_classified=True,
            confirmation_text="VALIDATE 2026-07-P2",
        )
    with pytest.raises(PermissionError, match="NOT_VALIDATED"):
        service.lock(
            user=admin(),
            impact=impact(),
            source_fingerprint="a",
            publication_state_known=True,
            confirmation_text="LOCK 2026-07-P2",
            reason="No validation",
        )


def test_unified_review_classification_resolution_and_filters(tmp_path) -> None:
    financial = settlement(
        manual_review_orders=2,
        settlement_status=RestaurantSettlementStatus.REVIEW_REQUIRED,
    )
    commission = settlement(
        restaurant_id="R2",
        restaurant_name="Restaurant Two",
        settlement_status=RestaurantSettlementStatus.BLOCKED_COMMISSION,
    )
    registry_items = (
        SimpleNamespace(
            restaurant_id="R1",
            restaurant_name="Restaurant One",
            city="Casa",
            account_manager="AM1",
            readiness=SimpleNamespace(identity_ready=True),
            mapping_status=SimpleNamespace(value="MATCHED_BY_ID"),
        ),
        SimpleNamespace(
            restaurant_id="R2",
            restaurant_name="Restaurant Two",
            city="Rabat",
            account_manager="AM2",
            readiness=SimpleNamespace(identity_ready=True),
            mapping_status=SimpleNamespace(value="MATCHED_BY_ID"),
        ),
    )
    workspace = SimpleNamespace(
        summary=summary(restaurants=(financial, commission)),
        registry=SimpleNamespace(restaurants=registry_items, partner_legal_master=None),
    )
    repository = ReviewRepository(tmp_path / "review.sqlite3")
    items = ReviewCenterBuilder().build(workspace, (), repository)
    assert {item.issue_type for item in items} == {
        ReviewIssueType.MANUAL_REVIEW,
        ReviewIssueType.COMMISSION_BLOCKER,
    }
    resolved = repository.transition(
        items[0], ReviewStatus.RESOLVED, actor_id="admin", reason="Reviewed"
    )
    assert resolved.status == ReviewStatus.RESOLVED
    assert repository.audit_events("2026-07-P2")[-1]["status"] == "RESOLVED"


def test_reporting_totals_comparison_documents_and_exports(tmp_path) -> None:
    current = BillingReportingService.financial(summary())
    previous = BillingReportingService.financial(
        summary(
            "2026-07-P1", (settlement(sales_ttc=Decimal(100), net_payable=Decimal(76)),)
        )
    )
    comparison = BillingReportingService.compare(current, previous)
    assert current.sales_ttc == Decimal(120)
    assert current.invoice_ttc + current.net_payable == current.sales_ttc
    assert comparison.absolute_delta["sales_ttc"] == Decimal(20)

    publications = DocumentPublicationRepository(
        tmp_path / "documents.sqlite3"
    ).list_for_period("2026-07-P2")
    document_report = BillingReportingService.documents(publications)
    assert document_report.total_pdfs == 0
    events = []
    exporter = BillingExportService(
        lambda event, details: events.append((event, details))
    )
    assert exporter.period_csv(current).startswith(b"\xef\xbb\xbf")
    workbook = exporter.workbook(
        current, ({"Restaurant": "One", "Sales TTC": 120},), (), ()
    )
    assert workbook.startswith(b"PK")
    assert all(event[0] == "REPORT_EXPORTED" for event in events)
    assert "rib" not in workbook.decode("latin-1", errors="ignore").casefold()
