from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import pandas as pd

CANONICAL_ALIASES: dict[str, frozenset[str]] = {
    "order_id": frozenset({"order id"}),
    "restaurant_id": frozenset({"restaurant id"}),
    "restaurant_name": frozenset({"restaurant name"}),
    "order_date": frozenset({"order day"}),
    "order_created_at": frozenset({"accepted at"}),
    "operational_status": frozenset({"status"}),
    "cancellation_reason": frozenset({"cancellation reason"}),
    "item_total": frozenset({"item total"}),
    "discount": frozenset({"total discount amount", "discount amount"}),
    "delivery_fee": frozenset({"delivery amount"}),
    "commission_amount": frozenset({"restaurant commission"}),
    "promo_amount": frozenset({"coupon discount"}),
}
CRITICAL_FIELDS = frozenset({"order_id", "restaurant_id", "order_date"})


def normalize_header(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def resolve_schema(
    frame: pd.DataFrame, configured: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    by_normalized: dict[str, list[str]] = defaultdict(list)
    for column in frame.columns:
        by_normalized[normalize_header(column)].append(str(column))
    resolved: dict[str, str] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    configured = configured or {}
    for canonical, configured_column in configured.items():
        if configured_column in frame.columns:
            resolved[canonical] = configured_column
    for canonical, aliases in CANONICAL_ALIASES.items():
        if canonical in resolved:
            continue
        matches = sorted(
            {raw for alias in aliases for raw in by_normalized.get(alias, [])}
        )
        if len(matches) == 1:
            resolved[canonical] = matches[0]
        elif len(matches) > 1:
            ambiguous[canonical] = tuple(matches)
    return resolved, ambiguous
