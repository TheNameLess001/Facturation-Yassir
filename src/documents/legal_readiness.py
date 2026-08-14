from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from src.restaurants.registry_models import RegisteredRestaurant


class CashCoDocumentType(StrEnum):
    INVOICE = "INVOICE"
    NOTE_DE_DEBOURS = "NOTE_DE_DEBOURS"
    PARTNER_STATEMENT = "PARTNER_STATEMENT"


class DocumentLegalStatus(StrEnum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


class LegalFieldStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class DocumentPartnerNameSource(StrEnum):
    LEGAL_ENTITY = "LEGAL_ENTITY"
    RESTAURANT_NAME_FALLBACK = "RESTAURANT_NAME_FALLBACK"


class DocumentLegalRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: CashCoDocumentType
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    authority: str = "4_Generateur bulk.py · BUSINESS_OWNER_CONFIRMED"


class LegalFieldTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    value: str | None = None
    source: str | None = None
    required: bool
    status: LegalFieldStatus


class DocumentLegalReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    document_type: CashCoDocumentType
    document_partner_name: str | None = None
    document_partner_name_source: DocumentPartnerNameSource | None = None
    required_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    missing_required_fields: tuple[str, ...]
    optional_missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]
    status: DocumentLegalStatus
    field_traces: tuple[LegalFieldTrace, ...]


class DocumentLegalPolicy:
    """Approved legacy-template legal requirements; no source value is fabricated."""

    FIELD_LABELS: ClassVar[dict[str, str]] = {
        "document_partner_name": "Partner Name",
        "legal_entity": "Raison Sociale",
        "address": "Address",
        "ice": "ICE",
        "if_number": "IF",
        "rc": "RC",
        "rib": "RIB",
    }
    REQUIREMENTS: ClassVar[
        dict[CashCoDocumentType, DocumentLegalRequirements]
    ] = {
        CashCoDocumentType.INVOICE: DocumentLegalRequirements(
            document_type=CashCoDocumentType.INVOICE,
            required_fields=("document_partner_name", "address"),
            optional_fields=("legal_entity", "ice", "if_number", "rc", "rib"),
        ),
        CashCoDocumentType.NOTE_DE_DEBOURS: DocumentLegalRequirements(
            document_type=CashCoDocumentType.NOTE_DE_DEBOURS,
            required_fields=("document_partner_name", "address"),
            optional_fields=("legal_entity", "ice", "if_number", "rc", "rib"),
        ),
        CashCoDocumentType.PARTNER_STATEMENT: DocumentLegalRequirements(
            document_type=CashCoDocumentType.PARTNER_STATEMENT,
            required_fields=("document_partner_name",),
            optional_fields=(
                "legal_entity",
                "address",
                "ice",
                "if_number",
                "rc",
                "rib",
            ),
        ),
    }

    def requirements(
        self, document_type: CashCoDocumentType
    ) -> DocumentLegalRequirements:
        return self.REQUIREMENTS[document_type]

    def evaluate(
        self,
        restaurant: RegisteredRestaurant,
        document_type: CashCoDocumentType,
    ) -> DocumentLegalReadiness:
        requirements = self.requirements(document_type)
        legal_entity = self._text(restaurant.legal_entity)
        restaurant_name = self._text(restaurant.restaurant_name)
        partner_name = legal_entity or restaurant_name
        name_source = (
            DocumentPartnerNameSource.LEGAL_ENTITY
            if legal_entity
            else DocumentPartnerNameSource.RESTAURANT_NAME_FALLBACK
            if restaurant_name
            else None
        )
        values = {
            "document_partner_name": partner_name,
            "legal_entity": legal_entity,
            "address": self._text(restaurant.address),
            "ice": self.normalize_ice(restaurant.ice),
            "if_number": self._text(restaurant.if_number),
            "rc": self._text(restaurant.rc),
            "rib": self._text(restaurant.rib),
        }
        fields = (*requirements.required_fields, *requirements.optional_fields)
        traces: list[LegalFieldTrace] = []
        invalid_fields: list[str] = []
        for field in fields:
            raw_value = values[field]
            if field == "ice" and restaurant.ice and raw_value is None:
                status = LegalFieldStatus.INVALID
                invalid_fields.append(field)
            else:
                status = (
                    LegalFieldStatus.AVAILABLE
                    if raw_value is not None
                    else LegalFieldStatus.MISSING
                )
            source = self._source(restaurant, field, name_source)
            traces.append(
                LegalFieldTrace(
                    field=field,
                    value=self._safe_display_value(field, raw_value),
                    source=source,
                    required=field in requirements.required_fields,
                    status=status,
                )
            )
        by_field = {item.field: item for item in traces}
        missing_required = tuple(
            field
            for field in requirements.required_fields
            if by_field[field].status != LegalFieldStatus.AVAILABLE
        )
        optional_missing = tuple(
            field
            for field in requirements.optional_fields
            if by_field[field].status == LegalFieldStatus.MISSING
        )
        available = tuple(
            field
            for field in fields
            if by_field[field].status == LegalFieldStatus.AVAILABLE
        )
        warnings = bool(
            optional_missing
            or invalid_fields
            or name_source == DocumentPartnerNameSource.RESTAURANT_NAME_FALLBACK
        )
        if missing_required:
            status = DocumentLegalStatus.BLOCKED
        elif warnings:
            status = DocumentLegalStatus.READY_WITH_WARNINGS
        else:
            status = DocumentLegalStatus.READY
        return DocumentLegalReadiness(
            restaurant_id=restaurant.restaurant_id or "",
            document_type=document_type,
            document_partner_name=partner_name,
            document_partner_name_source=name_source,
            required_fields=requirements.required_fields,
            available_fields=available,
            missing_required_fields=missing_required,
            optional_missing_fields=optional_missing,
            invalid_fields=tuple(invalid_fields),
            status=status,
            field_traces=tuple(traces),
        )

    def evaluate_package(
        self, restaurant: RegisteredRestaurant
    ) -> tuple[DocumentLegalReadiness, ...]:
        return tuple(self.evaluate(restaurant, item) for item in CashCoDocumentType)

    @staticmethod
    def normalize_ice(value: object) -> str | None:
        text = DocumentLegalPolicy._text(value)
        if text is None:
            return None
        normalized = re.sub(r"[\s-]+", "", text)
        return normalized if normalized.isdigit() and len(normalized) == 15 else None

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _safe_display_value(field: str, value: str | None) -> str | None:
        if field != "rib" or value is None:
            return value
        compact = re.sub(r"\s+", "", value)
        return f"•••• {compact[-4:]}" if len(compact) >= 4 else "••••"

    @staticmethod
    def _source(
        restaurant: RegisteredRestaurant,
        field: str,
        name_source: DocumentPartnerNameSource | None,
    ) -> str | None:
        if field == "document_partner_name":
            source_field = (
                "legal_entity"
                if name_source == DocumentPartnerNameSource.LEGAL_ENTITY
                else "restaurant_name"
            )
            return restaurant.field_sources.get(source_field)
        return restaurant.field_sources.get(field)
