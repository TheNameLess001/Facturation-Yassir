from __future__ import annotations

import hashlib
from collections import defaultdict

import pandas as pd

from src.ingestion.admin_earnings_schema import (
    CRITICAL_FIELDS,
    normalize_header,
    resolve_schema,
)
from src.ingestion.phase3_models import SchemaProfile


class SchemaProfiler:
    def __init__(self) -> None:
        self._groups: dict[tuple[str, ...], dict[str, object]] = defaultdict(
            lambda: {"files": [], "rows": 0, "source_columns": ()}
        )

    def add(self, filename: str, frame: pd.DataFrame, configured: dict[str, str] | None = None):
        source_signature = tuple(str(column) for column in frame.columns)
        normalized = tuple(normalize_header(column) for column in frame.columns)
        mapping, ambiguous = resolve_schema(frame, configured)
        group = self._groups[source_signature]
        group["normalized"] = normalized
        group["files"].append(filename)  # type: ignore[union-attr]
        group["rows"] = int(group["rows"]) + len(frame)
        group["source_columns"] = tuple(str(column) for column in frame.columns)
        group["mapping"] = mapping
        group["ambiguous"] = ambiguous
        return mapping, ambiguous

    def profiles(self) -> tuple[SchemaProfile, ...]:
        profiles = []
        for source_signature, group in sorted(self._groups.items()):
            normalized = tuple(group["normalized"])
            mapping = dict(group.get("mapping", {}))
            source_columns = tuple(group["source_columns"])
            profiles.append(
                SchemaProfile(
                    signature=hashlib.sha256("|".join(source_signature).encode()).hexdigest()[:16],
                    normalized_columns=normalized,
                    source_columns=source_columns,
                    files=tuple(sorted(group["files"])),
                    row_count=int(group["rows"]),
                    canonical_mapping=mapping,
                    missing_critical_fields=tuple(sorted(CRITICAL_FIELDS - mapping.keys())),
                    unexpected_columns=tuple(
                        column for column in source_columns if column not in mapping.values()
                    ),
                    ambiguous_mappings=dict(group.get("ambiguous", {})),
                )
            )
        return tuple(profiles)
