from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime

import pandas as pd

from src.ingestion.admin_earnings_normalizer import (
    normalize_decimal,
    normalize_identifier,
)
from src.restaurants.mapping_review import (
    CandidateRankingService,
    conflicting_scope_fields,
)
from src.restaurants.registry_models import (
    DataQualityStatus,
    InvoiceScopeSchemaProfile,
    MappingReviewCase,
    MappingStatus,
    RegisteredRestaurant,
    RegistryIssue,
    RegistryIssueSeverity,
    RestaurantReadiness,
    RestaurantRegistryResult,
    RSTSchemaProfile,
    ScopeSourceRow,
    SuggestionStrength,
)

INVOICE_SCOPE_ALIASES: dict[str, tuple[str, ...]] = {
    "restaurant_id": ("restaurant id", "id"),
    # ``Column 1`` was confirmed from the real CASH-CO worksheet on 2026-08-12.
    "restaurant_name": ("column 1", "restaurant name", "restaurant", "store name"),
    "city": ("city", "ville"),
    "commission_rate": ("commission", "comission", "commission %"),
    "comment": ("comment", "comments", "commentaire"),
    "in_scope": ("to invoice", "billing enabled", "invoice enabled", "active"),
}

RST_ALIASES: dict[str, tuple[str, ...]] = {
    "restaurant_id": ("restaurant id", "id"),
    "restaurant_name": ("restaurant name", "restaurant", "store name"),
    "chain": ("chain", "parent"),
    "legal_entity": ("legal entity", "raison sociale", "company name"),
    "ice": ("ice",),
    "if_number": ("if", "identifiant fiscal", "tax id"),
    "rc": ("rc", "registre de commerce"),
    "rib": ("rib",),
    "bank": ("bank", "banque"),
    "address": ("address", "adresse"),
    "email": ("email",),
    "finance_email": ("finance email", "billing email", "email finance"),
    "phone": ("phone", "telephone", "téléphone"),
    "city": ("main city", "city", "ville"),
    "area": ("sub city", "area", "zone"),
    "account_manager": ("account manager", "am"),
    "commission_rate": ("commission %", "commission", "comission"),
    "store_type": ("store type",),
    "restaurant_status": ("restaurant status",),
    "status": ("status",),
}

FALSE_SCOPE_VALUES = frozenset(
    {"0", "false", "no", "non", "inactive", "disabled", "out", "out of scope"}
)


def normalize_restaurant_name(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = re.sub(r"[\u2018\u2019`´]", "'", folded)
    folded = re.sub(r"[^\w\s'-]+", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip(" -")
    return folded or None


def resolve_columns(
    columns: list[object] | pd.Index,
    aliases: dict[str, tuple[str, ...]],
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    normalized = defaultdict(list)
    for column in columns:
        normalized[_normalize_header(column)].append(str(column))
    mapping: dict[str, str] = {}
    for field, exact_column in (overrides or {}).items():
        if exact_column not in columns:
            raise ValueError(f"Configured source column was not found: {exact_column}")
        mapping[field] = exact_column
    for field, candidates in aliases.items():
        if field in mapping:
            continue
        matches: list[str] = []
        for candidate in candidates:
            matches.extend(normalized.get(_normalize_header(candidate), ()))
        unique = tuple(dict.fromkeys(matches))
        if len(unique) > 1:
            raise ValueError(f"Ambiguous source columns for {field}: {', '.join(unique)}")
        if unique:
            mapping[field] = unique[0]
    return mapping


class RestaurantRegistryBuilder:
    """Build an Invoice-Scope-led registry without fuzzy matching or persistence."""

    def build(
        self,
        invoice_scope: pd.DataFrame,
        rst: pd.DataFrame,
        *,
        invoice_scope_profile: InvoiceScopeSchemaProfile,
        rst_profile: RSTSchemaProfile,
        invoice_scope_column_map: dict[str, str] | None = None,
        rst_column_map: dict[str, str] | None = None,
        alias_map: dict[str, str] | None = None,
        canonical_order_counts: dict[str, int] | None = None,
    ) -> RestaurantRegistryResult:
        scope_mapping = resolve_columns(
            invoice_scope.columns,
            INVOICE_SCOPE_ALIASES,
            invoice_scope_column_map,
        )
        rst_mapping = resolve_columns(rst.columns, RST_ALIASES, rst_column_map)
        if "restaurant_name" not in scope_mapping:
            raise ValueError("Invoice Scope has no configured Restaurant Name column.")
        rst_records = self._rst_records(rst, rst_mapping)
        by_id, by_name = self._rst_indexes(rst_records)
        controlled_aliases = {
            normalize_restaurant_name(name): normalize_identifier(restaurant_id)
            for name, restaurant_id in (alias_map or {}).items()
            if normalize_restaurant_name(name) and normalize_identifier(restaurant_id)
        }
        raw_scope_records = self._scope_records(invoice_scope, scope_mapping)
        active_scope_records = tuple(
            item for item in raw_scope_records if item["in_scope"]
        )
        groups = self._scope_groups(active_scope_records)
        issues: list[RegistryIssue] = []
        restaurants: list[RegisteredRestaurant] = []
        mapping_cases: list[MappingReviewCase] = []
        order_counts = canonical_order_counts or {}
        ranker = CandidateRankingService()
        for case_key, records in groups.items():
            source = records[0]
            scope_rows = tuple(self._scope_row_model(record) for record in records)
            restaurant_issues: list[RegistryIssue] = []
            duplicate = len(records) > 1 and self._scope_rows_identical(records)
            conflicting = len(records) > 1 and not duplicate
            if conflicting:
                status = MappingStatus.CONFLICTING_SCOPE
                identity_status = status
                mapped = None
                method = "CONFLICTING_SCOPE"
                restaurant_issues.append(
                    self._issue(
                        "CONFLICTING_SCOPE_ROW",
                        RegistryIssueSeverity.BLOCKING,
                        "The same scoped Restaurant ID has conflicting scope values.",
                        source,
                    )
                )
            else:
                mapped, status, method = self._match(
                    source, by_id, by_name, controlled_aliases
                )
                identity_status = status
                if duplicate:
                    status = MappingStatus.DUPLICATE_SCOPE
                    method = f"{method};DUPLICATE_SCOPE"
                    restaurant_issues.append(
                        self._issue(
                            "DUPLICATE_SCOPE_ROW",
                            RegistryIssueSeverity.WARNING,
                            "An identical Invoice Scope restaurant row occurs more than once.",
                            source,
                        )
                    )
            restaurant_issues.extend(
                self._mapping_issues(source, mapped, identity_status)
            )
            issues.extend(restaurant_issues)
            result = self._registered(
                source,
                mapped,
                status,
                identity_status,
                method,
                restaurant_issues,
                order_counts,
            )
            restaurants.append(result)
            needs_review = (
                not result.readiness.identity_ready
                or duplicate
                or conflicting
            )
            candidates = (
                ranker.rank(scope_rows[0], rst_records, order_counts)
                if needs_review
                else ()
            )
            mapping_cases.append(
                MappingReviewCase(
                    case_key=case_key,
                    mapping_status=status,
                    mapping_method=method,
                    identity_ready=result.readiness.identity_ready,
                    scope_rows=scope_rows,
                    candidates=candidates,
                    conflict_fields=conflicting_scope_fields(scope_rows),
                    issue_codes=tuple(issue.code for issue in restaurant_issues),
                    suggestion_strength=(
                        ranker.classify(candidates)
                        if needs_review
                        else SuggestionStrength.NOT_REQUIRED
                    ),
                )
            )
        with_id = sum(item["restaurant_id"] is not None for item in active_scope_records)
        return RestaurantRegistryResult(
            generated_at=datetime.now(UTC),
            invoice_scope_profile=invoice_scope_profile,
            rst_profile=rst_profile,
            scope_rows=len(active_scope_records),
            scope_rows_with_restaurant_id=with_id,
            scope_rows_without_restaurant_id=len(active_scope_records) - with_id,
            restaurants=tuple(restaurants),
            issues=tuple(issues),
            mapping_cases=tuple(mapping_cases),
        )

    @staticmethod
    def _scope_records(
        frame: pd.DataFrame, mapping: dict[str, str]
    ) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for offset, (_, row) in enumerate(frame.iterrows(), start=2):
            name = _text(_value(row, mapping, "restaurant_name"))
            restaurant_id = normalize_identifier(_value(row, mapping, "restaurant_id"))
            if not name and not restaurant_id:
                continue
            explicit = _text(_value(row, mapping, "in_scope"))
            in_scope = explicit is None or explicit.casefold() not in FALSE_SCOPE_VALUES
            commission = None
            try:
                commission = normalize_decimal(_value(row, mapping, "commission_rate"))
            except (TypeError, ValueError):
                pass
            records.append(
                {
                    "source_row": offset,
                    "restaurant_id": restaurant_id,
                    "restaurant_name": name,
                    "normalized_name": normalize_restaurant_name(name),
                    "city": _text(_value(row, mapping, "city")),
                    "commission_rate": commission,
                    "comment": _text(_value(row, mapping, "comment")),
                    "extra_fields": {
                        str(column): _text(value)
                        for column, value in row.items()
                        if str(column) not in set(mapping.values())
                        and _text(value) is not None
                    },
                    "in_scope": in_scope,
                }
            )
        return tuple(records)

    @staticmethod
    def _scope_groups(
        records: tuple[dict[str, object], ...]
    ) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            restaurant_id = record["restaurant_id"]
            name = record["normalized_name"]
            key = f"id:{restaurant_id}" if restaurant_id else f"name:{name}"
            if key == "name:None":
                key = f"row:{record['source_row']}"
            grouped[key].append(record)
        return grouped

    @staticmethod
    def _scope_rows_identical(records: list[dict[str, object]]) -> bool:
        signatures = {
            tuple(
                (key, repr(value))
                for key, value in sorted(record.items())
                if key != "source_row"
            )
            for record in records
        }
        return len(signatures) == 1

    @staticmethod
    def _rst_records(
        frame: pd.DataFrame, mapping: dict[str, str]
    ) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for offset, (_, row) in enumerate(frame.iterrows(), start=2):
            restaurant_id = normalize_identifier(_value(row, mapping, "restaurant_id"))
            name = _text(_value(row, mapping, "restaurant_name"))
            if not restaurant_id and not name:
                continue
            commission = None
            try:
                commission = normalize_decimal(_value(row, mapping, "commission_rate"))
            except (TypeError, ValueError):
                pass
            records.append(
                {
                    "source_row": offset,
                    "restaurant_id": restaurant_id,
                    "restaurant_name": name,
                    "normalized_name": normalize_restaurant_name(name),
                    "chain": _text(_value(row, mapping, "chain")),
                    "legal_entity": _text(_value(row, mapping, "legal_entity")),
                    "ice": normalize_identifier(_value(row, mapping, "ice")),
                    "if_number": normalize_identifier(_value(row, mapping, "if_number")),
                    "rc": normalize_identifier(_value(row, mapping, "rc")),
                    "rib": normalize_identifier(_value(row, mapping, "rib")),
                    "bank": _text(_value(row, mapping, "bank")),
                    "address": _text(_value(row, mapping, "address")),
                    "email": _text(_value(row, mapping, "email")),
                    "finance_email": _text(_value(row, mapping, "finance_email")),
                    "phone": _text(_value(row, mapping, "phone")),
                    "city": _text(_value(row, mapping, "city")),
                    "area": _text(_value(row, mapping, "area")),
                    "account_manager": _text(_value(row, mapping, "account_manager")),
                    "commission_rate": commission,
                    "store_type": _text(_value(row, mapping, "store_type")),
                    "status": _text(_value(row, mapping, "restaurant_status"))
                    or _text(_value(row, mapping, "status")),
                }
            )
        return tuple(records)

    @staticmethod
    def _rst_indexes(records):
        by_id: dict[str, list[dict[str, object]]] = defaultdict(list)
        by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            if record["restaurant_id"]:
                by_id[str(record["restaurant_id"])].append(record)
            if record["normalized_name"]:
                by_name[str(record["normalized_name"])].append(record)
        return by_id, by_name

    @classmethod
    def _match(cls, source, by_id, by_name, alias_map):
        restaurant_id = source["restaurant_id"]
        if restaurant_id:
            matches = cls._unique_candidates(by_id.get(restaurant_id, ()))
            if len(matches) == 1:
                return matches[0], MappingStatus.MATCHED_BY_ID, "EXACT_RESTAURANT_ID"
            if len(matches) > 1:
                return None, MappingStatus.AMBIGUOUS, "AMBIGUOUS_RESTAURANT_ID"
            return None, MappingStatus.UNMATCHED, "RESTAURANT_ID_NOT_FOUND"
        normalized_name = source["normalized_name"]
        alias_id = alias_map.get(normalized_name)
        if alias_id:
            matches = cls._unique_candidates(by_id.get(alias_id, ()))
            if len(matches) == 1:
                return matches[0], MappingStatus.MATCHED_BY_ALIAS, "CONTROLLED_ALIAS"
            return None, MappingStatus.AMBIGUOUS, "INVALID_CONTROLLED_ALIAS"
        candidates = cls._unique_candidates(by_name.get(normalized_name, ()))
        if len(candidates) == 1:
            return candidates[0], MappingStatus.MATCHED_BY_EXACT_NAME, "EXACT_UNIQUE_NAME"
        if len(candidates) > 1:
            return None, MappingStatus.AMBIGUOUS, "AMBIGUOUS_EXACT_NAME"
        return None, MappingStatus.UNMATCHED, "EXACT_NAME_NOT_FOUND"

    @staticmethod
    def _unique_candidates(records) -> tuple[dict[str, object], ...]:
        unique: dict[tuple[tuple[str, str], ...], dict[str, object]] = {}
        for record in records:
            signature = tuple(
                (key, repr(value))
                for key, value in sorted(record.items())
                if key != "source_row"
            )
            unique.setdefault(signature, record)
        return tuple(unique.values())

    @classmethod
    def _mapping_issues(cls, source, mapped, status) -> list[RegistryIssue]:
        issues: list[RegistryIssue] = []
        if status == MappingStatus.UNMATCHED:
            issues.append(cls._issue("UNMATCHED_SCOPE_RESTAURANT", RegistryIssueSeverity.BLOCKING, "Invoice Scope restaurant was not found in RST.", source))
        elif status == MappingStatus.AMBIGUOUS:
            issues.append(cls._issue("AMBIGUOUS_RESTAURANT_MAPPING", RegistryIssueSeverity.BLOCKING, "Invoice Scope restaurant matches multiple RST candidates.", source))
        if mapped is None:
            return issues
        checks = (
            ("MISSING_EMAIL", "email", "No restaurant email is available."),
            ("MISSING_RIB", "rib", "No RIB is available for future payment workflows."),
            ("MISSING_ICE", "ice", "No ICE is available for future documents."),
            ("MISSING_LEGAL_ENTITY", "legal_entity", "No legal entity is available for future documents."),
            ("MISSING_ADDRESS", "address", "No address is available for future documents."),
        )
        for code, field, message in checks:
            if mapped.get(field) is None:
                issues.append(cls._issue(code, RegistryIssueSeverity.WARNING, message, source))
        if mapped.get("commission_rate") is None and source.get("commission_rate") is None:
            issues.append(
                cls._issue(
                    "MISSING_COMMISSION",
                    RegistryIssueSeverity.WARNING,
                    "No commission is available in Invoice Scope or RST.",
                    source,
                )
            )
        return issues

    @staticmethod
    def _registered(
        source,
        mapped,
        status,
        identity_status,
        method,
        issues,
        order_counts,
    ):
        chosen = mapped or {}
        restaurant_id = chosen.get("restaurant_id") or source["restaurant_id"]
        issue_codes = tuple(issue.code for issue in issues)
        quality = DataQualityStatus.BLOCKING if any(issue.severity == RegistryIssueSeverity.BLOCKING for issue in issues) else DataQualityStatus.WARNING if issues else DataQualityStatus.HEALTHY
        count = order_counts.get(str(restaurant_id), 0) if restaurant_id else 0
        identity_ready = bool(
            mapped is not None
            and identity_status
            in {
                MappingStatus.MATCHED_BY_ID,
                MappingStatus.MATCHED_BY_EXACT_NAME,
                MappingStatus.MATCHED_BY_ALIAS,
            }
        )
        return RegisteredRestaurant(
            restaurant_id=restaurant_id,
            restaurant_name=chosen.get("restaurant_name") or source["restaurant_name"],
            chain=chosen.get("chain"),
            is_chain=bool(chosen.get("chain")),
            legal_entity=chosen.get("legal_entity"),
            ice=chosen.get("ice"),
            if_number=chosen.get("if_number"),
            rc=chosen.get("rc"),
            rib=chosen.get("rib"),
            bank=chosen.get("bank"),
            address=chosen.get("address"),
            email=chosen.get("email"),
            finance_email=chosen.get("finance_email"),
            phone=chosen.get("phone"),
            city=chosen.get("city") or source["city"],
            area=chosen.get("area"),
            account_manager=chosen.get("account_manager"),
            commission_rate=(
                chosen.get("commission_rate")
                if chosen.get("commission_rate") is not None
                else source["commission_rate"]
            ),
            scope_source_row=int(source["source_row"]),
            rst_source_reference=f"RST row {chosen['source_row']}" if chosen else None,
            mapping_method=method,
            mapping_status=status,
            data_quality_status=quality,
            admin_orders_available=count > 0,
            canonical_order_count=count,
            issue_codes=issue_codes,
            readiness=RestaurantReadiness(
                identity_ready=identity_ready,
                orders_available=count > 0,
                settlement_ready=None,
                document_ready=bool(
                    chosen.get("legal_entity")
                    and chosen.get("ice")
                    and chosen.get("address")
                ),
                email_ready=bool(chosen.get("email") or chosen.get("finance_email")),
                payment_ready=bool(chosen.get("rib")),
            ),
        )

    @staticmethod
    def _scope_row_model(source) -> ScopeSourceRow:
        return ScopeSourceRow(
            source_row=int(source["source_row"]),
            restaurant_name=source["restaurant_name"],
            restaurant_id=source["restaurant_id"],
            city=source["city"],
            commission_rate=source["commission_rate"],
            comment=source["comment"],
            extra_fields=source["extra_fields"],
        )

    @staticmethod
    def _issue(code, severity, message, source):
        return RegistryIssue(
            code=code,
            severity=severity,
            message=message,
            scope_source_row=int(source["source_row"]),
            restaurant_id=source["restaurant_id"],
            restaurant_name=source["restaurant_name"],
        )


def _normalize_header(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def _value(row: pd.Series, mapping: dict[str, str], field: str) -> object:
    column = mapping.get(field)
    return row[column] if column else None
