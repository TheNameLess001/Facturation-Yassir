from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from src.config import Settings, get_settings
from src.documents.phase8 import (
    CashCoDocumentType,
    DocumentReadinessStatus,
    Phase8DocumentEngine,
)
from src.emails.packages import (
    PartnerEmailPackageFactory,
    resolve_recipient,
    stable_hash,
)
from src.emails.phase10_authorization import PeriodAuthorizationService
from src.emails.phase10_models import (
    DocumentAttachmentRef,
    EmailWorkflowStatus,
    PartnerEmailPackage,
    PeriodWorkflowStatus,
    ProductionReadinessResult,
    ReadinessBlocker,
    ReadinessState,
    RecipientStatus,
)
from src.emails.readiness import ProductionReadinessInput, ProductionReadinessPolicy
from src.emails.workflow_repository import EmailWorkflowRepository
from src.settlement.phase5_models import RestaurantSettlementStatus
from src.settlement.phase5_runtime import Phase5Workspace, load_phase5_workspace


@dataclass(frozen=True)
class EmailCenterRow:
    restaurant: str
    restaurant_id: str
    city: str | None
    account_manager: str | None
    chain: str | None
    recipient: str | None
    financial_status: str
    document_status: str
    email_status: str
    authorization_status: str
    send_status: str
    blockers: tuple[str, ...]
    package: PartnerEmailPackage
    readiness: ProductionReadinessResult

    @property
    def preauthorization_ready(self) -> bool:
        downstream = {
            ReadinessBlocker.ADMIN_NOT_AUTHORIZED,
            ReadinessBlocker.AUTHORIZATION_STALE,
            ReadinessBlocker.PRODUCTION_SEND_DISABLED,
        }
        return not (set(self.readiness.blockers) - downstream)


@dataclass(frozen=True)
class EmailCenterSnapshot:
    period_code: str
    period_status: PeriodWorkflowStatus
    scope_restaurants: int
    identity_ready: int
    settlement_ready: int
    financial_review_pending: int
    document_ready: int
    email_ready: int
    missing_email: int
    invalid_email: int
    formula_blocked: int
    legal_blocked: int
    blocked: int
    authorized: int
    sent: int
    failed: int
    production_send_eligible: int
    rows: tuple[EmailCenterRow, ...]


def build_email_center_snapshot(
    workspace: Phase5Workspace,
    *,
    settings: Settings | None = None,
) -> EmailCenterSnapshot:
    settings = settings or get_settings()
    summary = workspace.summary
    repository = EmailWorkflowRepository(settings.email_workflow_registry_path)
    active_authorization = repository.active_authorization(summary.period.period_code)
    stored_sends = {
        item.send_key: item
        for item in repository.list_latest_sends(summary.period.period_code)
    }
    locked = repository.period_locked(summary.period.period_code)
    registry_by_id = {
        item.restaurant_id: item
        for item in workspace.registry.restaurants
        if item.restaurant_id
    }
    document_engine = Phase8DocumentEngine()
    package_factory = PartnerEmailPackageFactory()
    policy = ProductionReadinessPolicy()
    rows: list[EmailCenterRow] = []
    for settlement in summary.restaurants:
        restaurant = registry_by_id.get(settlement.restaurant_id)
        if restaurant is None:
            continue
        document_readiness = document_engine.readiness(restaurant, settlement)
        document_refs = tuple(
            DocumentAttachmentRef(
                document_type=document_type.value,
                document_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{settlement.period_code}:{settlement.restaurant_id}:"
                        f"{document_type.value}:v1",
                    )
                ),
                version=1,
                content_hash=stable_hash(
                    {
                        "restaurant_id": settlement.restaurant_id,
                        "period": settlement.period_code,
                        "type": document_type.value,
                        "status": "DRAFT_NOT_VALIDATED",
                    }
                ),
                status="DRAFT_NOT_VALIDATED",
            )
            for document_type in CashCoDocumentType
        )
        package = package_factory.create(
            period_code=settlement.period_code,
            restaurant=restaurant,
            financial_status=settlement.settlement_status.value,
            settlement_snapshot=settlement.model_dump(mode="json"),
            document_refs=document_refs,
        )
        recipient = resolve_recipient(restaurant)
        previous_send = stored_sends.get(package.send_key)
        readiness = policy.evaluate(
            ProductionReadinessInput(
                identity_ready=restaurant.readiness.identity_ready,
                settlement_ready=(
                    settlement.settlement_status == RestaurantSettlementStatus.READY
                ),
                manual_review_clear=settlement.manual_review_orders == 0,
                commission_valid=(
                    settlement.commission_resolution.effective_commission is not None
                ),
                financial_data_valid=not any(
                    code.startswith("INVALID_") for code in settlement.issue_codes
                ),
                legacy_formula_validated=(
                    document_readiness.financial_formulas_validated
                ),
                legal_data_ready=document_readiness.legal_ready,
                document_ready=(
                    document_readiness.status == DocumentReadinessStatus.READY
                ),
                email_status=recipient.status,
                admin_authorized=active_authorization is not None,
                authorization_current=active_authorization is not None,
                already_sent=(
                    previous_send is not None
                    and previous_send.status == EmailWorkflowStatus.SENT
                ),
                period_locked=locked,
                production_send_enabled=(
                    settings.email_allow_send
                    and settings.production_email_send_enabled
                ),
            )
        )
        rows.append(
            EmailCenterRow(
                restaurant=restaurant.restaurant_name or "—",
                restaurant_id=settlement.restaurant_id,
                city=restaurant.city,
                account_manager=restaurant.account_manager,
                chain=restaurant.chain,
                recipient=recipient.recipient_to,
                financial_status=settlement.settlement_status.value,
                document_status=document_readiness.status.value,
                email_status=recipient.status.value,
                authorization_status=(
                    active_authorization.status.value
                    if active_authorization
                    else "NOT_AUTHORIZED"
                ),
                send_status=(
                    previous_send.status.value if previous_send else "NOT_ATTEMPTED"
                ),
                blockers=tuple(item.value for item in readiness.blockers),
                package=package,
                readiness=readiness,
            )
        )
    preauthorization_packages = tuple(
        row.package for row in rows if row.preauthorization_ready
    )
    authorization_current = bool(
        active_authorization
        and PeriodAuthorizationService(repository).is_current(
            active_authorization, preauthorization_packages
        )
    )
    if active_authorization and not authorization_current:
        stale_rows: list[EmailCenterRow] = []
        for row in rows:
            blockers = tuple(
                dict.fromkeys(
                    (*row.readiness.blockers, ReadinessBlocker.AUTHORIZATION_STALE)
                )
            )
            stale_rows.append(
                replace(
                    row,
                    authorization_status="STALE",
                    readiness=row.readiness.model_copy(
                        update={
                            "blockers": blockers,
                            "ready_for_send": False,
                            "state": ReadinessState.READY_FOR_DRAFT,
                        }
                    ),
                )
            )
        rows = stale_rows
    formula_blocked = sum(
        ReadinessBlocker.LEGACY_FORMULA_NOT_VALIDATED.value in item.blockers
        for item in rows
    )
    legal_blocked = sum(
        ReadinessBlocker.MISSING_LEGAL_DATA.value in item.blockers for item in rows
    )
    document_ready = sum(item.document_status == "READY" for item in rows)
    email_ready = sum(item.preauthorization_ready for item in rows)
    sent = sum(item.send_status == EmailWorkflowStatus.SENT.value for item in rows)
    failed = sum(item.send_status == EmailWorkflowStatus.FAILED.value for item in rows)
    sending = any(
        item.send_status == EmailWorkflowStatus.SENDING.value for item in rows
    )
    if locked:
        period_status = PeriodWorkflowStatus.LOCKED
    elif sending:
        period_status = PeriodWorkflowStatus.SENDING
    elif sent:
        period_status = PeriodWorkflowStatus.SENT
    elif active_authorization and authorization_current:
        period_status = PeriodWorkflowStatus.AUTHORIZED
    elif email_ready:
        period_status = PeriodWorkflowStatus.EMAIL_READY
    elif document_ready:
        period_status = PeriodWorkflowStatus.DOCUMENTS_READY
    elif summary.manual_review_orders or summary.invalid_financial_rows:
        period_status = PeriodWorkflowStatus.REVIEW
    elif summary.restaurant_status_count(RestaurantSettlementStatus.READY):
        period_status = PeriodWorkflowStatus.FINANCIALLY_READY
    else:
        period_status = PeriodWorkflowStatus.OPEN
    return EmailCenterSnapshot(
        period_code=summary.period.period_code,
        period_status=period_status,
        scope_restaurants=(
            summary.identity_ready_restaurants + summary.identity_blocked_restaurants
        ),
        identity_ready=summary.identity_ready_restaurants,
        settlement_ready=summary.restaurant_status_count(
            RestaurantSettlementStatus.READY
        ),
        financial_review_pending=sum(
            item.manual_review_orders > 0 for item in summary.restaurants
        ),
        document_ready=document_ready,
        email_ready=email_ready,
        missing_email=sum(item.email_status == RecipientStatus.EMAIL_MISSING.value for item in rows),
        invalid_email=sum(item.email_status == RecipientStatus.EMAIL_INVALID.value for item in rows),
        formula_blocked=formula_blocked,
        legal_blocked=legal_blocked,
        blocked=sum(not item.readiness.ready_for_send for item in rows),
        authorized=(
            len(preauthorization_packages)
            if active_authorization and authorization_current
            else 0
        ),
        sent=sent,
        failed=failed,
        production_send_eligible=sum(item.readiness.ready_for_send for item in rows),
        rows=tuple(rows),
    )


def load_email_center_snapshot(period_code: str) -> EmailCenterSnapshot:
    return build_email_center_snapshot(load_phase5_workspace(period_code))
