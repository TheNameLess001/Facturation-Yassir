from __future__ import annotations

import io
import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.google.exceptions import (
    DriveConnectionError,
    DriveFileNotFoundError,
    DrivePermissionError,
)
from src.google.models import (
    FOLDER_MIME_TYPE,
    DriveConnectionResult,
    DriveFile,
)

LOGGER = logging.getLogger(__name__)
FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,md5Checksum,parents,webViewLink"
)


class GoogleDriveService:
    """Read-only Drive v3 adapter with Shared Drive-compatible request flags."""

    def __init__(
        self, credentials: Any | None = None, *, api: Any | None = None
    ) -> None:
        self._api = api or build(
            "drive", "v3", credentials=credentials, cache_discovery=False
        )

    def list_files(self, folder_id: str) -> tuple[DriveFile, ...]:
        query = f"'{self._escape(folder_id)}' in parents and trashed = false"
        return tuple(item for item in self._list(query) if not item.is_folder)

    def list_child_folders(self, folder_id: str) -> tuple[DriveFile, ...]:
        query = (
            f"'{self._escape(folder_id)}' in parents and "
            f"mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
        )
        return self._list(query)

    def get_file_metadata(self, file_id: str) -> DriveFile:
        try:
            payload = (
                self._api.files()
                .get(fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )
            return DriveFile.from_api(payload)
        except HttpError as exc:
            raise self._translate_error(exc, file_id) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive metadata request failed") from exc

    def get_folder_metadata(self, folder_id: str) -> DriveFile:
        item = self.get_file_metadata(folder_id)
        if not item.is_folder:
            raise DriveFileNotFoundError("Configured Drive folder is not a folder")
        return item

    def find_files(
        self, folder_id: str, *, name_contains: str | None = None
    ) -> tuple[DriveFile, ...]:
        files = self.list_files(folder_id)
        if name_contains is None:
            return files
        needle = name_contains.casefold()
        return tuple(item for item in files if needle in item.name.casefold())

    def file_exists(self, file_id: str) -> bool:
        try:
            self.get_file_metadata(file_id)
            return True
        except DriveFileNotFoundError:
            return False

    def download_file(self, file_id: str) -> bytes:
        """Read a source object; discovery itself never invokes this method."""
        buffer = io.BytesIO()
        try:
            request = self._api.files().get_media(
                fileId=file_id, supportsAllDrives=True
            )
            downloader = MediaIoBaseDownload(buffer, request)
            complete = False
            while not complete:
                _, complete = downloader.next_chunk()
            return buffer.getvalue()
        except HttpError as exc:
            raise self._translate_error(exc, file_id) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive download failed") from exc

    def test_connection(self, root_folder_id: str) -> DriveConnectionResult:
        folder = self.get_folder_metadata(root_folder_id)
        LOGGER.info(
            "drive_connection_success", extra={"root_folder_id": root_folder_id}
        )
        return DriveConnectionResult(connected=True, root_name=folder.name)

    def _list(self, query: str) -> tuple[DriveFile, ...]:
        found: list[DriveFile] = []
        page_token: str | None = None
        try:
            while True:
                payload = (
                    self._api.files()
                    .list(
                        q=query,
                        fields=f"nextPageToken,files({FILE_FIELDS})",
                        pageSize=1000,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        corpora="allDrives",
                    )
                    .execute()
                )
                found.extend(
                    DriveFile.from_api(item) for item in payload.get("files", [])
                )
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            raise self._translate_error(exc) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive listing failed") from exc
        return tuple(
            sorted(found, key=lambda item: (item.name.casefold(), item.file_id))
        )

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "\\'")

    @staticmethod
    def _translate_error(exc: HttpError, file_id: str | None = None) -> Exception:
        status = getattr(exc.resp, "status", None)
        if status == 404:
            return DriveFileNotFoundError(
                f"Drive object not found: {file_id or 'unknown'}"
            )
        if status in {401, 403}:
            return DrivePermissionError("Google Drive permission denied")
        return DriveConnectionError("Google Drive request failed")
