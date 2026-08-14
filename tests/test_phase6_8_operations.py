from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from src.documents.phase8 import (
    CashCoDocumentType,
    DocumentReadinessStatus,
    Phase8DocumentEngine,
)
from src.models.enums import FinancialDecision
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader
from src.settlement.legacy_validation import (
    FormulaEvidenceConfidence,
    LegacyFormulaEvidence,
    LegacyFormulaRegistry,
    ParityStatus,
)
from src.settlement.overrides import (
    FinancialOverrideRepository,
    FinancialOverrideService,
    OverrideReasonCode,
)
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_models import (
    CommissionResolutionStatus,
    RestaurantSettlementStatus,
)
from src.settlement.phase5_service import Phase5SettlementService, resolve_commission
from src.ui.dashboard_data import (
    PeriodOperationalStatus,
    dashboard_snapshot,
    period_trend,
    settlement_progress,
)


def registry(*, legal: bool = True, commission: str | None = "20"):
    scope = pd.DataFrame(
        [{"Column 1": "Restaurant", "Restaurant ID": "R1", "Commission": commission}]
    )
    rst = pd.DataFrame(
        [
            {
                "Restaurant ID": "R1",
                "Restaurant Name": "Restaurant",
                "Commission %": "25",
                "Legal Entity": "Restaurant SARL" if legal else None,
                "ICE": "001" if legal else None,
                "IF": "002" if legal else None,
                "RC": "003" if legal else None,
                "Address": "1 Main Street" if legal else None,
                "Email": "finance@example.test",
            }
        ]
    )
    return RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
    )


def canonical(status: str = "Cancelled by user") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order_id": "O1",
                "restaurant_id": "R1",
                "restaurant_name": "Restaurant",
                "order_date": "2026-07-20",
                "operational_status": status,
                "cancellation_reason": "Order placed accidentally",
                "item_total": "100.00",
                "promo_amount": "0",
                "delivery_fee": "10",
                "commission_amount": "20",
            }
        ]
    )


def evaluate(*, overrides=(), legal: bool = True, commission: str | None = "20"):
    return Phase5SettlementService().evaluate(
        SettlementPeriodService().get(
            "2026-07-P2",
            as_of=date(2026, 8, 12),
        ),
        canonical(),
        registry(legal=legal, commission=commission),
        overrides=overrides,
        evaluated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def test_override_creation_supersession_and_audit_are_append_only(tmp_path) -> None:
    repository = FinancialOverrideRepository(tmp_path / "overrides.sqlite3")
    service = FinancialOverrideService(repository)
    first, first_audit = service.create(
        period_code="2026-07-P2",
        restaurant_id="R1",
        order_id="O1",
        system_decision=FinancialDecision.MANUAL_REVIEW,
        new_decision=FinancialDecision.PAY_PARTNER,
        reason_code=OverrideReasonCode.RESTAURANT_CONFIRMED,
        comment=None,
        created_by="finance-1",
        source_engine_version="engine-1",
        source_decision_rule="CUSTOMER_CANCELLATION_REQUIRES_REVIEW",
        created_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )
    second, second_audit = service.create(
        period_code="2026-07-P2",
        restaurant_id="R1",
        order_id="O1",
        system_decision=FinancialDecision.MANUAL_REVIEW,
        new_decision=FinancialDecision.EXCLUDE,
        reason_code=OverrideReasonCode.CUSTOMER_CANCELLATION,
        comment="Customer evidence reviewed",
        created_by="finance-2",
        source_engine_version="engine-1",
        source_decision_rule="CUSTOMER_CANCELLATION_REQUIRES_REVIEW",
        created_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
    )
    history = repository.list_for_order("2026-07-P2", "O1")
    assert history == (first, second)
    assert second.previous_decision == FinancialDecision.PAY_PARTNER
    assert second.supersedes_override_id == first.override_id
    assert first_audit.event_type == second_audit.event_type == "FINANCIAL_OVERRIDE_CREATED"
    assert second_audit.details["supersedes_override_id"] == str(first.override_id)


def test_other_override_requires_comment(tmp_path) -> None:
    service = FinancialOverrideService(
        FinancialOverrideRepository(tmp_path / "overrides.sqlite3")
    )
    with pytest.raises(ValueError, match="requires a comment"):
        service.create(
            period_code="2026-07-P2",
            restaurant_id="R1",
            order_id="O1",
            system_decision=FinancialDecision.MANUAL_REVIEW,
            new_decision=FinancialDecision.PAY_PARTNER,
            reason_code=OverrideReasonCode.OTHER,
            comment=None,
            created_by="finance",
            source_engine_version="engine",
            source_decision_rule="RULE",
        )


def test_latest_override_recomputes_final_decision_and_settlement(tmp_path) -> None:
    repository = FinancialOverrideRepository(tmp_path / "overrides.sqlite3")
    override, _ = FinancialOverrideService(repository).create(
        period_code="2026-07-P2",
        restaurant_id="R1",
        order_id="O1",
        system_decision=FinancialDecision.MANUAL_REVIEW,
        new_decision=FinancialDecision.PAY_PARTNER,
        reason_code=OverrideReasonCode.RESTAURANT_CONFIRMED,
        comment=None,
        created_by="finance",
        source_engine_version="engine",
        source_decision_rule="CUSTOMER_CANCELLATION_REQUIRES_REVIEW",
    )
    before = evaluate()
    after = evaluate(overrides=(override,))
    assert before.manual_review_orders == 1
    assert after.manual_review_orders == 0
    assert after.pay_partner_orders == 1
    order = after.restaurants[0].orders[0]
    assert order.system_financial_decision == FinancialDecision.MANUAL_REVIEW
    assert order.final_financial_decision == FinancialDecision.PAY_PARTNER
    assert order.manual_override_applied is True
    assert after.restaurants[0].settlement_status == RestaurantSettlementStatus.READY
    assert after.money_reconciliation[0].difference == 0


def test_commission_resolution_uses_scope_authority() -> None:
    match = resolve_commission(Decimal("0.2"), Decimal("0.2"), Decimal(100))
    scope_only = resolve_commission(Decimal("0.2"), None, Decimal(100))
    rst_only = resolve_commission(None, Decimal("0.25"), Decimal(100))
    mismatch = resolve_commission(Decimal("0.2"), Decimal("0.25"), Decimal(100))
    missing = resolve_commission(None, None, Decimal(100))
    assert match.status == CommissionResolutionStatus.MATCH
    assert scope_only.status == CommissionResolutionStatus.SCOPE_ONLY
    assert rst_only.status == CommissionResolutionStatus.RST_ONLY
    assert rst_only.effective_commission is None
    assert mismatch.status == CommissionResolutionStatus.MISMATCH
    assert mismatch.effective_commission == Decimal("0.2")
    assert mismatch.potential_financial_impact == Decimal("5.00")
    assert missing.status == CommissionResolutionStatus.MISSING


def test_legacy_formula_gate_and_parity_statuses() -> None:
    registry = LegacyFormulaRegistry()
    assert registry.production_ready() is False
    assert all(
        item.confidence != FormulaEvidenceConfidence.AUTHORITATIVE
        for item in registry.discover()
    )
    blocked = registry.compare(
        restaurant_id="R1",
        period_code="2026-07-P2",
        financial_field="invoice_ttc",
        legacy_expected=Decimal(100),
        new_amount=Decimal(100),
        formula_validated=False,
    )
    match = registry.compare(
        restaurant_id="R1",
        period_code="2026-07-P2",
        financial_field="invoice_ttc",
        legacy_expected=Decimal(100),
        new_amount=Decimal(100),
        formula_validated=True,
    )
    mismatch = registry.compare(
        restaurant_id="R1",
        period_code="2026-07-P2",
        financial_field="invoice_ttc",
        legacy_expected=Decimal(100),
        new_amount=Decimal("100.01"),
        formula_validated=True,
    )
    assert blocked.status == ParityStatus.FORMULA_NOT_VALIDATED
    assert match.status == ParityStatus.MATCH
    assert mismatch.status == ParityStatus.MISMATCH
    assert mismatch.difference == Decimal("0.01")


def test_authoritative_evidence_is_required_for_every_formula() -> None:
    formula_registry = LegacyFormulaRegistry()
    partial = tuple(
        LegacyFormulaEvidence(
            financial_field=field,
            formula="authoritative formula",
            evidence_source="approved workbook",
            confidence=FormulaEvidenceConfidence.AUTHORITATIVE,
        )
        for field in formula_registry.REQUIRED_FIELDS[:-1]
    )
    complete = partial + (
        LegacyFormulaEvidence(
            financial_field=formula_registry.REQUIRED_FIELDS[-1],
            formula="authoritative formula",
            evidence_source="approved workbook",
            confidence=FormulaEvidenceConfidence.AUTHORITATIVE,
        ),
    )
    assert formula_registry.production_ready(partial) is False
    assert formula_registry.production_ready(complete) is False


def test_document_readiness_preview_and_versioning_are_formula_gated(tmp_path) -> None:
    repository = FinancialOverrideRepository(tmp_path / "overrides.sqlite3")
    override, _ = FinancialOverrideService(repository).create(
        period_code="2026-07-P2",
        restaurant_id="R1",
        order_id="O1",
        system_decision=FinancialDecision.MANUAL_REVIEW,
        new_decision=FinancialDecision.PAY_PARTNER,
        reason_code=OverrideReasonCode.RESTAURANT_CONFIRMED,
        comment=None,
        created_by="finance",
        source_engine_version="engine",
        source_decision_rule="RULE",
    )
    result = evaluate(overrides=(override,))
    restaurant = registry().restaurants[0]
    engine = Phase8DocumentEngine()
    readiness = engine.readiness(restaurant, result.restaurants[0])
    preview = engine.preview(
        CashCoDocumentType.INVOICE,
        restaurant,
        result.restaurants[0],
        version=2,
        generated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    assert readiness.status == DocumentReadinessStatus.FORMULA_NOT_VALIDATED
    assert readiness.potentially_eligible is True
    assert preview.document_key == "R1:2026-07-P2:INVOICE:v2"
    assert preview.watermark == "DRAFT · NOT VALIDATED"
    assert preview.content["invoice_ttc"] is None
    assert b"NOT VALIDATED" in engine.render_local_preview(preview)


def test_document_readiness_distinguishes_financial_and_legal_blockers() -> None:
    unresolved = evaluate()
    resolved_registry = registry(legal=False)
    missing_legal = Phase8DocumentEngine().readiness(
        resolved_registry.restaurants[0],
        unresolved.restaurants[0].model_copy(
            update={
                "manual_review_orders": 0,
                "settlement_status": RestaurantSettlementStatus.READY,
            }
        ),
    )
    financial = Phase8DocumentEngine().readiness(
        registry().restaurants[0], unresolved.restaurants[0]
    )
    assert financial.status == DocumentReadinessStatus.FINANCIAL_REVIEW
    assert missing_legal.status == DocumentReadinessStatus.MISSING_LEGAL
    assert set(missing_legal.missing_legal_fields) == {
        "Legal Entity",
        "ICE",
        "IF",
        "RC",
        "Address",
    }


def test_dashboard_snapshot_trend_alerts_and_progress() -> None:
    summary = evaluate()
    readiness = (
        Phase8DocumentEngine().readiness(
            registry().restaurants[0], summary.restaurants[0]
        ),
    )
    snapshot = dashboard_snapshot(summary, readiness)
    trend = period_trend((summary,))
    progress = dict(settlement_progress(summary, readiness))
    assert snapshot.identity_ready == 1
    assert snapshot.manual_review == 1
    assert snapshot.commission_mismatches == 1
    assert snapshot.period_status == PeriodOperationalStatus.REVIEW
    assert trend[0].review_rate == 1.0
    assert progress["Scope"] == 1
    assert progress["Documents Ready"] == 0
    assert progress["Email Ready"] == 0
