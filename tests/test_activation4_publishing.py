from __future__ import annotations

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
    DriveDestinationType,
    FakeDocumentDriveProvider,
    inspect_drive_destination,
)
from src.emails.packages import PartnerEmailPackageFactory
from src.emails.phase10_models import DocumentAttachmentRef
from src.emails.sandbox import (
    GmailAuthMethod,
    GmailSandboxPackageFactory,
    inspect_gmail_sandbox,
)
from src.google.models import DriveFile
from src.operations.activation4 import run_activation4_dry_run
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
