from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings
from src.documents.publishing import (
    DriveDestinationCapabilityResult,
    DriveValidationResult,
    DriveValidationStatus,
)
from src.emails.phase10_models import GmailAuthenticationStatus, GmailCapability
from src.emails.runtime import EmailCenterSnapshot, build_email_center_snapshot
from src.emails.sandbox import (
    GmailSandboxCapability,
    SandboxDraftRecord,
    SandboxDraftStatus,
)
from src.operations.go_live import (
    GoLiveReadiness,
    GoLiveReadinessInput,
    GoLiveReadinessPolicy,
)
from src.restaurants.registry_models import LegalMasterSyncStatus
from src.settlement.legacy_validation import LegacyFormulaRegistry
from src.settlement.phase5_runtime import Phase5Workspace


@dataclass(frozen=True)
class GoLiveOperationalSnapshot:
    period_code: str
    document_ready: int
    email_package_ready: int
    ready_candidates_affected_by_legal_master_issues: int
    destination: DriveDestinationCapabilityResult
    drive_validation: DriveValidationResult | None
    gmail: GmailCapability
    sandbox: GmailSandboxCapability
    sandbox_draft: SandboxDraftRecord | None
    readiness: GoLiveReadiness
    email_snapshot: EmailCenterSnapshot


def build_go_live_snapshot(
    workspace: Phase5Workspace,
    *,
    settings: Settings,
    destination: DriveDestinationCapabilityResult,
    gmail: GmailCapability,
    sandbox: GmailSandboxCapability,
    drive_validation: DriveValidationResult | None = None,
    sandbox_draft: SandboxDraftRecord | None = None,
) -> GoLiveOperationalSnapshot:
    email = build_email_center_snapshot(workspace, settings=settings)
    legal_source = workspace.registry.partner_legal_master
    issue_ids = {
        item.restaurant_id
        for item in (legal_source.issues if legal_source else ())
        if item.restaurant_id
    }
    ready_ids = {
        item.restaurant_id for item in email.rows if item.preauthorization_ready
    }
    legal_master_connected = bool(
        legal_source
        and legal_source.status
        == LegalMasterSyncStatus.CONNECTED
    )
    storage_ready = bool(
        destination.can_create
        and drive_validation
        and drive_validation.status
        in {DriveValidationStatus.PASS, DriveValidationStatus.ALREADY_VALIDATED}
    )
    sandbox_ready = bool(
        sandbox_draft
        and sandbox_draft.status
        in {SandboxDraftStatus.CREATED, SandboxDraftStatus.ALREADY_CREATED}
    )
    readiness = GoLiveReadinessPolicy().evaluate(
        GoLiveReadinessInput(
            financial_policy_ready=LegacyFormulaRegistry().certification().production_ready,
            partner_legal_master_connected=legal_master_connected,
            settlement_engine_ready=bool(workspace.summary.restaurants),
            document_rendering_ready=email.document_ready > 0,
            document_storage_ready=storage_ready,
            email_package_ready=email.email_ready > 0,
            gmail_auth_ready=(
                gmail.authentication == GmailAuthenticationStatus.PASS
            ),
            gmail_sender_ready=sandbox.sender_configured,
            sandbox_validation_ready=sandbox_ready,
            admin_workflow_ready=True,
            idempotency_ready=True,
            audit_ready=True,
            production_send_enabled=settings.production_email_send_enabled,
        )
    )
    return GoLiveOperationalSnapshot(
        period_code=workspace.summary.period.period_code,
        document_ready=email.document_ready,
        email_package_ready=email.email_ready,
        ready_candidates_affected_by_legal_master_issues=len(ready_ids & issue_ids),
        destination=destination,
        drive_validation=drive_validation,
        gmail=gmail,
        sandbox=sandbox,
        sandbox_draft=sandbox_draft,
        readiness=readiness,
        email_snapshot=email,
    )
