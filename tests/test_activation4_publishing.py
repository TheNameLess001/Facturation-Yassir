from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd

from src.config import Settings
from src.documents.legal_readiness import CashCoDocumentType
from src.documents.phase8 import Phase8DocumentEngine
from src.documents.publishing import (
    DocumentPublicationRepository,
    DocumentPublicationStatus,
    DocumentPublishingService,
    DocumentPublishMode,
    DocumentStorageMode,
    DriveDestinationType,
    DriveValidationStatus,
    FakeDocumentDriveProvider,
    inspect_drive_destination,
    validate_document_storage_write,
)
from src.emails.gmail_adapter import FakeGmailAdapter, validate_gmail_capability
from src.emails.packages import PartnerEmailPackageFactory
from src.emails.phase10_models import DocumentAttachmentRef
from src.emails.sandbox import (
    GmailAuthMethod,
    GmailSandboxDraftService,
    GmailSandboxPackageFactory,
    SandboxDraftStatus,
    inspect_gmail_sandbox,
)
from src.emails.workflow_repository import EmailWorkflowRepository
from src.google.auth import build_document_storage_credentials
from src.google.gmail_auth import build_gmail_credentials
from src.google.models import DriveFile
from src.operations.activation4 import run_activation4_dry_run
from src.operations.go_live import (
    GoLiveBlocker,
    GoLiveReadinessInput,
    GoLiveReadinessPolicy,
    GoLiveStatus,
)
from src.restaurants.scope_registry import RestaurantRegistryBuilder
from src.restaurants.source_reader import RestaurantSourceReader
from src.settlement.periods import SettlementPeriodService
from src.settlement.phase5_runtime import Phase5Workspace
from src.settlement.phase5_service import Phase5SettlementService


def workspace() -> Phase5Workspace:
    scope = pd.DataFrame(
        [{"Restaurant": "Restaurant One", "Restaurant ID": "R1", "Commission": "20"}]
    )
    rst = pd.DataFrame(
        [
            {
                "Restaurant ID": "R1",
                "Restaurant Name": "Restaurant One",
                "Address": "1 Approved Address",
                "Email": "finance@example.test",
            }
        ]
    )
    registry = RestaurantRegistryBuilder().build(
        scope,
        rst,
        invoice_scope_profile=RestaurantSourceReader.profile_invoice_frame(scope),
        rst_profile=RestaurantSourceReader.profile_rst_frame(rst),
    )
    orders = pd.DataFrame(
        [
            {
                "order_id": "O1",
                "restaurant_id": "R1",
                "restaurant_name": "Restaurant One",
                "order_date": "2026-07-20",
                "operational_status": "Delivered",
                "cancellation_reason": None,
                "item_total": "120",
            }
        ]
    )
    summary = Phase5SettlementService().evaluate(
        SettlementPeriodService().get("2026-07-P2", as_of=date(2026, 8, 12)),
        orders,
        registry,
    )
    return Phase5Workspace(summary=summary, registry=registry)


def root(*, shared: bool = False, create: bool = True) -> DriveFile:
    return DriveFile(
        file_id="documents",
        name="05_DOCUMENTS",
        mime_type="application/vnd.google-apps.folder",
        modified_time=datetime(2026, 8, 14, tzinfo=UTC),
        drive_id="shared-id" if shared else None,
        spaces=("drive",),
        is_folder=True,
        capabilities={
            "canAddChildren": create,
            "canEdit": True,
            "canDelete": False,
        },
    )


def candidates():
    item = workspace()
    restaurant = item.registry.restaurants[0]
    settlement = item.summary.restaurants[0]
    engine = Phase8DocumentEngine()
    return tuple(
        engine.production_candidate(kind, restaurant, settlement)
        for kind in CashCoDocumentType
    )


def test_rendered_documents_use_certified_values_and_deterministic_hash() -> None:
    candidate = candidates()[0]
    first = Phase8DocumentEngine.render_production_document(candidate)
    second = Phase8DocumentEngine.render_production_document(candidate)
    rendered = first.content.decode()
    assert first.document_hash == second.document_hash == candidate.document_hash
    assert "cashco_legacy_v1" in rendered
    assert "20.00 MAD" in rendered
    assert "4.00 MAD" in rendered
    assert "24.00 MAD" in rendered
    assert "123456" not in rendered


def test_document_hashes_cover_financial_legal_and_versioned_content() -> None:
    first = candidates()[0]
    assert first.financial_snapshot_hash
    assert first.legal_snapshot_hash
    assert first.settlement_snapshot_hash
    assert first.document_hash
    assert len({first.document_hash, first.financial_snapshot_hash, first.legal_snapshot_hash}) == 3


def test_drive_capability_distinguishes_my_and_shared_drive() -> None:
    my_drive = inspect_drive_destination(FakeDocumentDriveProvider(root()), "documents")
    shared = inspect_drive_destination(
        FakeDocumentDriveProvider(root(shared=True)), "documents"
    )
    unavailable = inspect_drive_destination(
        FakeDocumentDriveProvider(root(create=False)), "documents"
    )
    assert my_drive.destination_type == DriveDestinationType.MY_DRIVE
    assert shared.destination_type == DriveDestinationType.SHARED_DRIVE
    assert my_drive.can_create
    assert not unavailable.can_create


def test_preview_never_creates_and_sample_publication_is_idempotent(tmp_path) -> None:
    provider = FakeDocumentDriveProvider(root())
    capability = inspect_drive_destination(provider, "documents")
    repository = DocumentPublicationRepository(tmp_path / "publications.sqlite3")
    preview = DocumentPublishingService(
        provider, repository, capability, mode=DocumentPublishMode.PREVIEW
    ).publish(candidates()[:1])
    assert preview.not_published == 1
    assert provider.created == []
    service = DocumentPublishingService(
        provider, repository, capability, mode=DocumentPublishMode.SAMPLE
    )
    first = service.publish(candidates()[:1])
    second = service.publish(candidates()[:1])
    assert first.published == 1
    assert second.already_published == 1
    assert len(provider.created) == 1


def test_create_unavailable_and_partial_failure_are_independent(tmp_path) -> None:
    blocked_provider = FakeDocumentDriveProvider(root(create=False))
    blocked = DocumentPublishingService(
        blocked_provider,
        DocumentPublicationRepository(tmp_path / "blocked.sqlite3"),
        inspect_drive_destination(blocked_provider, "documents"),
        mode=DocumentPublishMode.SAMPLE,
    ).publish(candidates()[:1])
    assert blocked.failed == 1
    assert blocked.publications[0].error_code == "CREATE_NOT_AVAILABLE"

    items = candidates()
    fail_name = f"TEST_CASHCO_{Phase8DocumentEngine.render_production_document(items[1]).filename}"
    provider = FakeDocumentDriveProvider(root(), fail_names=frozenset({fail_name}))
    result = DocumentPublishingService(
        provider,
        DocumentPublicationRepository(tmp_path / "partial.sqlite3"),
        inspect_drive_destination(provider, "documents"),
        mode=DocumentPublishMode.SAMPLE,
    ).publish(items)
    assert result.published == 2
    assert result.failed == 1
    assert {item.status for item in result.publications} >= {
        DocumentPublicationStatus.PUBLISHED,
        DocumentPublicationStatus.FAILED,
    }
    provider.fail_names = frozenset()
    retry = DocumentPublishingService(
        provider,
        DocumentPublicationRepository(tmp_path / "partial.sqlite3"),
        inspect_drive_destination(provider, "documents"),
        mode=DocumentPublishMode.SAMPLE,
    ).publish((items[1],))
    assert retry.published == 1
    assert retry.requested == 1


def test_activation4_publisher_refuses_production_mode(tmp_path) -> None:
    provider = FakeDocumentDriveProvider(root())
    result = DocumentPublishingService(
        provider,
        DocumentPublicationRepository(tmp_path / "production.sqlite3"),
        inspect_drive_destination(provider, "documents"),
        mode=DocumentPublishMode.PRODUCTION,
    ).publish(candidates()[:1])
    assert result.failed == 1
    assert result.publications[0].error_code == "PRODUCTION_MODE_NOT_AUTHORIZED"
    assert provider.created == []


def test_gmail_defaults_disabled_and_service_account_has_no_delegation() -> None:
    settings = Settings(
        _env_file=None,
        google_service_account_json='{"type":"service_account"}',
    )
    capability = inspect_gmail_sandbox(settings)
    assert capability.auth_method == GmailAuthMethod.SERVICE_ACCOUNT_WITHOUT_DELEGATION
    assert not capability.sandbox_available
    assert not capability.draft_execution_allowed
    assert not capability.send_execution_allowed
    assert settings.production_email_send_enabled is False


def test_sandbox_recipient_replaces_production_recipient_and_prefixes_subject() -> None:
    item = workspace()
    restaurant = item.registry.restaurants[0]
    package = PartnerEmailPackageFactory().create(
        period_code="2026-07-P2",
        restaurant=restaurant,
        financial_status="READY",
        settlement_snapshot={"value": "120"},
        document_refs=(
            DocumentAttachmentRef(
                document_type="INVOICE",
                document_id="doc-1",
                version=1,
                content_hash="hash",
                status="PRODUCTION_READY",
            ),
        ),
    )
    settings = Settings(
        _env_file=None,
        gmail_auth_mode="OAUTH",
        gmail_oauth_client_id="synthetic-id",
        gmail_oauth_client_secret="synthetic-secret",
        gmail_oauth_refresh_token="synthetic-token",
        gmail_sender_email="cashco@example.test",
        gmail_execution_mode="SANDBOX",
        gmail_sandbox_recipient="sandbox@example.test",
    )
    sandbox = GmailSandboxPackageFactory().build(
        package, inspect_gmail_sandbox(settings)
    )
    assert sandbox.recipient_to == "sandbox@example.test"
    assert sandbox.recipient_to != package.recipient_to
    assert sandbox.subject.startswith("[TEST CASHCO]")
    assert "TEST / DRY RUN" in sandbox.body
    assert "RIB" not in sandbox.body


def test_end_to_end_dry_run_stops_before_provider_send(tmp_path) -> None:
    result = run_activation4_dry_run(
        workspace(),
        sample_size=3,
        settings=Settings(
            _env_file=None,
            email_workflow_registry_path=tmp_path / "email.sqlite3",
        ),
    )
    assert result.fully_document_ready == 1
    assert result.email_packages_buildable == 1
    assert result.production_send_eligible == 0
    assert result.gmail_provider_calls == 0
    assert result.samples[0].provider_send == "NOT_CALLED"
    assert result.samples[0].production_safety_flag == "OFF"
    assert result.samples[0].reconciliation_difference == Decimal(0)
    assert "DRY_RUN_COMPLETED" in result.audit_events


def test_shared_drive_and_oauth_storage_modes_are_explicit() -> None:
    shared = FakeDocumentDriveProvider(root(shared=True))
    valid = inspect_drive_destination(
        shared,
        "documents",
        storage_mode=DocumentStorageMode.SHARED_DRIVE,
        shared_drive_id="shared-id",
    )
    mismatch = inspect_drive_destination(
        shared,
        "documents",
        storage_mode=DocumentStorageMode.SHARED_DRIVE,
        shared_drive_id="another-drive",
    )
    oauth = inspect_drive_destination(
        FakeDocumentDriveProvider(root()),
        "documents",
        storage_mode=DocumentStorageMode.OAUTH_USER,
    )
    disabled_provider = FakeDocumentDriveProvider(root())
    disabled = inspect_drive_destination(
        disabled_provider,
        "documents",
        storage_mode=DocumentStorageMode.DISABLED,
    )
    assert valid.can_create and valid.can_list and valid.can_retrieve_metadata
    assert not mismatch.can_create
    assert mismatch.configuration_error == "SHARED_DRIVE_ID_MISMATCH"
    assert oauth.can_create
    assert not disabled.can_create
    assert disabled_provider.created == []


def test_synthetic_drive_validation_is_readable_and_idempotent() -> None:
    provider = FakeDocumentDriveProvider(root(shared=True))
    destination = inspect_drive_destination(
        provider,
        "documents",
        storage_mode=DocumentStorageMode.SHARED_DRIVE,
        shared_drive_id="shared-id",
    )
    first = validate_document_storage_write(provider, destination)
    second = validate_document_storage_write(provider, destination)
    assert first.status == DriveValidationStatus.PASS
    assert first.created and first.read_back and first.metadata_verified
    assert second.status == DriveValidationStatus.ALREADY_VALIDATED
    assert second.idempotent
    assert provider.created == ["CASHCO_VALIDATION_TEST.txt"]


def test_sandbox_draft_is_recipient_safe_draft_only_and_idempotent(tmp_path) -> None:
    item = workspace()
    restaurant = item.registry.restaurants[0]
    package = PartnerEmailPackageFactory().create(
        period_code="2026-07-P2",
        restaurant=restaurant,
        financial_status="READY",
        settlement_snapshot={"value": "120"},
        document_refs=(
            DocumentAttachmentRef(
                document_type="INVOICE",
                document_id="doc-1",
                version=1,
                content_hash="hash",
                status="PRODUCTION_READY",
            ),
        ),
    )
    settings = Settings(
        _env_file=None,
        gmail_auth_mode="OAUTH",
        gmail_oauth_client_id="synthetic-id",
        gmail_oauth_client_secret="synthetic-secret",
        gmail_oauth_refresh_token="synthetic-token",
        gmail_sender_email="cashco@example.test",
        gmail_execution_mode="SANDBOX",
        gmail_sandbox_recipient="sandbox@example.test",
        gmail_sandbox_allow_drafts=True,
        email_allow_drafts=True,
        gmail_sandbox_send_enabled=False,
        production_email_send_enabled=False,
        email_workflow_registry_path=tmp_path / "sandbox.sqlite3",
    )
    gmail = FakeGmailAdapter()
    repository = EmailWorkflowRepository(settings.email_workflow_registry_path)
    service = GmailSandboxDraftService(settings, repository, gmail)
    first = service.create_draft(package, (b"synthetic document",))
    second = service.create_draft(package, (b"synthetic document",))
    assert first.status == SandboxDraftStatus.CREATED
    assert first.recipient == "sandbox@example.test"
    assert second.status == SandboxDraftStatus.ALREADY_CREATED
    assert len(gmail.drafts) == 1
    assert gmail.sent == []
    assert not inspect_gmail_sandbox(settings).send_execution_allowed
    assert settings.production_email_send_enabled is False
    assert repository.list_audit("2026-07-P2")[-1].event_type == "GMAIL_SANDBOX_DRAFT_CREATED"


def test_go_live_readiness_requires_infrastructure_but_not_production_send() -> None:
    ready = GoLiveReadinessInput(
        financial_policy_ready=True,
        partner_legal_master_connected=True,
        settlement_engine_ready=True,
        document_rendering_ready=True,
        document_storage_ready=True,
        email_package_ready=True,
        gmail_auth_ready=True,
        gmail_sender_ready=True,
        sandbox_validation_ready=True,
        admin_workflow_ready=True,
        idempotency_ready=True,
        audit_ready=True,
        production_send_enabled=False,
    )
    result = GoLiveReadinessPolicy().evaluate(ready)
    blocked = GoLiveReadinessPolicy().evaluate(
        ready.model_copy(update={"document_storage_ready": False})
    )
    unsafe = GoLiveReadinessPolicy().evaluate(
        ready.model_copy(update={"production_send_enabled": True})
    )
    assert result.status == GoLiveStatus.READY_FOR_GO_LIVE_AUTHORIZATION
    assert result.production_send_flag == "OFF"
    assert GoLiveBlocker.DOCUMENT_STORAGE_NOT_READY in blocked.blockers
    assert GoLiveBlocker.PRODUCTION_FLAG_UNSAFELY_ENABLED in unsafe.blockers


def test_oauth_user_credentials_are_external_and_scope_specific() -> None:
    authorized_user = json.dumps(
        {
            "type": "authorized_user",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    drive = build_document_storage_credentials(
        Settings(
            _env_file=None,
            document_storage_mode="OAUTH_USER",
            google_oauth_user_json=authorized_user,
        )
    )
    gmail = build_gmail_credentials(
        Settings(
            _env_file=None,
            gmail_auth_mode="OAUTH",
            gmail_sender_email="cashco@example.test",
            gmail_oauth_user_json=authorized_user,
        )
    )
    assert drive.refresh_token == "refresh-token"
    assert gmail.refresh_token == "refresh-token"


def test_domain_delegation_uses_explicit_company_identity(monkeypatch) -> None:
    captured = {}

    class FakeDelegatedCredentials:
        def with_subject(self, subject):
            captured["subject"] = subject
            return self

    monkeypatch.setattr(
        "src.google.gmail_auth.service_account.Credentials.from_service_account_info",
        lambda info, scopes: FakeDelegatedCredentials(),
    )
    service_account_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "project",
            "private_key_id": "key-id",
            "private_key": "external-secret-placeholder",
            "client_email": "service@example.test",
            "client_id": "client-id",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    credentials = build_gmail_credentials(
        Settings(
            _env_file=None,
            google_service_account_json=service_account_json,
            gmail_auth_mode="DOMAIN_DELEGATION",
            gmail_sender_email="cashco@example.test",
            gmail_domain_delegated_user="cashco@example.test",
        )
    )
    assert isinstance(credentials, FakeDelegatedCredentials)
    assert captured["subject"] == "cashco@example.test"


def test_gmail_profile_must_match_explicit_sender() -> None:
    class Request:
        def __init__(self, email):
            self.email = email

        def execute(self):
            return {"emailAddress": self.email}

    class Users:
        def __init__(self, email):
            self.email = email

        def getProfile(self, *, userId):
            assert userId == "me"
            return Request(self.email)

    class Api:
        def __init__(self, email):
            self.email = email

        def users(self):
            return Users(self.email)

    settings = Settings(
        _env_file=None,
        gmail_auth_mode="OAUTH",
        gmail_oauth_client_id="synthetic-id",
        gmail_oauth_client_secret="synthetic-secret",
        gmail_oauth_refresh_token="synthetic-token",
        gmail_sender_email="cashco@example.test",
    )
    matched = validate_gmail_capability(
        settings, api=Api("cashco@example.test")
    )
    mismatch = validate_gmail_capability(
        settings, api=Api("another@example.test")
    )
    assert matched.authentication.value == "PASS"
    assert mismatch.authentication.value == "FAIL"


def test_full_sandbox_rehearsal_publishes_one_restaurant_and_creates_one_draft(
    tmp_path,
) -> None:
    provider = FakeDocumentDriveProvider(root(shared=True))
    destination = inspect_drive_destination(
        provider,
        "documents",
        storage_mode=DocumentStorageMode.SHARED_DRIVE,
        shared_drive_id="shared-id",
    )
    assert validate_document_storage_write(provider, destination).status == "PASS"
    items = candidates()
    publication = DocumentPublishingService(
        provider,
        DocumentPublicationRepository(tmp_path / "documents.sqlite3"),
        destination,
        mode=DocumentPublishMode.SAMPLE,
    ).publish(items)
    assert publication.published == 3

    active = workspace()
    restaurant = active.registry.restaurants[0]
    settlement = active.summary.restaurants[0]
    references = tuple(
        DocumentAttachmentRef(
            document_type=item.document_type.value,
            document_id=item.document_reference,
            version=item.document_version,
            content_hash=item.document_hash,
            status=item.status.value,
        )
        for item in items
    )
    package = PartnerEmailPackageFactory().create(
        period_code="2026-07-P2",
        restaurant=restaurant,
        financial_status="READY",
        settlement_snapshot=settlement.model_dump(mode="json"),
        document_refs=references,
    )
    settings = Settings(
        _env_file=None,
        gmail_auth_mode="OAUTH",
        gmail_oauth_client_id="synthetic-id",
        gmail_oauth_client_secret="synthetic-secret",
        gmail_oauth_refresh_token="synthetic-token",
        gmail_sender_email="cashco@example.test",
        gmail_execution_mode="SANDBOX",
        gmail_sandbox_recipient="sandbox@example.test",
        gmail_sandbox_allow_drafts=True,
        email_allow_drafts=True,
        production_email_send_enabled=False,
        email_workflow_registry_path=tmp_path / "workflow.sqlite3",
    )
    gmail = FakeGmailAdapter()
    draft = GmailSandboxDraftService(
        settings,
        EmailWorkflowRepository(settings.email_workflow_registry_path),
        gmail,
    ).create_draft(
        package,
        tuple(
            Phase8DocumentEngine.render_production_document(item).content
            for item in items
        ),
    )
    assert draft.status == SandboxDraftStatus.CREATED
    assert draft.recipient == "sandbox@example.test"
    assert len(gmail.drafts) == 1
    assert gmail.sent == []
    assert settings.production_email_send_enabled is False
