from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.auth import User
from src.config import Settings
from src.documents import DocumentRegistry, DocumentService
from src.emails.gmail_adapter import FakeGmailAdapter, inspect_gmail_capability
from src.emails.packages import (
    PartnerEmailPackageFactory,
    PartnerEmailTemplate,
    resolve_recipient,
)
from src.emails.period_locking import Phase10PeriodLockService
from src.emails.phase10_authorization import PeriodAuthorizationService
from src.emails.phase10_models import (
    AuthorizationStatus,
    DocumentAttachmentRef,
    EmailAutomationMode,
    EmailWorkflowStatus,
    ReadinessBlocker,
    RecipientStatus,
)
from src.emails.readiness import ProductionReadinessInput, ProductionReadinessPolicy
from src.emails.workflow_repository import EmailWorkflowRepository
from src.emails.workflow_service import (
    Phase10EmailWorkflowService,
    ProductionSendDisabledError,
)
from src.models.enums import FinancialDecision, Role
from src.restaurants.registry_models import (
    DataQualityStatus,
    MappingStatus,
    RegisteredRestaurant,
    RestaurantReadiness,
)
from src.settlement.overrides import (
    FinancialOverrideRepository,
    FinancialOverrideService,
    OverrideReasonCode,
)

PERIOD = "2026-07-P2"


def user(role: Role = Role.ADMIN) -> User:
    return User(f"{role.value.lower()}-1", role.value.title(), "u@example.com", role)


def restaurant(
    restaurant_id: str = "R-1",
    *,
    email: str | None = "Partner@Example.COM ",
    finance_email: str | None = " Finance@Example.COM ",
) -> RegisteredRestaurant:
    return RegisteredRestaurant(
        restaurant_id=restaurant_id,
        restaurant_name=f"Restaurant {restaurant_id}",
        legal_entity="Example SARL",
        ice="ICE",
        if_number="IF",
        rc="RC",
        address="Address",
        email=email,
        finance_email=finance_email,
        scope_source_row=2,
        mapping_method="EXACT_ID",
        mapping_status=MappingStatus.MATCHED_BY_ID,
        data_quality_status=DataQualityStatus.HEALTHY,
        invoice_scope_commission_rate=Decimal("0.2"),
        readiness=RestaurantReadiness(
            identity_ready=True,
            orders_available=True,
            settlement_ready=True,
            document_ready=True,
            email_ready=True,
            payment_ready=True,
        ),
    )


def documents(*, version: int = 1, suffix: str = "") -> tuple[DocumentAttachmentRef, ...]:
    return tuple(
        DocumentAttachmentRef(
            document_type=kind,
            document_id=f"{kind}-{suffix or version}",
            version=version,
            content_hash=f"hash-{kind}-{suffix or version}",
            status="PRODUCTION_READY",
        )
        for kind in ("INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT")
    )


def package(
    restaurant_id: str = "R-1",
    *,
    settlement_value: str = "100",
    document_refs: tuple[DocumentAttachmentRef, ...] | None = None,
    target: RegisteredRestaurant | None = None,
    factory: PartnerEmailPackageFactory | None = None,
):
    return (factory or PartnerEmailPackageFactory()).create(
        period_code=PERIOD,
        restaurant=target or restaurant(restaurant_id),
        financial_status="READY",
        settlement_snapshot={"amount": settlement_value, "commission": "0.2"},
        document_refs=document_refs or documents(),
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )


def ready(*, production_send_enabled: bool = True):
    return ProductionReadinessPolicy().evaluate(
        ProductionReadinessInput(
            identity_ready=True,
            settlement_ready=True,
            manual_review_clear=True,
            commission_valid=True,
            financial_data_valid=True,
            legacy_formula_validated=True,
            legal_data_ready=True,
            document_ready=True,
            email_status=RecipientStatus.EMAIL_VALID,
            admin_authorized=True,
            authorization_current=True,
            production_send_enabled=production_send_enabled,
        )
    )


def enabled_settings(tmp_path, **changes) -> Settings:
    values = {
        "email_allow_drafts": True,
        "email_allow_send": True,
        "production_email_send_enabled": True,
        "gmail_execution_mode": "PRODUCTION",
        "email_workflow_registry_path": tmp_path / "email.sqlite3",
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


def authorize(repo: EmailWorkflowRepository, packages):
    return PeriodAuthorizationService(repo).authorize(
        user=user(),
        period_code=PERIOD,
        mode=EmailAutomationMode.SEND,
        packages=tuple(packages),
        eligible_restaurant_count=len(packages),
        blocked_restaurant_count=0,
        confirmation_text=f"SEND CASHCO {PERIOD}",
    )


def test_recipient_finance_email_precedence_and_normalization() -> None:
    result = resolve_recipient(restaurant())
    assert result.recipient_to == "finance@example.com"
    assert result.source_field == "finance_email"
    assert result.recipient_cc == ()
    assert result.status == RecipientStatus.EMAIL_VALID


def test_recipient_fallback_missing_and_invalid() -> None:
    fallback = resolve_recipient(restaurant(finance_email=None))
    missing = resolve_recipient(restaurant(finance_email=None, email=None))
    invalid = resolve_recipient(restaurant(finance_email="not-an-email"))
    assert fallback.recipient_to == "partner@example.com"
    assert missing.status == RecipientStatus.EMAIL_MISSING
    assert invalid.status == RecipientStatus.EMAIL_INVALID


def test_package_generation_and_hashing_include_attachment_versions() -> None:
    first = package(document_refs=documents(version=1))
    same = package(document_refs=documents(version=1))
    changed = package(document_refs=documents(version=2))
    assert first.package_hash == same.package_hash
    assert first.send_key == same.send_key
    assert changed.document_snapshot_hash != first.document_snapshot_hash
    assert changed.send_key != first.send_key
    assert "RIB" not in first.body.upper()


def test_readiness_policy_returns_multiple_blockers() -> None:
    result = ProductionReadinessPolicy().evaluate(
        ProductionReadinessInput(
            identity_ready=True,
            settlement_ready=False,
            manual_review_clear=False,
            commission_valid=False,
            financial_data_valid=False,
            legacy_formula_validated=False,
            legal_data_ready=False,
            document_ready=False,
            email_status=RecipientStatus.EMAIL_INVALID,
        )
    )
    assert ReadinessBlocker.MANUAL_REVIEW_PENDING in result.blockers
    assert ReadinessBlocker.LEGACY_FORMULA_NOT_VALIDATED in result.blockers
    assert ReadinessBlocker.EMAIL_INVALID in result.blockers
    assert ReadinessBlocker.PRODUCTION_SEND_DISABLED in result.blockers
    assert not result.ready_for_send


@pytest.mark.parametrize(
    ("field", "blocker"),
    [
        ("legacy_formula_validated", ReadinessBlocker.LEGACY_FORMULA_NOT_VALIDATED),
        ("document_ready", ReadinessBlocker.DOCUMENT_NOT_READY),
        ("manual_review_clear", ReadinessBlocker.MANUAL_REVIEW_PENDING),
        ("admin_authorized", ReadinessBlocker.ADMIN_NOT_AUTHORIZED),
    ],
)
def test_send_gate_individual_blockers(field: str, blocker: ReadinessBlocker) -> None:
    values = {
        "identity_ready": True,
        "settlement_ready": True,
        "manual_review_clear": True,
        "commission_valid": True,
        "financial_data_valid": True,
        "legacy_formula_validated": True,
        "legal_data_ready": True,
        "document_ready": True,
        "email_status": RecipientStatus.EMAIL_VALID,
        "admin_authorized": True,
        "authorization_current": True,
        "production_send_enabled": True,
    }
    values[field] = False
    result = ProductionReadinessPolicy().evaluate(ProductionReadinessInput(**values))
    assert blocker in result.blockers
    assert not result.ready_for_send


def test_safe_defaults_and_gmail_not_configured() -> None:
    settings = Settings(_env_file=None)
    capability = inspect_gmail_capability(settings)
    assert settings.email_default_mode == "OFF"
    assert settings.production_email_send_enabled is False
    assert capability.authentication.value == "NOT_CONFIGURED"
    assert capability.draft_capability.value == "NO"


def test_period_automation_mode_defaults_off_and_is_scoped(tmp_path) -> None:
    repo = EmailWorkflowRepository(tmp_path / "workflow.sqlite3")
    assert repo.mode_for_period(PERIOD) == EmailAutomationMode.OFF
    repo.set_period_mode(PERIOD, EmailAutomationMode.PREVIEW)
    assert repo.mode_for_period(PERIOD) == EmailAutomationMode.PREVIEW
    assert repo.mode_for_period("2026-08-P1") == EmailAutomationMode.OFF
    service = PeriodAuthorizationService(repo)
    with pytest.raises(PermissionError):
        service.set_safe_mode(
            user=user(Role.FINANCE),
            period_code="2026-08-P1",
            mode=EmailAutomationMode.PREVIEW,
        )


def test_preview_is_side_effect_free_and_finance_draft_permission(tmp_path) -> None:
    settings = enabled_settings(tmp_path)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    gmail = FakeGmailAdapter()
    service = Phase10EmailWorkflowService(settings=settings, repository=repo, gmail=gmail)
    item = package()
    service.preview(user(Role.FINANCE), item)
    repo.set_period_mode(PERIOD, EmailAutomationMode.DRAFT)
    draft_id = service.create_draft(
        user=user(Role.FINANCE), package=item, readiness=ready(), attachments=()
    )
    assert draft_id.startswith("fake-draft")
    assert gmail.sent == []


def test_draft_disabled_and_send_admin_only(tmp_path) -> None:
    settings = enabled_settings(tmp_path, email_allow_drafts=False)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    service = Phase10EmailWorkflowService(
        settings=settings, repository=repo, gmail=FakeGmailAdapter()
    )
    with pytest.raises(ProductionSendDisabledError):
        service.create_draft(
            user=user(Role.FINANCE), package=package(), readiness=ready(), attachments=()
        )
    authorize(repo, (package(),))
    with pytest.raises(PermissionError):
        service.send(
            user=user(Role.FINANCE),
            package=package(),
            readiness=ready(),
            authorized_packages=(package(),),
            attachments=(),
        )


def test_send_safety_flag_blocks_before_adapter(tmp_path) -> None:
    settings = enabled_settings(tmp_path, production_email_send_enabled=False)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    gmail = FakeGmailAdapter()
    service = Phase10EmailWorkflowService(settings=settings, repository=repo, gmail=gmail)
    with pytest.raises(ProductionSendDisabledError, match="PRODUCTION_SEND_DISABLED"):
        service.send(
            user=user(), package=package(), readiness=ready(), authorized_packages=(package(),), attachments=()
        )
    assert gmail.sent == []


def test_authorization_strong_confirmation_and_snapshot_hashes(tmp_path) -> None:
    repo = EmailWorkflowRepository(tmp_path / "workflow.sqlite3")
    service = PeriodAuthorizationService(repo)
    with pytest.raises(PermissionError):
        service.authorize(
            user=user(), period_code=PERIOD, mode=EmailAutomationMode.SEND,
            packages=(package(),), eligible_restaurant_count=1,
            blocked_restaurant_count=0, confirmation_text="confirm",
        )
    authorization = authorize(repo, (package(),))
    assert authorization.settlement_snapshot_hash
    assert authorization.document_snapshot_hash
    assert authorization.email_snapshot_hash


@pytest.mark.parametrize("change", ["override", "commission", "document", "recipient", "body"])
def test_authorization_stale_after_material_change(tmp_path, change: str) -> None:
    repo = EmailWorkflowRepository(tmp_path / f"{change}.sqlite3")
    original = package()
    authorization = authorize(repo, (original,))
    if change in {"override", "commission"}:
        modified = package(settlement_value="101" if change == "override" else "102")
    elif change == "document":
        modified = package(document_refs=documents(version=2))
    elif change == "recipient":
        modified = package(target=restaurant(finance_email="changed@example.com"))
    else:
        class ChangedTemplate(PartnerEmailTemplate):
            @staticmethod
            def body(period_code: str, restaurant_name: str) -> str:
                return "Changed controlled body"

        modified = package(factory=PartnerEmailPackageFactory(ChangedTemplate()))
    assert not PeriodAuthorizationService(repo).is_current(authorization, (modified,))
    assert repo.active_authorization(PERIOD) is None


def test_send_idempotency_and_double_click_protection(tmp_path) -> None:
    settings = enabled_settings(tmp_path)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    item = package()
    authorize(repo, (item,))
    gmail = FakeGmailAdapter()
    service = Phase10EmailWorkflowService(settings=settings, repository=repo, gmail=gmail)
    first = service.send(
        user=user(), package=item, readiness=ready(), authorized_packages=(item,), attachments=()
    )
    second = service.send(
        user=user(), package=item, readiness=ready(), authorized_packages=(item,), attachments=()
    )
    assert first.status == EmailWorkflowStatus.SENT
    assert second.status == EmailWorkflowStatus.ALREADY_SENT
    assert len(gmail.sent) == 1


def test_provider_failure_and_explicit_retry(tmp_path) -> None:
    settings = enabled_settings(tmp_path)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    item = package()
    authorize(repo, (item,))
    gmail = FakeGmailAdapter(fail_send_keys=frozenset({item.send_key}))
    service = Phase10EmailWorkflowService(settings=settings, repository=repo, gmail=gmail)
    failed = service.send(
        user=user(), package=item, readiness=ready(), authorized_packages=(item,), attachments=()
    )
    assert failed.status == EmailWorkflowStatus.FAILED
    unchanged = service.send(
        user=user(), package=item, readiness=ready(), authorized_packages=(item,), attachments=()
    )
    assert unchanged.status == EmailWorkflowStatus.FAILED
    gmail.fail_send_keys = frozenset()
    retried = service.send(
        user=user(), package=item, readiness=ready(), authorized_packages=(item,), attachments=(), retry=True
    )
    assert retried.status == EmailWorkflowStatus.SENT


def test_batch_partial_failure_does_not_retry_sent_package(tmp_path) -> None:
    settings = enabled_settings(tmp_path)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    one, two = package("R-1"), package("R-2")
    authorize(repo, (one, two))
    gmail = FakeGmailAdapter(fail_send_keys=frozenset({two.send_key}))
    service = Phase10EmailWorkflowService(settings=settings, repository=repo, gmail=gmail)
    readiness = {one.send_key: ready(), two.send_key: ready()}
    first = service.send_batch(
        user=user(), packages=(one, two), readiness_by_send_key=readiness,
        attachments_by_send_key={},
    )
    assert (first.sent, first.failed) == (1, 1)
    gmail.fail_send_keys = frozenset()
    retry = service.send_batch(
        user=user(), packages=(one, two), readiness_by_send_key=readiness,
        attachments_by_send_key={}, retry=True,
    )
    assert retry.sent == 1
    assert retry.already_sent == 1
    assert gmail.sent.count(one.send_key) == 1


def test_period_lock_and_reopen_invalidate_authorization(tmp_path) -> None:
    repo = EmailWorkflowRepository(tmp_path / "workflow.sqlite3")
    authorization = authorize(repo, (package(),))
    locks = Phase10PeriodLockService(repo)
    locks.lock(
        user=user(), period_code=PERIOD, intended_send_count=1, manual_close=True,
        reason="Approved manual close", confirmation_text=f"LOCK {PERIOD}",
    )
    assert repo.period_locked(PERIOD)
    locks.reopen(
        user=user(), period_code=PERIOD, reason="Controlled correction",
        confirmation_text=f"REOPEN {PERIOD}",
    )
    assert not repo.period_locked(PERIOD)
    assert repo.active_authorization(PERIOD) is None
    assert authorization.status == AuthorizationStatus.ACTIVE
    events = [item.event_type for item in repo.list_audit(PERIOD)]
    assert "PERIOD_LOCKED" in events
    assert "PERIOD_REOPENED" in events
    assert "AUTHORIZATION_STALE" in events


def test_period_lock_requires_sends_or_explicit_manual_close(tmp_path) -> None:
    repo = EmailWorkflowRepository(tmp_path / "workflow.sqlite3")
    with pytest.raises(ValueError, match="manual close"):
        Phase10PeriodLockService(repo).lock(
            user=user(),
            period_code=PERIOD,
            intended_send_count=0,
            manual_close=False,
            reason="No sends",
            confirmation_text=f"LOCK {PERIOD}",
        )


def test_period_reopen_permissions_and_override_lock_checker(tmp_path) -> None:
    repo = EmailWorkflowRepository(tmp_path / "workflow.sqlite3")
    repo.set_period_lock(PERIOD, True)
    with pytest.raises(PermissionError):
        Phase10PeriodLockService(repo).reopen(
            user=user(Role.FINANCE), period_code=PERIOD, reason="No",
            confirmation_text=f"REOPEN {PERIOD}",
        )
    assert repo.list_audit(PERIOD)[-1].event_type == "PERIOD_REOPEN_ATTEMPT_DENIED"

    override_service = FinancialOverrideService(
        FinancialOverrideRepository(tmp_path / "overrides.sqlite3"),
        repo.period_locked,
    )
    with pytest.raises(PermissionError, match="PERIOD_LOCKED"):
        override_service.create(
            period_code=PERIOD,
            restaurant_id="R-1",
            order_id="O-1",
            system_decision=FinancialDecision.MANUAL_REVIEW,
            new_decision=FinancialDecision.PAY_PARTNER,
            reason_code=OverrideReasonCode.RESTAURANT_CONFIRMED,
            comment=None,
            created_by="admin-1",
            source_engine_version="phase5-v1",
            source_decision_rule="UNKNOWN",
        )

    document_service = DocumentService(
        DocumentRegistry(tmp_path / "docs.sqlite3"),
        period_locked=repo.period_locked,
    )
    assert document_service.period_locked(PERIOD)


def test_audit_is_minimal_and_does_not_persist_email_body(tmp_path) -> None:
    settings = enabled_settings(tmp_path)
    repo = EmailWorkflowRepository(settings.email_workflow_registry_path)
    service = Phase10EmailWorkflowService(
        settings=settings, repository=repo, gmail=FakeGmailAdapter()
    )
    service.preview(user(), package())
    serialized = " ".join(item.model_dump_json() for item in repo.list_audit(PERIOD))
    assert "EMAIL_PREVIEWED" in serialized
    assert "Veuillez trouver" not in serialized
