from __future__ import annotations

from src.config import get_settings
from src.documents.publishing import (
    DocumentPublicationRepository,
    DocumentPublicationStatus,
)
from src.documents.r2_storage import CloudflareR2DocumentSource

PERIOD = "2026-07-P2"
EXPECTED_FALSE_VERSIONS = 309


def main() -> None:
    settings = get_settings()
    repository = DocumentPublicationRepository(
        settings.document_publication_registry_path
    )
    latest = repository.list_latest_for_period(PERIOD)
    v1 = {
        (item.restaurant_id, item.document_type): item
        for item in latest
        if item.provider == "R2" and item.document_version == 1
    }
    v2 = {
        (item.restaurant_id, item.document_type): item
        for item in latest
        if item.provider == "R2"
        and item.document_version == 2
        and item.status == DocumentPublicationStatus.PUBLISHED
    }
    if len(v1) != EXPECTED_FALSE_VERSIONS or len(v2) != EXPECTED_FALSE_VERSIONS:
        raise RuntimeError("CORRECTIVE_SCOPE_COUNT_MISMATCH")
    if set(v1) != set(v2):
        raise RuntimeError("CORRECTIVE_IDENTITY_MISMATCH")
    for identity, older in v1.items():
        newer = v2[identity]
        if not (
            older.financial_snapshot_hash == newer.financial_snapshot_hash
            and older.legal_snapshot_hash == newer.legal_snapshot_hash
            and older.financial_policy_version == newer.financial_policy_version
            and newer.object_key
            and newer.object_key.endswith("_v2.pdf")
        ):
            raise RuntimeError("BUSINESS_SNAPSHOT_CHANGED_ABORT_DELETE")

    provider = CloudflareR2DocumentSource.from_settings(settings)
    keys = tuple(v2[identity].object_key for identity in sorted(v2))
    if any(key is None for key in keys):
        raise RuntimeError("OBJECT_KEY_MISSING")
    provider.delete_objects(tuple(key for key in keys if key is not None))
    for identity in sorted(v2):
        repository.supersede(v2[identity])
        repository.append(
            v1[identity].model_copy(
                update={"status": DocumentPublicationStatus.PUBLISHED}
            )
        )
    repository.append_document_audit(
        "FALSE_DOCUMENT_VERSIONS_REMOVED",
        {
            "period_code": PERIOD,
            "documents": len(keys),
            "reason": "RUNTIME_TIMESTAMP_HASH_INSTABILITY",
        },
    )
    remaining = provider.count_objects("2026/07/P2/")
    if remaining != EXPECTED_FALSE_VERSIONS:
        raise RuntimeError("CORRECTIVE_OBJECT_COUNT_MISMATCH")
    print("CORRECTIVE_REPAIR", "PASS")
    print("REMOVED_FALSE_VERSIONS", len(keys))
    print("CURRENT_PERIOD_OBJECTS", remaining)


if __name__ == "__main__":
    main()
