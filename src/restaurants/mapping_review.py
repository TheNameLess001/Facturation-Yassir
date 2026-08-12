from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.restaurants.registry_models import (
    RestaurantCandidate,
    ScopeSourceRow,
    SuggestionStrength,
)


class CandidateRankingService:
    """Advisory ranking for human review; it never returns a mapping decision."""

    def __init__(self, *, maximum_candidates: int = 8) -> None:
        self.maximum_candidates = maximum_candidates

    def rank(
        self,
        scope: ScopeSourceRow,
        rst_records: tuple[dict[str, object], ...],
        order_counts: dict[str, int],
    ) -> tuple[RestaurantCandidate, ...]:
        ranked: list[RestaurantCandidate] = []
        scope_name = _normalized(scope.restaurant_name)
        scope_tokens = _tokens(scope_name)
        scope_city = _normalized(scope.city)
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
            chain_tokens = _tokens(_normalized(record.get("chain")))
            chain_signal = bool(scope_tokens and chain_tokens & scope_tokens)
            score = min(
                1.0,
                name_similarity * 0.65
                + token_overlap * 0.15
                + (0.12 if same_city else 0.0)
                + (0.08 if chain_signal else 0.0),
            )
            indicators = [f"NAME {name_similarity:.0%}"]
            if token_overlap:
                indicators.append(f"TOKENS {token_overlap:.0%}")
            if same_city:
                indicators.append("SAME CITY")
            if chain_signal:
                indicators.append("CHAIN/BRAND TOKEN")
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
                    store_type=_optional_text(record.get("store_type")),
                    status=_optional_text(record.get("status")),
                    commission_rate=record.get("commission_rate"),
                    email=_optional_text(record.get("email")),
                    canonical_order_count=canonical_orders,
                    name_similarity=name_similarity,
                    same_city=same_city,
                    chain_signal=chain_signal,
                    token_overlap=token_overlap,
                    advisory_score=score,
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
