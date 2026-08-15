from __future__ import annotations

from src.config import Settings
from src.documents.publishing import DocumentStorageMode
from src.google.auth import build_document_storage_credentials
from src.google.drive_service import GoogleDriveService
from src.google.exceptions import GoogleConfigurationError


def build_document_storage_service(settings: Settings) -> GoogleDriveService:
    """Build only the explicitly configured publication identity."""
    mode = DocumentStorageMode(settings.document_storage_mode)
    if mode == DocumentStorageMode.DISABLED:
        raise GoogleConfigurationError("Document storage is disabled")
    return GoogleDriveService(build_document_storage_credentials(settings))
