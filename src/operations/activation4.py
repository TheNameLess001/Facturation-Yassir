from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.config import Settings, get_settings
from src.documents.legal_readiness import CashCoDocumentType
from src.documents.phase8 import Phase8DocumentEngine, ProductionDocumentStatus
from src.documents.publishing import (
    DocumentPublicationBatchResult,
    DocumentPublishingService,
)
from src.emails.runtime import build_email_center_snapshot
from src.settlement.phase5_runtime import Phase5Workspace


@dataclass(frozen=True)
class DryRunRestaurantResult:
    restaurant_id: str
    restaurant_name: str
    period_code: str
    settlement: str
    financial_policy: str
    invoice: str
    note_de_debours: str
    statement: str
    recipient_resolution: str
    email_package: str
    admin_authorization: str
    production_safety_flag: str
    provider_send: str
    reconciliation_difference: Decimal
    document_hashes: tuple[str, ...]


@dataclass(frozen=True)
class Activation4DryRunResult:
    period_code: str
    fully_document_ready: int
    email_packages_buildable: int
    samples: tuple[DryRunRestaurantResult, ...]
    publications: DocumentPublicationBatchResult | None
    production_send_eligible: int
    gmail_provider_calls: int
    audit_events: tuple[str, ...]


def run_activation4_dry_run(
    workspace: Phase5Workspace,
    *,
    sample_size: int = 1,
    publishing: DocumentPublishingService | None = None,
    settings: Settings | None = None,
) -> Activation4DryRunResult:
    """Run a deterministic rehearsal. This function has no Gmail provider boundary."""
    sample_size = max(1, min(3, sample_size))
    email_snapshot = build_email_center_snapshot(
        workspace, settings=settings or get_settings()
    )
    ready_rows = tuple(
        sorted(
            (row for row in email_snapshot.rows if row.preauthorization_ready),
            key=lambda row: row.restaurant_id,
        )
    )
    registry = {
        item.restaurant_id: item
        for item in workspace.registry.restaurants
        if item.restaurant_id
    }
    settlements = {
        item.restaurant_id: item for item in workspace.summary.restaurants
    }
    engine = Phase8DocumentEngine()
    sample_results: list[DryRunRestaurantResult] = []
    all_candidates = []
    for row in ready_rows[:sample_size]:
        restaurant = registry[row.restaurant_id]
        settlement = settlements[row.restaurant_id]
        candidates = tuple(
            engine.production_candidate(kind, restaurant, settlement)
            for kind in CashCoDocumentType
        )
        all_candidates.extend(candidates)
        invoice = next(
            item for item in candidates if item.document_type == CashCoDocumentType.INVOICE
        )
        note = next(
            item
            for item in candidates
            if item.document_type == CashCoDocumentType.NOTE_DE_DEBOURS
        )
        statement = next(
            item
            for item in candidates
            if item.document_type == CashCoDocumentType.PARTNER_STATEMENT
        )
        difference = Decimal(0)
        if (
            settlement.sales_ttc is not None
            and settlement.net_payable is not None
            and settlement.invoice_ttc is not None
        ):
            difference = (
                settlement.sales_ttc
                - settlement.net_payable
                - settlement.invoice_ttc
            )
        sample_results.append(
            DryRunRestaurantResult(
                restaurant_id=row.restaurant_id,
                restaurant_name=row.restaurant,
                period_code=workspace.summary.period.period_code,
                settlement="READY",
                financial_policy=settlement.financial_policy_version
                or "NOT_VALIDATED",
                invoice=invoice.status.value,
                note_de_debours=note.status.value,
                statement=statement.status.value,
                recipient_resolution=(
                    "VALID" if row.email_status == "EMAIL_VALID" else row.email_status
                ),
                email_package=(
                    "READY" if row.preauthorization_ready else "BLOCKED"
                ),
                admin_authorization="NOT_PRODUCTION_AUTHORIZED",
                production_safety_flag="OFF",
                provider_send="NOT_CALLED",
                reconciliation_difference=difference,
                document_hashes=tuple(item.document_hash for item in candidates),
            )
        )
    publication_result = (
        publishing.publish(tuple(all_candidates)) if publishing is not None else None
    )
    fully_document_ready = sum(
        all(
            engine.production_candidate(kind, registry[row.restaurant_id], settlements[row.restaurant_id]).status
            == ProductionDocumentStatus.PRODUCTION_READY
            for kind in CashCoDocumentType
        )
        for row in email_snapshot.rows
        if row.restaurant_id in registry and row.restaurant_id in settlements
    )
    audit = ["DRY_RUN_STARTED"]
    audit.extend("DOCUMENT_RENDERED" for _ in all_candidates)
    if publication_result:
        audit.extend(publication_result.audit_events)
    audit.extend(("GMAIL_SANDBOX_PACKAGE_BUILT", "DRY_RUN_COMPLETED"))
    return Activation4DryRunResult(
        period_code=workspace.summary.period.period_code,
        fully_document_ready=fully_document_ready,
        email_packages_buildable=len(ready_rows),
        samples=tuple(sample_results),
        publications=publication_result,
        production_send_eligible=0,
        gmail_provider_calls=0,
        audit_events=tuple(audit),
    )
