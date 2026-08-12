from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.restaurants.registry_models import (
    ConflictInterpretation,
    CorrectionConfidence,
    RestaurantCandidate,
    ScopeConflictReason,
    ScopeSourceRow,
    SuggestionStrength,
)


class CandidateRankingService:
    """Advisory ranking for human review; it never returns a mapping decision."""

    def __init__(self, *, maximum_candidates: int = 5) -> None:
        self.maximum_candidates = maximum_candidates

    def rank(
        self,
        scope: ScopeSourceRow,
        rst_records: tuple[dict[str, object], ...],
        order_counts: dict[str, int],
        order_names: dict[str, str] | None = None,
    ) -> tuple[RestaurantCandidate, ...]:
        ranked: list[RestaurantCandidate] = []
        scope_name = _normalized(scope.restaurant_name)
        scope_tokens = _tokens(scope_name)
        scope_city = _normalized(scope.city)
        scope_area = _normalized(scope.area)
        scope_phone = _phone(scope.phone)
        scope_email = _normalized(scope.email)
        for record in rst_records:
            restaurant_id = record.get("restaurant_id")
            if not restaurant_id:
                continue
            candidate_name = _normalized(record.get("restaurant_name"))
            name_similarity = SequenceMatcher(None, scope_name, candidate_name).ratio()
            candidate_tokens = _tokens(candidate_name)
            token_overlap = _jaccard(scope_tokens, candidate_tokens)
            same_city = bool(
                scope_city
                and scope_city
                in {
                    _normalized(record.get("city")),
                    _normalized(record.get("area")),
                }
            )
            same_area = bool(
                scope_area and scope_area == _normalized(record.get("area"))
            )
            same_phone = bool(
                scope_phone and scope_phone == _phone(record.get("phone"))
            )
            same_email = bool(
                scope_email and scope_email == _normalized(record.get("email"))
            )
            chain_tokens = _tokens(_normalized(record.get("chain")))
            chain_signal = bool(scope_tokens and chain_tokens & scope_tokens)
            score = min(
                1.0,
                name_similarity * 0.65
                + token_overlap * 0.15
                + (0.12 if same_city else 0.0)
                + (0.08 if chain_signal else 0.0)
                + (0.06 if same_area else 0.0)
                + (0.12 if same_phone else 0.0)
                + (0.12 if same_email else 0.0),
            )
            indicators = [f"NAME {name_similarity:.0%}"]
            if token_overlap:
                indicators.append(f"TOKENS {token_overlap:.0%}")
            if same_city:
                indicators.append("SAME CITY")
            if chain_signal:
                indicators.append("CHAIN/BRAND TOKEN")
            if same_area:
                indicators.append("SAME AREA")
            if same_phone:
                indicators.append("SAME PHONE")
            if same_email:
                indicators.append("SAME EMAIL")
            canonical_orders = order_counts.get(str(restaurant_id), 0)
            indicators.append(
                f"ADMIN ORDERS {canonical_orders:,}"
                if canonical_orders
                else "NO ADMIN ORDERS"
            )
            ranked.append(
                RestaurantCandidate(
                    restaurant_id=str(restaurant_id),
                    restaurant_name=_optional_text(record.get("restaurant_name")),
                    city=_optional_text(record.get("city")),
                    area=_optional_text(record.get("area")),
                    chain=_optional_text(record.get("chain")),
                    address=_optional_text(record.get("address")),
                    store_type=_optional_text(record.get("store_type")),
                    status=_optional_text(record.get("status")),
                    commission_rate=record.get("commission_rate"),
                    email=_optional_text(record.get("email")),
                    phone=_optional_text(record.get("phone")),
                    admin_restaurant_name=(order_names or {}).get(
                        str(restaurant_id)
                    ),
                    canonical_order_count=canonical_orders,
                    name_similarity=name_similarity,
                    same_city=same_city,
                    chain_signal=chain_signal,
                    token_overlap=token_overlap,
                    advisory_score=score,
                    confidence=confidence_for_score(score),
                    similarity_indicators=tuple(indicators),
                )
            )
        return tuple(
            sorted(
                ranked,
                key=lambda item: (
                    -item.advisory_score,
                    -item.canonical_order_count,
                    (item.restaurant_name or "").casefold(),
                    item.restaurant_id,
                ),
            )[: self.maximum_candidates]
        )

    @staticmethod
    def classify(
        candidates: tuple[RestaurantCandidate, ...],
    ) -> SuggestionStrength:
        if not candidates or candidates[0].advisory_score < 0.5:
            return SuggestionStrength.NO_USEFUL_CANDIDATE
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        margin = top.advisory_score - second.advisory_score if second else 1.0
        if top.advisory_score >= 0.82 and margin >= 0.12:
            return SuggestionStrength.STRONG_SINGLE_CANDIDATE
        plausible = sum(item.advisory_score >= 0.68 for item in candidates)
        if plausible >= 2:
            return SuggestionStrength.MULTIPLE_PLAUSIBLE_CANDIDATES
        return SuggestionStrength.WEAK_SUGGESTION

    @classmethod
    def correction_confidence(
        cls,
        candidates: tuple[RestaurantCandidate, ...],
    ) -> CorrectionConfidence:
        strength = cls.classify(candidates)
        return {
            SuggestionStrength.STRONG_SINGLE_CANDIDATE: CorrectionConfidence.HIGH_CONFIDENCE,
            SuggestionStrength.MULTIPLE_PLAUSIBLE_CANDIDATES: CorrectionConfidence.MEDIUM_CONFIDENCE,
            SuggestionStrength.WEAK_SUGGESTION: CorrectionConfidence.LOW_CONFIDENCE,
            SuggestionStrength.NO_USEFUL_CANDIDATE: CorrectionConfidence.NO_CANDIDATE,
            SuggestionStrength.NOT_REQUIRED: CorrectionConfidence.NOT_REQUIRED,
        }[strength]


def materially_different_restaurant_names(
    scope_name: object,
    rst_name: object,
) -> bool:
    """Flag only an obvious ID/name contradiction, never a spelling variation."""
    scope = _normalized(scope_name)
    rst = _normalized(rst_name)
    if not scope or not rst:
        return False
    similarity = SequenceMatcher(None, scope, rst).ratio()
    shared_tokens = _tokens(scope) & _tokens(rst)
    return similarity < 0.5 and not shared_tokens


def conflicting_scope_fields(rows: tuple[ScopeSourceRow, ...]) -> tuple[str, ...]:
    if len(rows) < 2:
        return ()
    fields = (
        "restaurant_name",
        "restaurant_id",
        "city",
        "commission_rate",
        "comment",
    )
    conflicts = [
        field
        for field in fields
        if len({repr(getattr(row, field)) for row in rows}) > 1
    ]
    extra_keys = sorted({key for row in rows for key in row.extra_fields})
    conflicts.extend(
        f"extra:{key}"
        for key in extra_keys
        if len({repr(row.extra_fields.get(key)) for row in rows}) > 1
    )
    return tuple(conflicts)


def classify_scope_conflict(
    rows: tuple[ScopeSourceRow, ...],
    fields: tuple[str, ...],
) -> tuple[ScopeConflictReason | None, ConflictInterpretation | None]:
    if len(rows) < 2 or not fields:
        return None, None
    material = set(fields) & {"restaurant_name", "city", "commission_rate"}
    if len(material) > 1:
        reason = ScopeConflictReason.MULTI_FIELD_CONFLICT
    elif material == {"restaurant_name"}:
        reason = ScopeConflictReason.NAME_CONFLICT
    elif material == {"city"}:
        reason = ScopeConflictReason.CITY_CONFLICT
    elif material == {"commission_rate"}:
        reason = ScopeConflictReason.COMMISSION_CONFLICT
    else:
        reason = ScopeConflictReason.OTHER

    names = [_normalized(row.restaurant_name) for row in rows]
    cities = {_normalized(row.city) for row in rows if _normalized(row.city)}
    name_similarity = min(
        (
            SequenceMatcher(None, left, right).ratio()
            for index, left in enumerate(names)
            for right in names[index + 1 :]
            if left and right
        ),
        default=1.0,
    )
    if len(set(names)) == 1 and len(cities) <= 1:
        interpretation = ConflictInterpretation.SAME_STORE_DUPLICATED
    elif name_similarity < 0.5 and len(cities) > 1:
        interpretation = ConflictInterpretation.DIFFERENT_STORES_SHARING_ID
    elif "restaurant_name" in fields and name_similarity >= 0.7:
        interpretation = ConflictInterpretation.OLD_NEW_RESTAURANT_NAMING
    elif reason in {
        ScopeConflictReason.CITY_CONFLICT,
        ScopeConflictReason.COMMISSION_CONFLICT,
    }:
        interpretation = ConflictInterpretation.DATA_ENTRY_ERROR
    else:
        interpretation = ConflictInterpretation.UNCERTAIN
    return reason, interpretation


def confidence_for_score(score: float) -> CorrectionConfidence:
    if score >= 0.82:
        return CorrectionConfidence.HIGH_CONFIDENCE
    if score >= 0.68:
        return CorrectionConfidence.MEDIUM_CONFIDENCE
    if score >= 0.5:
        return CorrectionConfidence.LOW_CONFIDENCE
    return CorrectionConfidence.NO_CANDIDATE


def _normalized(value: object) -> str:
    text = "" if value is None else str(value).casefold().strip()
    return re.sub(r"[^\w]+", " ", text).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in value.split() if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _phone(value: object) -> str:
    return re.sub(r"\D+", "", "" if value is None else str(value))
