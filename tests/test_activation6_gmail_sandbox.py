from __future__ import annotations

import hashlib

import pytest

from src.config import Settings
from src.emails.attachments import R2AttachmentLoader, StoredDocument
from src.emails.gmail_adapter import FakeGmailAdapter
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
from src.restaurants.registry_models import RegisteredRestaurant


def _package(content_hash: str = "hash"):
    restaurant = RegisteredRestaurant.model_construct(
        restaurant_id="R-001",
        restaurant_name="Restaurant Test",
        email="partner@example.com",
        finance_email=None,
    )
    return PartnerEmailPackageFactory().create(
        period_code="2026-07-P2",
        restaurant=restaurant,
        financial_status="READY",
        settlement_snapshot={"net": "100.00"},
        document_refs=tuple(
            DocumentAttachmentRef(
                document_type=kind,
                document_id=f"doc-{index}",
                version=1,
                content_hash=content_hash,
                status="PRODUCTION_READY",
            )
            for index, kind in enumerate(
                ("INVOICE", "NOTE_DE_DEBOURS", "PARTNER_STATEMENT"), start=1
            )
        ),
    )


def _settings(tmp_path=None, **updates):
    values = {
        "_env_file": None,
        "gmail_auth_mode": "OAUTH",
        "gmail_sender_email": "billing@example.com",
        "gmail_execution_mode": "SANDBOX",
        "gmail_sandbox_recipient": "internal@example.com",
        "gmail_sandbox_send_enabled": False,
        "production_email_send_enabled": False,
    }
    if tmp_path:
        values["email_workflow_registry_path"] = tmp_path / "workflow.sqlite3"
    values.update(updates)
    return Settings(**values)


def test_disabled_defaults_and_plain_service_account_are_blocked() -> None:
    defaults = Settings(_env_file=None)
    plain_sa = Settings(
        _env_file=None, google_service_account_json='{"type":"service_account"}'
    )
    assert defaults.gmail_execution_mode == "DISABLED"
    assert not defaults.gmail_sandbox_send_enabled
    assert not defaults.production_email_send_enabled
    assert (
        inspect_gmail_sandbox(plain_sa).auth_method
        == GmailAuthMethod.SERVICE_ACCOUNT_WITHOUT_DELEGATION
    )
    assert not inspect_gmail_sandbox(plain_sa).draft_execution_allowed


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"gmail_sender_email": None}, "sender"),
        ({"gmail_sandbox_recipient": None}, "recipient"),
        ({"gmail_execution_mode": "DISABLED"}, "mode"),
    ],
)
def test_sandbox_requirements(updates, expected) -> None:
    capability = inspect_gmail_sandbox(_settings(**updates))
    assert not capability.draft_execution_allowed
    assert expected


def test_sandbox_package_isolates_production_recipient() -> None:
    original = _package()
    sandbox = GmailSandboxPackageFactory().build(
        original, inspect_gmail_sandbox(_settings())
    )
    assert original.recipient_to == "partner@example.com"
    assert sandbox.recipient_to == "internal@example.com"
    assert "partner@example.com" not in sandbox.model_dump_json()
    assert sandbox.subject.startswith("[TEST CASHCO]")
    assert "TEST / DRY RUN" in sandbox.body
    assert "RIB" not in sandbox.body


class _R2:
    def __init__(self, objects):
        self.objects = objects

    def get_document(self, object_key):
        return self.objects[object_key]


def test_r2_attachment_loading_validates_all_snapshot_metadata() -> None:
    content = b"%PDF-1.7\ncontrolled"
    digest = hashlib.sha256(content).hexdigest()
    package = _package(digest)
    objects = {}
    keys = {}
    for ref in package.document_refs:
        key = f"2026-07-P2/R-001/{ref.document_id}.pdf"
        keys[ref.document_id] = key
        objects[key] = StoredDocument(
            object_key=key,
            content=content,
            content_type="application/pdf",
            content_hash=digest,
            document_id=ref.document_id,
            document_type=ref.document_type,
            version=ref.version,
            period_code=package.period_code,
            restaurant_id=package.restaurant_id,
            financial_snapshot_hash=package.settlement_snapshot_hash,
        )
    assert R2AttachmentLoader(_R2(objects), keys).load(package) == (content,) * 3


def test_r2_attachment_hash_drift_is_blocked() -> None:
    package = _package("expected")
    ref = package.document_refs[0]
    key = "doc.pdf"
    item = StoredDocument(
        object_key=key,
        content=b"changed",
        content_type="application/pdf",
        content_hash="expected",
        document_id=ref.document_id,
        document_type=ref.document_type,
        version=1,
        period_code=package.period_code,
        restaurant_id=package.restaurant_id,
        financial_snapshot_hash=package.settlement_snapshot_hash,
    )
    with pytest.raises(ValueError, match="DOCUMENT_HASH_MISMATCH"):
        R2AttachmentLoader(_R2({key: item}), {ref.document_id: key})._load_one(
            package, ref
        )


def test_exact_sandbox_draft_is_idempotent_and_never_sends(tmp_path) -> None:
    settings = _settings(tmp_path)
    repository = EmailWorkflowRepository(settings.email_workflow_registry_path)
    gmail = FakeGmailAdapter()
    service = GmailSandboxDraftService(settings, repository, gmail)
    package = _package()
    first = service.create_draft(package, (b"one", b"two", b"three"))
    second = service.create_draft(package, (b"one", b"two", b"three"))
    assert first.status == SandboxDraftStatus.CREATED
    assert second.status == SandboxDraftStatus.ALREADY_CREATED
    assert len(gmail.drafts) == 1
    assert gmail.sent == []
    assert [
        event.event_type for event in repository.list_audit(package.period_code)
    ] == [
        "GMAIL_SANDBOX_PACKAGE_BUILT",
        "GMAIL_SANDBOX_DRAFT_CREATED",
    ]
