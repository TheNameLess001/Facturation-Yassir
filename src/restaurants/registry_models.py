from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MappingStatus(StrEnum):
    MATCHED_BY_ID = "MATCHED_BY_ID"
    MATCHED_BY_EXACT_NAME = "MATCHED_BY_EXACT_NAME"
    MATCHED_BY_ALIAS = "MATCHED_BY_ALIAS"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    DUPLICATE_SCOPE = "DUPLICATE_SCOPE"
    CONFLICTING_SCOPE = "CONFLICTING_SCOPE"


class DataQualityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class RegistryIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


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
    scope_source_row: int
    rst_source_reference: str | None = None
    mapping_method: str
    mapping_status: MappingStatus
    data_quality_status: DataQualityStatus
    admin_orders_available: bool = False
    canonical_order_count: int = 0
    issue_codes: tuple[str, ...] = ()


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

    def mapping_count(self, status: MappingStatus) -> int:
        return sum(item.mapping_status == status for item in self.restaurants)

    def issue_count(self, code: str) -> int:
        return sum(item.code == code for item in self.issues)
