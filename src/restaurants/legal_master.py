from __future__ import annotations

import hashlib
import io
import json
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

import pandas as pd

from src.emails.packages import EMAIL_PATTERN, normalize_email, resolve_recipient
from src.google.interfaces import ReadOnlyDriveService
from src.ingestion.admin_earnings_normalizer import normalize_identifier
from src.restaurants.mapping_review import materially_different_restaurant_names
from src.restaurants.registry_models import (
    LegalFieldLineage,
    LegalMasterAuditEvent,
    LegalMasterRecordStatus,
    LegalMasterSyncStatus,
    LegalValueStatus,
    PartnerLegalIssue,
    PartnerLegalMasterProfile,
    PartnerLegalMasterSnapshot,
    PartnerLegalRecord,
    PaymentReadinessStatus,
    RegisteredRestaurant,
    RestaurantRegistryResult,
    RibValueStatus,
)
from src.restaurants.scope_registry import resolve_columns

GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
XLSX_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SOURCE_NAME = "PARTNER_LEGAL_MASTER"

PARTNER_LEGAL_ALIASES: dict[str, tuple[str, ...]] = {
    "restaurant_id": ("restaurant id", "id"),
    "restaurant_name": ("restaurant name", "restaurant"),
    "city": ("ville", "city"),
    "address": ("adresse", "address"),
    "email": ("email",),
    "finance_email": ("finance email", "email finance", "billing email"),
    "phone": ("phone", "telephone", "téléphone"),
    "raison_sociale": ("raison sociale", "legal entity", "company name"),
    "ice": ("ice",),
    "if_number": ("if", "identifiant fiscal", "tax id"),
    "rc": ("rc", "registre de commerce"),
    "rib": ("rib",),
    "bank": ("bank", "banque"),
    "finance_contact": ("finance contact", "contact finance"),
    "review_status": ("review status",),
    "comment": ("comment", "commentaire"),
    "legal_data_source": ("legal data source",),
}

SUPPORTED_REVIEW_STATUSES = frozenset(
    {"TO_REVIEW", "CONFIRMED", "NEEDS_PARTNER", "NEEDS_FINANCE", "INVALID", "DO_NOT_USE"}
)
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "restaurant_id",
    "restaurant_name",
    "raison_sociale",
    "ice",
    "if_number",
    "rc",
    "address",
    "city",
    "email",
    "finance_email",
    "phone",
    "finance_contact",
    "rib",
    "bank",
    "review_status",
    "comment",
)
CONFLICT_FIELDS: tuple[str, ...] = (
    "raison_sociale",
    "ice",
    "if_number",
    "rc",
    "rib",
    "address",
    "city",
    "finance_email",
    "bank",
)


class PartnerLegalMasterSource:
    """Read-only adapter for the human-maintained Partner Legal Master."""

    def __init__(self, drive: ReadOnlyDriveService) -> None:
        self.drive = drive

    def fetch(
        self,
        file_id: str,
        worksheet: str,
        *,
        column_map: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> PartnerLegalMasterSnapshot:
        metadata = self.drive.get_file_metadata(file_id)
        if metadata.mime_type != GOOGLE_SHEETS_MIME_TYPE:
            raise ValueError("Partner Legal Master must be a Google Sheet")
        content = self.drive.export_file(file_id, XLSX_EXPORT_MIME)
        workbook = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
        if worksheet not in workbook.sheet_names:
            raise ValueError(f"Partner Legal Master worksheet was not found: {worksheet}")
        frame = pd.read_excel(workbook, sheet_name=worksheet, dtype=object)
        return self.from_frame(
            frame,
            file_id=metadata.file_id,
            filename=metadata.name,
            mime_type=metadata.mime_type,
            worksheet_names=tuple(workbook.sheet_names),
            selected_worksheet=worksheet,
            modified_at=metadata.modified_time,
            capabilities=metadata.capabilities,
            column_map=column_map,
            now=now,
        )

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        *,
        file_id: str = "partner-legal-master",
        filename: str = "Partner Legal Master",
        mime_type: str = GOOGLE_SHEETS_MIME_TYPE,
        worksheet_names: tuple[str, ...] = ("PARTNERS",),
        selected_worksheet: str = "PARTNERS",
        modified_at: datetime | None = None,
        capabilities: dict[str, bool] | None = None,
        column_map: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> PartnerLegalMasterSnapshot:
        timestamp = now or datetime.now(UTC)
        frame = frame.copy()
        frame.columns = tuple(str(item).strip() for item in frame.columns)
        mapping = resolve_columns(frame.columns, PARTNER_LEGAL_ALIASES, column_map)
        if "restaurant_id" not in mapping or "restaurant_name" not in mapping:
            raise ValueError(
                "Partner Legal Master requires Restaurant ID and Restaurant Name columns"
            )
        records = cls._records(frame, mapping, source_version="PENDING")
        fingerprint = cls.fingerprint(records)
        source_version = (
            f"{(modified_at or timestamp).isoformat()}:{fingerprint}"
        )
        records = tuple(
            item.model_copy(update={"source_version": source_version}) for item in records
        )
        issues, duplicate_groups, conflict_groups = cls._source_issues(records)
        ids = {item.restaurant_id for item in records if item.restaurant_id}
        profile = PartnerLegalMasterProfile(
            file_id=file_id,
            filename=filename,
            mime_type=mime_type,
            worksheet_names=worksheet_names,
            selected_worksheet=selected_worksheet,
            modified_at=modified_at or timestamp,
            capabilities=capabilities or {},
            row_count=len(records),
            column_count=len(frame.columns),
            columns=tuple(str(item) for item in frame.columns),
            unique_restaurant_ids=len(ids),
            missing_ids=sum(item.restaurant_id is None for item in records),
            duplicate_id_groups=duplicate_groups,
            conflict_groups=conflict_groups,
        )
        events = [
            LegalMasterAuditEvent(
                event_type="LEGAL_MASTER_REFRESHED",
                occurred_at=timestamp,
                fingerprint=fingerprint,
                rows=len(records),
                conflicts=conflict_groups,
            )
        ]
        if conflict_groups:
            events.append(
                LegalMasterAuditEvent(
                    event_type="LEGAL_MASTER_CONFLICT_DETECTED",
                    occurred_at=timestamp,
                    fingerprint=fingerprint,
                    rows=len(records),
                    conflicts=conflict_groups,
                )
            )
        return PartnerLegalMasterSnapshot(
            status=LegalMasterSyncStatus.CONNECTED,
            profile=profile,
            records=records,
            issues=issues,
            fingerprint=fingerprint,
            source_version=source_version,
            synced_at=timestamp,
            last_successful_sync=timestamp,
            audit_events=tuple(events),
        )

    @classmethod
    def _records(
        cls,
        frame: pd.DataFrame,
        mapping: dict[str, str],
        *,
        source_version: str,
    ) -> tuple[PartnerLegalRecord, ...]:
        records: list[PartnerLegalRecord] = []
        for source_row, (_, row) in enumerate(frame.iterrows(), start=2):
            values = {
                field: cls._text(row[column]) if column in row else None
                for field, column in mapping.items()
            }
            restaurant_id = normalize_identifier(values.get("restaurant_id"))
            restaurant_name = cls._text(values.get("restaurant_name"))
            if not restaurant_id and not restaurant_name:
                continue
            review_status = cls._review_status(values.get("review_status"))
            ice = cls._text(values.get("ice"))
            rib = cls._text(values.get("rib"))
            finance_email = normalize_email(cls._text(values.get("finance_email")))
            records.append(
                PartnerLegalRecord(
                    restaurant_id=restaurant_id,
                    restaurant_name=restaurant_name,
                    raison_sociale=cls._text(values.get("raison_sociale")),
                    ice=ice,
                    if_number=cls._text(values.get("if_number")),
                    rc=cls._text(values.get("rc")),
                    address=cls._text(values.get("address")),
                    city=cls._text(values.get("city")),
                    email=normalize_email(cls._text(values.get("email"))),
                    finance_email=finance_email,
                    phone=cls._text(values.get("phone")),
                    finance_contact=cls._text(values.get("finance_contact")),
                    rib=rib,
                    bank=cls._text(values.get("bank")),
                    review_status=review_status,
                    comment=cls._text(values.get("comment")),
                    source_version=source_version,
                    source_row=source_row,
                    ice_status=cls.ice_status(ice),
                    rib_status=cls.rib_status(rib),
                    finance_email_status=cls.email_status(finance_email),
                )
            )
        return tuple(records)

    @staticmethod
    def fingerprint(records: tuple[PartnerLegalRecord, ...]) -> str:
        normalized = sorted(
            json.dumps(
                {field: getattr(item, field) for field in FINGERPRINT_FIELDS},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for item in records
        )
        return hashlib.sha256("\n".join(normalized).encode()).hexdigest()

    @classmethod
    def _source_issues(
        cls, records: tuple[PartnerLegalRecord, ...]
    ) -> tuple[tuple[PartnerLegalIssue, ...], int, int]:
        issues: list[PartnerLegalIssue] = []
        grouped: dict[str, list[PartnerLegalRecord]] = defaultdict(list)
        for record in records:
            if record.restaurant_id is None:
                issues.append(
                    PartnerLegalIssue(
                        code=LegalMasterRecordStatus.MISSING_ID.value,
                        restaurant_name=record.restaurant_name,
                        source_rows=(record.source_row,),
                        review_status=record.review_status,
                    )
                )
            else:
                grouped[record.restaurant_id].append(record)
            if record.ice_status == LegalValueStatus.INVALID:
                issues.append(cls._record_issue("INVALID_ICE", record))
            if record.rib_status == RibValueStatus.INVALID_FORMAT:
                issues.append(cls._record_issue("INVALID_RIB", record))
            if record.finance_email_status == LegalValueStatus.INVALID:
                issues.append(cls._record_issue("INVALID_FINANCE_EMAIL", record))
            if (
                record.review_status
                and record.review_status not in SUPPORTED_REVIEW_STATUSES
            ):
                issues.append(cls._record_issue("INVALID_REVIEW_STATUS", record))
        duplicate_groups = 0
        conflict_groups = 0
        for restaurant_id, rows in grouped.items():
            if len(rows) < 2:
                continue
            duplicate_groups += 1
            source_rows = tuple(item.source_row for item in rows)
            issues.append(
                PartnerLegalIssue(
                    code=LegalMasterRecordStatus.DUPLICATE_ID.value,
                    restaurant_id=restaurant_id,
                    restaurant_name=rows[0].restaurant_name,
                    source_rows=source_rows,
                )
            )
            conflict_fields = tuple(
                field
                for field in CONFLICT_FIELDS
                if len({getattr(item, field) for item in rows}) > 1
            )
            if conflict_fields:
                conflict_groups += 1
                issues.append(
                    PartnerLegalIssue(
                        code="LEGAL_SOURCE_CONFLICT",
                        restaurant_id=restaurant_id,
                        restaurant_name=rows[0].restaurant_name,
                        source_rows=source_rows,
                        fields=conflict_fields,
                    )
                )
        return tuple(issues), duplicate_groups, conflict_groups

    @staticmethod
    def _record_issue(code: str, record: PartnerLegalRecord) -> PartnerLegalIssue:
        return PartnerLegalIssue(
            code=code,
            restaurant_id=record.restaurant_id,
            restaurant_name=record.restaurant_name,
            source_rows=(record.source_row,),
            review_status=record.review_status,
        )

    @staticmethod
    def ice_status(value: str | None) -> LegalValueStatus:
        if value is None:
            return LegalValueStatus.MISSING
        compact = "".join(character for character in value if character not in " -")
        return (
            LegalValueStatus.VALID
            if compact.isdigit() and len(compact) == 15
            else LegalValueStatus.INVALID
        )

    @staticmethod
    def rib_status(value: str | None) -> RibValueStatus:
        if value is None:
            return RibValueStatus.MISSING
        compact = "".join(character for character in value if character not in " -")
        return (
            RibValueStatus.VALID_FORMAT
            if compact.isdigit() and len(compact) == 24
            else RibValueStatus.INVALID_FORMAT
        )

    @staticmethod
    def email_status(value: str | None) -> LegalValueStatus:
        if value is None:
            return LegalValueStatus.MISSING
        return (
            LegalValueStatus.VALID
            if EMAIL_PATTERN.fullmatch(value)
            else LegalValueStatus.INVALID
        )

    @staticmethod
    def mask_rib(value: str | None) -> str:
        if not value:
            return "MISSING"
        compact = "".join(character for character in value if character not in " -")
        return f"****************{compact[-4:]}"

    @staticmethod
    def _review_status(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper().replace(" ", "_")
        return normalized if normalized else None

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        text = str(value).strip()
        return text or None


class PartnerLegalRegistryEnricher:
    """Exact-ID enrichment. Legal values never change restaurant identity or commission."""

    def enrich(
        self,
        registry: RestaurantRegistryResult,
        snapshot: PartnerLegalMasterSnapshot,
        *,
        rst_ids: set[str],
    ) -> RestaurantRegistryResult:
        if snapshot.profile is None:
            return registry.model_copy(update={"partner_legal_master": snapshot})
        grouped: dict[str, list[PartnerLegalRecord]] = defaultdict(list)
        for record in snapshot.records:
            if record.restaurant_id:
                grouped[record.restaurant_id].append(record)
        source_issues = list(snapshot.issues)
        conflict_ids = {
            item.restaurant_id
            for item in source_issues
            if item.code == "LEGAL_SOURCE_CONFLICT" and item.restaurant_id
        }
        scope_ids = {
            item.restaurant_id for item in registry.restaurants if item.restaurant_id
        }
        name_mismatches = 0
        affected = 0
        enriched: list[RegisteredRestaurant] = []
        for restaurant in registry.restaurants:
            restaurant_id = restaurant.restaurant_id
            candidates = grouped.get(restaurant_id or "", [])
            record = candidates[0] if len(candidates) == 1 else None
            issue_codes = list(restaurant.issue_codes)
            if restaurant_id in conflict_ids:
                issue_codes.append("LEGAL_SOURCE_CONFLICT")
                record = None
            if record and restaurant_id not in rst_ids:
                source_issues.append(self._match_issue("ID_NOT_IN_RST", record))
                issue_codes.append("LEGAL_MASTER_ID_NOT_IN_RST")
                record = None
            if record and materially_different_restaurant_names(
                record.restaurant_name, restaurant.restaurant_name
            ):
                name_mismatches += 1
                source_issues.append(self._match_issue("NAME_MISMATCH", record))
                issue_codes.append("LEGAL_MASTER_NAME_MISMATCH")
                record = None
            if record and record.review_status == "DO_NOT_USE":
                issue_codes.append("LEGAL_MASTER_DO_NOT_USE")
                record = None
            updated = self._apply_record(restaurant, record, issue_codes)
            if updated != restaurant:
                affected += 1
            enriched.append(updated)
        legal_ids = set(grouped)
        for restaurant_id in sorted(legal_ids - rst_ids):
            rows = grouped[restaurant_id]
            if not any(
                item.code == "ID_NOT_IN_RST" and item.restaurant_id == restaurant_id
                for item in source_issues
            ):
                source_issues.append(self._match_issue("ID_NOT_IN_RST", rows[0]))
        profile = snapshot.profile.model_copy(
            update={
                "matched_invoice_scope": len(legal_ids & scope_ids),
                "matched_rst": len(legal_ids & rst_ids),
                "name_mismatches": name_mismatches,
            }
        )
        audit_events = tuple(
            item.model_copy(
                update={
                    "matched_ids": len(legal_ids & scope_ids),
                    "affected_readiness_count": affected,
                }
            )
            for item in snapshot.audit_events
        )
        enriched_snapshot = snapshot.model_copy(
            update={
                "profile": profile,
                "issues": tuple(source_issues),
                "audit_events": audit_events,
            }
        )
        return registry.model_copy(
            update={
                "restaurants": tuple(enriched),
                "partner_legal_master": enriched_snapshot,
            }
        )

    @staticmethod
    def _match_issue(code: str, record: PartnerLegalRecord) -> PartnerLegalIssue:
        return PartnerLegalIssue(
            code=code,
            restaurant_id=record.restaurant_id,
            restaurant_name=record.restaurant_name,
            source_rows=(record.source_row,),
            review_status=record.review_status,
        )

    def _apply_record(
        self,
        restaurant: RegisteredRestaurant,
        record: PartnerLegalRecord | None,
        issue_codes: list[str],
    ) -> RegisteredRestaurant:
        if record is None:
            return restaurant.model_copy(
                update={"issue_codes": tuple(dict.fromkeys(issue_codes))}
            )
        finance_email = (
            record.finance_email
            if record.finance_email_status == LegalValueStatus.VALID
            else None
        )
        if record.ice_status == LegalValueStatus.INVALID:
            issue_codes.append("INVALID_ICE")
        if record.rib_status == RibValueStatus.INVALID_FORMAT:
            issue_codes.append("INVALID_RIB")
        if record.finance_email_status == LegalValueStatus.INVALID:
            issue_codes.append("INVALID_FINANCE_EMAIL")
        payment_status = self._payment_status(record)
        lineage = dict(restaurant.field_lineage)
        field_sources = dict(restaurant.field_sources)
        values = {
            "legal_entity": record.raison_sociale,
            "ice": record.ice,
            "if_number": record.if_number,
            "rc": record.rc,
            "rib": record.rib,
            "bank": record.bank,
            "address": record.address or restaurant.address,
            "city": record.city or restaurant.city,
            "finance_email": finance_email,
            "phone": record.phone or restaurant.phone,
        }
        resolved_warning_fields = {
            "MISSING_LEGAL_ENTITY": values["legal_entity"],
            "MISSING_ICE": values["ice"],
            "MISSING_RIB": values["rib"],
            "MISSING_ADDRESS": values["address"],
            "MISSING_EMAIL": finance_email or restaurant.email,
        }
        issue_codes = [
            code
            for code in issue_codes
            if not resolved_warning_fields.get(code)
        ]
        source_fields = {
            "legal_entity": "Raison Sociale",
            "ice": "ICE",
            "if_number": "IF",
            "rc": "RC",
            "rib": "RIB",
            "bank": "Bank",
            "address": "Adresse",
            "city": "Ville",
            "finance_email": "Finance Email",
            "phone": "Phone",
        }
        for field in values:
            master_value = getattr(
                record,
                {"legal_entity": "raison_sociale", "if_number": "if_number"}.get(
                    field, field
                ),
                None,
            )
            if master_value is None or (field == "finance_email" and finance_email is None):
                continue
            lineage[field] = LegalFieldLineage(
                source=SOURCE_NAME,
                source_field=source_fields[field],
                source_row=record.source_row,
                source_version=record.source_version,
            )
            field_sources[field] = (
                f"{SOURCE_NAME}:{source_fields[field]}:row_{record.source_row}:"
                f"fp_{record.source_version.rsplit(':', 1)[-1][:12]}"
            )
        provisional = restaurant.model_copy(
            update={
                **values,
                "finance_contact": record.finance_contact,
                "legal_master_review_status": record.review_status,
                "field_sources": field_sources,
                "field_lineage": lineage,
                "payment_readiness_status": payment_status,
                "issue_codes": tuple(dict.fromkeys(issue_codes)),
            }
        )
        from src.documents.legal_readiness import (  # avoids a model import cycle
            DocumentLegalPolicy,
            DocumentLegalStatus,
        )

        legal = DocumentLegalPolicy().evaluate_package(provisional)
        document_ready = all(item.status != DocumentLegalStatus.BLOCKED for item in legal)
        email_ready = resolve_recipient(provisional).status.value == "EMAIL_VALID"
        return provisional.model_copy(
            update={
                "readiness": provisional.readiness.model_copy(
                    update={
                        "document_ready": document_ready,
                        "email_ready": email_ready,
                        "payment_ready": (
                            payment_status == PaymentReadinessStatus.PAYMENT_READY
                        ),
                    }
                )
            }
        )

    @staticmethod
    def _payment_status(record: PartnerLegalRecord) -> PaymentReadinessStatus:
        if record.rib_status == RibValueStatus.MISSING:
            return PaymentReadinessStatus.RIB_MISSING
        if record.rib_status == RibValueStatus.INVALID_FORMAT:
            return PaymentReadinessStatus.RIB_INVALID
        if record.review_status in {"NEEDS_FINANCE", "INVALID", "DO_NOT_USE"}:
            return PaymentReadinessStatus.PAYMENT_DATA_REVIEW
        return PaymentReadinessStatus.PAYMENT_READY


class PartnerLegalMasterCache:
    """Process-local five-minute cache retaining the last good snapshot on failure."""

    _lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._entries: dict[str, tuple[PartnerLegalMasterSnapshot, float]] = {}

    def load(
        self,
        key: str,
        loader: Callable[[], PartnerLegalMasterSnapshot],
        *,
        ttl_seconds: int = 300,
        force: bool = False,
        now: datetime | None = None,
    ) -> PartnerLegalMasterSnapshot:
        timestamp = now or datetime.now(UTC)
        with self._lock:
            previous = self._entries.get(key)
            if previous and not force and self._monotonic() < previous[1]:
                return previous[0]
            try:
                current = loader()
            # The loader is an integration boundary; every provider/parser failure must
            # preserve the last known-good registry instead of clearing partner data.
            except Exception as exc:  # noqa: BLE001
                reason = type(exc).__name__
                if previous:
                    stale = previous[0].model_copy(
                        update={
                            "status": LegalMasterSyncStatus.STALE_SOURCE,
                            "synced_at": timestamp,
                            "stale_reason": reason,
                            "audit_events": (
                                *previous[0].audit_events,
                                LegalMasterAuditEvent(
                                    event_type="LEGAL_MASTER_REFRESH_FAILED",
                                    occurred_at=timestamp,
                                    fingerprint=previous[0].fingerprint,
                                    rows=(
                                        previous[0].profile.row_count
                                        if previous[0].profile
                                        else 0
                                    ),
                                    conflicts=(
                                        previous[0].profile.conflict_groups
                                        if previous[0].profile
                                        else 0
                                    ),
                                ),
                            ),
                        }
                    )
                    self._entries[key] = (stale, self._monotonic() + ttl_seconds)
                    return stale
                return PartnerLegalMasterSnapshot(
                    status=LegalMasterSyncStatus.FAILED,
                    synced_at=timestamp,
                    stale_reason=reason,
                    audit_events=(
                        LegalMasterAuditEvent(
                            event_type="LEGAL_MASTER_REFRESH_FAILED",
                            occurred_at=timestamp,
                        ),
                    ),
                )
            if previous and previous[0].fingerprint != current.fingerprint:
                current = current.model_copy(
                    update={
                        "audit_events": (
                            *current.audit_events,
                            LegalMasterAuditEvent(
                                event_type="LEGAL_MASTER_VERSION_CHANGED",
                                occurred_at=timestamp,
                                fingerprint=current.fingerprint,
                                rows=current.profile.row_count if current.profile else 0,
                                conflicts=(
                                    current.profile.conflict_groups
                                    if current.profile
                                    else 0
                                ),
                            ),
                        )
                    }
                )
            self._entries[key] = (current, self._monotonic() + ttl_seconds)
            return current

    def expire(self, key: str | None = None) -> None:
        with self._lock:
            keys = (key,) if key else tuple(self._entries)
            for item in keys:
                if item in self._entries:
                    snapshot, _ = self._entries[item]
                    self._entries[item] = (snapshot, 0.0)
