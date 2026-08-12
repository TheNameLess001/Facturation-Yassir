"""Restaurant registry package with lazy legacy compatibility exports."""

from importlib import import_module

from .registry_models import (
    DataQualityStatus,
    InvoiceScopeSchemaProfile,
    MappingReviewCase,
    MappingStatus,
    RegisteredRestaurant,
    RegistryIssue,
    RestaurantReadiness,
    RestaurantRegistryResult,
)

_LEGACY_EXPORTS = {
    "RSTEnrichmentResult": (".models", "RSTEnrichmentResult"),
    "RSTEnrichmentService": (".rst_enrichment", "RSTEnrichmentService"),
    "RSTListResult": (".models", "RSTListResult"),
    "RestaurantLedgerProvisioner": (".ledger", "RestaurantLedgerProvisioner"),
    "RestaurantRegistryService": (".registry", "RestaurantRegistryService"),
}

__all__ = [
    "DataQualityStatus",
    "InvoiceScopeSchemaProfile",
    "MappingReviewCase",
    "MappingStatus",
    "RSTEnrichmentResult",
    "RSTEnrichmentService",
    "RSTListResult",
    "RegisteredRestaurant",
    "RegistryIssue",
    "RestaurantLedgerProvisioner",
    "RestaurantReadiness",
    "RestaurantRegistryResult",
    "RestaurantRegistryService",
]


def __getattr__(name: str):
    if name not in _LEGACY_EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _LEGACY_EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
