from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from src.emails.phase10_models import DocumentAttachmentRef, PartnerEmailPackage

PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class StoredDocument:
    """Provider-neutral immutable object metadata returned with R2 bytes."""

    object_key: str
    content: bytes
    content_type: str
    content_hash: str
    document_id: str
    document_type: str
    version: int
    period_code: str
    restaurant_id: str
    financial_snapshot_hash: str


class R2DocumentSource(Protocol):
    def get_document(self, object_key: str) -> StoredDocument: ...


class R2AttachmentLoader:
    """Loads a package from private R2 and rejects any snapshot drift."""

    def __init__(
        self, source: R2DocumentSource, object_keys: Mapping[str, str]
    ) -> None:
        self.source = source
        self.object_keys = object_keys

    def load(self, package: PartnerEmailPackage) -> tuple[bytes, ...]:
        if not package.document_refs:
            raise ValueError("DOCUMENT_NOT_FOUND")
        return tuple(self._load_one(package, ref) for ref in package.document_refs)

    def _load_one(
        self, package: PartnerEmailPackage, ref: DocumentAttachmentRef
    ) -> bytes:
        object_key = self.object_keys.get(ref.document_id)
        if not object_key:
            raise ValueError("R2_OBJECT_KEY_MISSING")
        item = self.source.get_document(object_key)
        actual_hash = hashlib.sha256(item.content).hexdigest()
        checks = (
            (item.object_key == object_key, "R2_OBJECT_KEY_MISMATCH"),
            (item.content_type == PDF_CONTENT_TYPE, "ATTACHMENT_CONTENT_TYPE_INVALID"),
            (
                actual_hash == item.content_hash == ref.content_hash,
                "DOCUMENT_HASH_MISMATCH",
            ),
            (item.document_id == ref.document_id, "DOCUMENT_ID_MISMATCH"),
            (item.document_type == ref.document_type, "DOCUMENT_TYPE_MISMATCH"),
            (item.version == ref.version, "DOCUMENT_VERSION_MISMATCH"),
            (item.period_code == package.period_code, "DOCUMENT_PERIOD_MISMATCH"),
            (
                item.restaurant_id == package.restaurant_id,
                "DOCUMENT_RESTAURANT_MISMATCH",
            ),
            (
                item.financial_snapshot_hash == package.settlement_snapshot_hash,
                "FINANCIAL_SNAPSHOT_MISMATCH",
            ),
        )
        for valid, code in checks:
            if not valid:
                raise ValueError(code)
        return item.content
