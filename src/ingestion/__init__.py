"""CashCo ingestion package with lazy historical compatibility exports.

The deprecated Payment Scope modules are never imported by normal CashCo V2
startup. Existing integrations that explicitly request an old symbol still receive
it lazily, preserving the validated historical API without making it active logic.
"""

from importlib import import_module

from .registry import SourceManifestRegistry

_LAZY_EXPORTS = {
    "AdminEarningsIngestionResult": (
        ".admin_earnings_models",
        "AdminEarningsIngestionResult",
    ),
    "AdminEarningsIngestionService": (
        ".admin_earnings",
        "AdminEarningsIngestionService",
    ),
    "DiscoveredSource": (".models", "DiscoveredSource"),
    "EligibilityResult": (".payment_scope_models", "EligibilityResult"),
    "NormalizedAdminEarningsRow": (
        ".admin_earnings_models",
        "NormalizedAdminEarningsRow",
    ),
    "PaymentScopeEligibilityService": (
        ".payment_scope",
        "PaymentScopeEligibilityService",
    ),
    "PaymentScopeIngestionResult": (
        ".payment_scope_models",
        "PaymentScopeIngestionResult",
    ),
    "PaymentScopeIngestionService": (
        ".payment_scope",
        "PaymentScopeIngestionService",
    ),
    "PaymentScopeSnapshot": (".payment_scope_models", "PaymentScopeSnapshot"),
    "PaymentScopeSnapshotRegistry": (
        ".payment_scope_registry",
        "PaymentScopeSnapshotRegistry",
    ),
    "SourceDiscoveryResult": (".models", "SourceDiscoveryResult"),
    "SourceDiscoveryService": (".source_discovery", "SourceDiscoveryService"),
    "SourceIssue": (".models", "SourceIssue"),
}

__all__ = [
    "AdminEarningsIngestionResult",
    "AdminEarningsIngestionService",
    "DiscoveredSource",
    "EligibilityResult",
    "NormalizedAdminEarningsRow",
    "PaymentScopeEligibilityService",
    "PaymentScopeIngestionResult",
    "PaymentScopeIngestionService",
    "PaymentScopeSnapshot",
    "PaymentScopeSnapshotRegistry",
    "SourceDiscoveryResult",
    "SourceDiscoveryService",
    "SourceIssue",
    "SourceManifestRegistry",
]


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
