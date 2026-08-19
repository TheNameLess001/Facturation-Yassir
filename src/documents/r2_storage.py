from __future__ import annotations

from typing import Any

from src.config import Settings
from src.documents.publishing import DocumentPublication
from src.emails.attachments import StoredDocument


class R2ConfigurationError(RuntimeError):
    pass


class CloudflareR2DocumentSource:
    """Private S3-compatible R2 reader. Object contents and credentials are never logged."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> CloudflareR2DocumentSource:
        if not all(
            (
                settings.r2_endpoint_url,
                settings.r2_bucket,
                settings.r2_access_key_id,
                settings.r2_secret_access_key,
            )
        ):
            raise R2ConfigurationError("R2_NOT_CONFIGURED")
        try:
            import boto3
        except ImportError as exc:
            raise R2ConfigurationError("R2_SDK_NOT_INSTALLED") from exc
        assert settings.r2_access_key_id and settings.r2_secret_access_key
        client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url.strip(),
            aws_access_key_id=settings.r2_access_key_id.get_secret_value().strip(),
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value().strip(),
            region_name="auto",
        )
        assert settings.r2_bucket
        return cls(client, settings.r2_bucket.strip())

    def get_document(self, object_key: str) -> StoredDocument:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        metadata = {
            str(k).casefold(): str(v) for k, v in response.get("Metadata", {}).items()
        }
        required = (
            "content-hash",
            "document-id",
            "document-type",
            "document-version",
            "period-code",
            "restaurant-id",
            "financial-snapshot-hash",
        )
        if any(not metadata.get(field) for field in required):
            raise ValueError("R2_DOCUMENT_METADATA_INCOMPLETE")
        return StoredDocument(
            object_key=object_key,
            content=response["Body"].read(),
            content_type=str(response.get("ContentType", "")),
            content_hash=metadata["content-hash"],
            document_id=metadata["document-id"],
            document_type=metadata["document-type"],
            version=int(metadata["document-version"]),
            period_code=metadata["period-code"],
            restaurant_id=metadata["restaurant-id"],
            financial_snapshot_hash=metadata["financial-snapshot-hash"],
        )

    def get_publication_document(
        self, publication: DocumentPublication
    ) -> StoredDocument:
        """Load a registry-bound legacy R2 object without requiring extra metadata."""
        if not publication.object_key:
            raise ValueError("R2_OBJECT_KEY_MISSING")
        response = self.client.get_object(
            Bucket=self.bucket, Key=publication.object_key
        )
        content = response["Body"].read()
        return StoredDocument(
            object_key=publication.object_key,
            content=content,
            content_type=str(response.get("ContentType", "")),
            content_hash=publication.document_hash,
            document_id=str(publication.publication_id),
            document_type=publication.document_type,
            version=publication.document_version,
            period_code=publication.period_code,
            restaurant_id=publication.restaurant_id,
            financial_snapshot_hash=publication.financial_snapshot_hash or "",
        )

    def put_pdf(self, object_key: str, content: bytes, metadata: dict[str, str]) -> str:
        result = self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=content,
            ContentType="application/pdf",
            Metadata=metadata,
        )
        return str(result.get("ETag", "")).strip('"')

    def head(self, object_key: str) -> dict[str, object]:
        result = self.client.head_object(Bucket=self.bucket, Key=object_key)
        return {
            "content_type": result.get("ContentType"),
            "size_bytes": int(result.get("ContentLength", 0)),
            "etag": str(result.get("ETag", "")).strip('"'),
            "metadata": {
                str(key).casefold(): str(value)
                for key, value in result.get("Metadata", {}).items()
            },
        }

    def download(self, object_key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=object_key)["Body"].read()

    def signed_get_url(self, object_key: str, expiry_seconds: int) -> str:
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": object_key},
                ExpiresIn=expiry_seconds,
            )
        )

    def health(self) -> bool:
        self.client.head_bucket(Bucket=self.bucket)
        return True

    def count_objects(self, prefix: str = "") -> int:
        paginator = self.client.get_paginator("list_objects_v2")
        return sum(
            int(page.get("KeyCount", len(page.get("Contents", ()))))
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
        )

    def delete_objects(self, object_keys: tuple[str, ...]) -> None:
        """Delete only explicit, fully resolved keys; never accepts prefixes."""
        if not object_keys or any(not key.endswith(".pdf") for key in object_keys):
            raise ValueError("EXPLICIT_PDF_OBJECT_KEYS_REQUIRED")
        for start in range(0, len(object_keys), 1000):
            result = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [
                        {"Key": key} for key in object_keys[start : start + 1000]
                    ],
                    "Quiet": False,
                },
            )
            if result.get("Errors"):
                raise RuntimeError("R2_CORRECTIVE_DELETE_FAILED")
