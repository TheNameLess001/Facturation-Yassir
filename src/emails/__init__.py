from .attachments import R2AttachmentLoader, R2DocumentSource, StoredDocument
from .authorization import AutomationAuthorizationService
from .gmail_adapter import (
    FakeGmailAdapter,
    GmailAdapter,
    ProductionGmailAdapter,
    inspect_gmail_capability,
)
from .packages import PartnerEmailPackageFactory, resolve_recipient
from .period_locking import Phase10PeriodLockService
from .phase10_authorization import PeriodAuthorizationService
from .readiness import ProductionReadinessInput, ProductionReadinessPolicy
from .registry import EmailRegistry
from .service import EmailExecutionService, EmailPreparationService
from .workflow_repository import EmailWorkflowRepository
from .workflow_service import Phase10EmailWorkflowService

__all__ = [
    "AutomationAuthorizationService",
    "EmailExecutionService",
    "EmailPreparationService",
    "EmailRegistry",
    "EmailWorkflowRepository",
    "FakeGmailAdapter",
    "GmailAdapter",
    "PartnerEmailPackageFactory",
    "PeriodAuthorizationService",
    "Phase10EmailWorkflowService",
    "Phase10PeriodLockService",
    "ProductionGmailAdapter",
    "ProductionReadinessInput",
    "ProductionReadinessPolicy",
    "R2AttachmentLoader",
    "R2DocumentSource",
    "StoredDocument",
    "inspect_gmail_capability",
    "resolve_recipient",
]
