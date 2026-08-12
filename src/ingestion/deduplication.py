from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from src.ingestion.phase3_models import (
    CanonicalAdminOrder,
    DuplicateClassification,
    DuplicateConflict,
    DuplicateOccurrence,
    IngestionIssueRecord,
    IssueSeverity,
)

MATERIAL_FIELDS = (
    "restaurant_id",
    "order_date",
    "operational_status",
    "cancellation_reason",
    "item_total",
    "subtotal",
    "gross_amount",
    "discount",
    "promo_amount",
    "delivery_fee",
    "commission_amount",
    "commission_rate",
    "currency",
)


def deduplicate_orders(records: list[CanonicalAdminOrder]):
    grouped: dict[str, list[CanonicalAdminOrder]] = defaultdict(list)
    for record in records:
        grouped[record.order_id].append(record)
    canonical: list[CanonicalAdminOrder] = []
    duplicate_rows: list[DuplicateOccurrence] = []
    conflicts: list[DuplicateConflict] = []
    issues: list[IngestionIssueRecord] = []
    for order_id in sorted(grouped):
        occurrences = grouped[order_id]
        if len(occurrences) == 1:
            canonical.append(occurrences[0])
            continue
        first = occurrences[0]
        identical = all(x.material_payload() == first.material_payload() for x in occurrences[1:])
        if identical:
            lineage = tuple(item for record in occurrences for item in record.lineage)
            canonical.append(first.model_copy(update={"lineage": lineage}))
            for index, record in enumerate(occurrences):
                duplicate_rows.append(
                    DuplicateOccurrence(
                        order_id=order_id,
                        classification=DuplicateClassification.IDENTICAL_DUPLICATE,
                        retained=index == 0,
                        occurrence=record.lineage[0],
                    )
                )
            continue
        conflicting_fields = tuple(
            field
            for field in MATERIAL_FIELDS
            if len({str(getattr(item, field)) for item in occurrences}) > 1
        )
        conflicts.append(
            DuplicateConflict(
                order_id=order_id,
                conflicting_fields=conflicting_fields,
                values_by_occurrence=tuple(
                    {field: getattr(item, field) for field in conflicting_fields}
                    for item in occurrences
                ),
                occurrences=tuple(item.lineage[0] for item in occurrences),
                detected_at=datetime.now(UTC),
            )
        )
        for record in occurrences:
            duplicate_rows.append(
                DuplicateOccurrence(
                    order_id=order_id,
                    classification=DuplicateClassification.CONFLICTING_DUPLICATE,
                    retained=False,
                    occurrence=record.lineage[0],
                )
            )
        issues.append(
            IngestionIssueRecord(
                category="CONFLICTING_DUPLICATE",
                severity=IssueSeverity.BLOCKING,
                message="Conflicting duplicate was excluded from canonical orders and routed to REVIEW_QUEUE.",
                order_id=order_id,
            )
        )
    return canonical, duplicate_rows, conflicts, issues
