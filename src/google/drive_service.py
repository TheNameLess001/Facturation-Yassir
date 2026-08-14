from __future__ import annotations

import io
import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from src.google.exceptions import (
    DriveConnectionError,
    DriveFileNotFoundError,
    DriveFolderNotFoundError,
    DrivePermissionError,
    GoogleAuthenticationError,
)
from src.google.models import (
    FOLDER_MIME_TYPE,
    AccessLevel,
    DriveAccessResult,
    DriveConnectionResult,
    DriveFile,
)

LOGGER = logging.getLogger(__name__)
FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,md5Checksum,parents,driveId,spaces,"
    "webViewLink,capabilities(canAddChildren,canEdit,canDownload,canDelete)"
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
            raise DriveFolderNotFoundError("Configured Drive folder is not a folder")
        return item

    def check_access(
        self, object_id: str | None, *, location: str, folder: bool, require_write: bool
    ) -> DriveAccessResult:
        """Inspect metadata/capabilities without mutating the Drive object."""
        if not object_id:
            return DriveAccessResult(
                location=location,
                access=AccessLevel.NOT_CONFIGURED,
                message=f"{location} is not configured.",
            )
        try:
            item = (
                self.get_folder_metadata(object_id)
                if folder
                else self.get_file_metadata(object_id)
            )
            writable = bool(
                item.capabilities.get("canAddChildren")
                if folder
                else item.capabilities.get("canEdit")
            )
            if require_write and not writable:
                return DriveAccessResult(
                    location=location,
                    object_id=object_id,
                    access=AccessLevel.READ_ONLY,
                    readable=True,
                    writable=False,
                    object=item,
                    message=f"{location} is readable but write access was not confirmed.",
                )
            return DriveAccessResult(
                location=location,
                object_id=object_id,
                access=AccessLevel.READ_WRITE if require_write else AccessLevel.READABLE,
                readable=True,
                writable=writable if require_write else None,
                object=item,
            )
        except DrivePermissionError:
            LOGGER.warning("drive_permission_failure", extra={"location": location})
            return DriveAccessResult(
                location=location,
                object_id=object_id,
                access=AccessLevel.INACCESSIBLE,
                message=f"{location} is configured but CashCo does not have permission to read it.",
            )
        except DriveFileNotFoundError:
            return DriveAccessResult(
                location=location,
                object_id=object_id,
                access=AccessLevel.INACCESSIBLE,
                message=f"{location} could not be found.",
            )
        except DriveConnectionError:
            return DriveAccessResult(
                location=location,
                object_id=object_id,
                access=AccessLevel.INACCESSIBLE,
                message=f"{location} could not be checked because Drive is unavailable.",
            )

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

    def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a Google-native file in memory without mutating the source."""
        buffer = io.BytesIO()
        try:
            request = self._api.files().export_media(fileId=file_id, mimeType=mime_type)
            downloader = MediaIoBaseDownload(buffer, request)
            complete = False
            while not complete:
                _, complete = downloader.next_chunk()
            return buffer.getvalue()
        except HttpError as exc:
            raise self._translate_error(exc, file_id) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive export failed") from exc

    def ensure_folder(self, parent_id: str, name: str) -> DriveFile:
        for folder in self.list_child_folders(parent_id):
            if folder.name == name:
                return folder
        try:
            payload = (
                self._api.files()
                .create(
                    body={"name": name, "mimeType": FOLDER_MIME_TYPE, "parents": [parent_id]},
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
            return DriveFile.from_api(payload)
        except HttpError as exc:
            raise self._translate_error(exc) from exc

    def upload_bytes_atomic(
        self, folder_id: str, name: str, content: bytes, mime_type: str
    ) -> DriveFile:
        """Stage bytes, then replace/create the named processed artifact."""
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
        temporary_name = f".{name}.uploading"
        try:
            temporary = (
                self._api.files()
                .create(
                    body={"name": temporary_name, "parents": [folder_id]},
                    media_body=media,
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
            existing = tuple(file for file in self.list_files(folder_id) if file.name == name)
            if existing:
                final = (
                    self._api.files()
                    .update(
                        fileId=existing[0].file_id,
                        media_body=MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True),
                        fields=FILE_FIELDS,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                self._api.files().delete(fileId=temporary["id"], supportsAllDrives=True).execute()
            else:
                final = (
                    self._api.files()
                    .update(
                        fileId=temporary["id"],
                        body={"name": name},
                        fields=FILE_FIELDS,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            return DriveFile.from_api(final)
        except HttpError as exc:
            raise self._translate_error(exc) from exc

    def update_file_content(
        self, file_id: str, content: bytes, mime_type: str
    ) -> DriveFile:
        """Update an existing file's content; this method never creates a Drive object."""
        try:
            payload = (
                self._api.files()
                .update(
                    fileId=file_id,
                    media_body=MediaIoBaseUpload(
                        io.BytesIO(content), mimetype=mime_type, resumable=True
                    ),
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
            return DriveFile.from_api(payload)
        except HttpError as exc:
            raise self._translate_error(exc, file_id) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive update failed") from exc

    def create_file(
        self, folder_id: str, name: str, content: bytes, mime_type: str
    ) -> DriveFile:
        """Create one immutable publication object without replacing a namesake."""
        media = MediaIoBaseUpload(
            io.BytesIO(content), mimetype=mime_type, resumable=True
        )
        try:
            payload = (
                self._api.files()
                .create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=media,
                    fields=FILE_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
            return DriveFile.from_api(payload)
        except HttpError as exc:
            raise self._translate_error(exc) from exc
        except Exception as exc:
            raise DriveConnectionError("Google Drive create failed") from exc

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
        if status == 401:
            return GoogleAuthenticationError("Google Drive authentication failed")
        if status == 403:
            return DrivePermissionError("Google Drive permission denied")
        return DriveConnectionError("Google Drive request failed")
