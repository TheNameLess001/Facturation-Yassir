from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MappingStatus(StrEnum):
    MATCHED_BY_ID = "MATCHED_BY_ID"
    MATCHED_BY_EXACT_NAME = "MATCHED_BY_EXACT_NAME"
    MATCHED_BY_ALIAS = "MATCHED_BY_ALIAS"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    DUPLICATE_SCOPE = "DUPLICATE_SCOPE"
    CONFLICTING_SCOPE = "CONFLICTING_SCOPE"
    SCOPE_ID_NAME_MISMATCH = "SCOPE_ID_NAME_MISMATCH"


class DataQualityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class RegistryIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class SuggestionStrength(StrEnum):
    STRONG_SINGLE_CANDIDATE = "STRONG_SINGLE_CANDIDATE"
    MULTIPLE_PLAUSIBLE_CANDIDATES = "MULTIPLE_PLAUSIBLE_CANDIDATES"
    WEAK_SUGGESTION = "WEAK_SUGGESTION"
    NO_USEFUL_CANDIDATE = "NO_USEFUL_CANDIDATE"
    NOT_REQUIRED = "NOT_REQUIRED"


class CorrectionConfidence(StrEnum):
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    MEDIUM_CONFIDENCE = "MEDIUM_CONFIDENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_CANDIDATE = "NO_CANDIDATE"
    NOT_REQUIRED = "NOT_REQUIRED"


class ScopeConflictReason(StrEnum):
    NAME_CONFLICT = "NAME_CONFLICT"
    CITY_CONFLICT = "CITY_CONFLICT"
    COMMISSION_CONFLICT = "COMMISSION_CONFLICT"
    MULTI_FIELD_CONFLICT = "MULTI_FIELD_CONFLICT"
    OTHER = "OTHER"


class ConflictInterpretation(StrEnum):
    SAME_STORE_DUPLICATED = "SAME_STORE_DUPLICATED"
    DIFFERENT_STORES_SHARING_ID = "DIFFERENT_STORES_INCORRECTLY_SHARING_ONE_ID"
    OLD_NEW_RESTAURANT_NAMING = "OLD_NEW_RESTAURANT_NAMING"
    DATA_ENTRY_ERROR = "DATA_ENTRY_ERROR"
    UNCERTAIN = "UNCERTAIN"


class NoIdClassification(StrEnum):
    EXACT_NAME_MAPPED = "EXACT_NAME_MAPPED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    CONFLICTING_OR_DUPLICATE = "CONFLICTING_OR_DUPLICATE"
    OTHER = "OTHER"


class WorksheetSchemaProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    worksheet_name: str
    header_row: int
    columns: tuple[str, ...]
    row_count: int
    blank_rows: int
    duplicate_rows: int
    field_types: dict[str, str]


class InvoiceScopeSchemaProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    mime_type: str
    active_worksheet: str
    worksheets: tuple[WorksheetSchemaProfile, ...]
    profiled_at: datetime

    @property
    def active(self) -> WorksheetSchemaProfile:
        return next(
            item
            for item in self.worksheets
            if item.worksheet_name == self.active_worksheet
        )


class RSTSchemaProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    mime_type: str
    columns: tuple[str, ...]
    row_count: int
    blank_rows: int
    duplicate_rows: int
    field_types: dict[str, str]
    profiled_at: datetime


class RegistryIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: RegistryIssueSeverity
    message: str
    scope_source_row: int | None = None
    restaurant_id: str | None = None
    restaurant_name: str | None = None


class ScopeSourceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_row: int
    restaurant_name: str | None = None
    restaurant_id: str | None = None
    city: str | None = None
    area: str | None = None
    phone: str | None = None
    email: str | None = None
    commission_rate: Decimal | None = None
    comment: str | None = None
    extra_fields: dict[str, str | None] = Field(default_factory=dict)


class RestaurantCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    restaurant_id: str
    restaurant_name: str | None = None
    city: str | None = None
    area: str | None = None
    chain: str | None = None
    address: str | None = None
    store_type: str | None = None
    status: str | None = None
    commission_rate: Decimal | None = None
    email: str | None = None
    phone: str | None = None
    admin_restaurant_name: str | None = None
    canonical_order_count: int = 0
    name_similarity: float
    same_city: bool = False
    chain_signal: bool = False
    token_overlap: float = 0.0
    advisory_score: float
    confidence: CorrectionConfidence
    similarity_indicators: tuple[str, ...] = ()


class CopyFixData(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope_row: int
    current_restaurant_id: str | None = None
    suggested_restaurant_id: str | None = None
    suggested_restaurant_name: str | None = None
    suggested_city: str | None = None


class MappingReviewCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_key: str
    mapping_status: MappingStatus
    mapping_method: str
    identity_ready: bool
    scope_rows: tuple[ScopeSourceRow, ...]
    candidates: tuple[RestaurantCandidate, ...] = ()
    conflict_fields: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    suggestion_strength: SuggestionStrength = SuggestionStrength.NOT_REQUIRED
    correction_confidence: CorrectionConfidence = CorrectionConfidence.NOT_REQUIRED
    conflict_reason: ScopeConflictReason | None = None
    conflict_interpretation: ConflictInterpretation | None = None
    scope_id_rst_candidate: RestaurantCandidate | None = None
    copy_fix: CopyFixData

    @property
    def likely_candidate(self) -> RestaurantCandidate | None:
        return self.candidates[0] if self.candidates else None


class RestaurantReadiness(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity_ready: bool
    orders_available: bool
    settlement_ready: bool | None = None
    document_ready: bool
    email_ready: bool
    payment_ready: bool


class RegisteredRestaurant(BaseModel):
    model_config = ConfigDict(frozen=True)

    restaurant_id: str | None = None
    restaurant_name: str | None = None
    chain: str | None = None
    is_chain: bool = False
    legal_entity: str | None = None
    ice: str | None = None
    if_number: str | None = None
    rc: str | None = None
    rib: str | None = None
    bank: str | None = None
    address: str | None = None
    email: str | None = None
    finance_email: str | None = None
    phone: str | None = None
    city: str | None = None
    area: str | None = None
    account_manager: str | None = None
    commission_rate: Decimal | None = None
    invoice_scope_commission_rate: Decimal | None = None
    rst_commission_rate: Decimal | None = None
    scope_source_row: int
    rst_source_reference: str | None = None
    mapping_method: str
    mapping_status: MappingStatus
    data_quality_status: DataQualityStatus
    admin_orders_available: bool = False
    canonical_order_count: int = 0
    admin_restaurant_name: str | None = None
    issue_codes: tuple[str, ...] = ()
    readiness: RestaurantReadiness


class RestaurantRegistryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    invoice_scope_profile: InvoiceScopeSchemaProfile
    rst_profile: RSTSchemaProfile
    scope_rows: int
    scope_rows_with_restaurant_id: int
    scope_rows_without_restaurant_id: int
    restaurants: tuple[RegisteredRestaurant, ...]
    issues: tuple[RegistryIssue, ...]
    mapping_cases: tuple[MappingReviewCase, ...] = ()

    def mapping_count(self, status: MappingStatus) -> int:
        return sum(item.mapping_status == status for item in self.restaurants)

    def issue_count(self, code: str) -> int:
        return sum(item.code == code for item in self.issues)

    @property
    def mapped_count(self) -> int:
        return sum(item.readiness.identity_ready for item in self.restaurants)

    @property
    def mapping_completion(self) -> float:
        return self.mapped_count / len(self.restaurants) if self.restaurants else 0.0

    @property
    def blocking_mapping_issues(self) -> int:
        return sum(not item.readiness.identity_ready for item in self.restaurants)

    @property
    def ready_for_settlement_mapping(self) -> bool:
        return self.blocking_mapping_issues == 0

    @property
    def identity_ready_restaurants(self) -> tuple[RegisteredRestaurant, ...]:
        """Future settlement evaluation population; no financial logic is applied."""
        return tuple(item for item in self.restaurants if item.readiness.identity_ready)

    @property
    def identity_blocked_restaurants(self) -> tuple[RegisteredRestaurant, ...]:
        return tuple(item for item in self.restaurants if not item.readiness.identity_ready)

    def no_id_row_counts(self) -> dict[NoIdClassification, int]:
        counts = {item: 0 for item in NoIdClassification}
        for case in self.mapping_cases:
            for row in case.scope_rows:
                if row.restaurant_id is not None:
                    continue
                if case.mapping_status in {
                    MappingStatus.CONFLICTING_SCOPE,
                    MappingStatus.DUPLICATE_SCOPE,
                }:
                    classification = NoIdClassification.CONFLICTING_OR_DUPLICATE
                elif (
                    case.mapping_method.startswith("EXACT_UNIQUE_NAME")
                    and case.identity_ready
                ):
                    classification = NoIdClassification.EXACT_NAME_MAPPED
                elif "AMBIGUOUS_RESTAURANT_MAPPING" in case.issue_codes:
                    classification = NoIdClassification.AMBIGUOUS
                elif "UNMATCHED_SCOPE_RESTAURANT" in case.issue_codes:
                    classification = NoIdClassification.UNMATCHED
                else:
                    classification = NoIdClassification.OTHER
                counts[classification] += 1
        return counts
