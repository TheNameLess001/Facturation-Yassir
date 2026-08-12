from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from src.google.drive_service import FILE_FIELDS, GoogleDriveService
from src.google.exceptions import DrivePermissionError
from src.google.models import FOLDER_MIME_TYPE


def api_file(file_id: str = "file-1", name: str = "source.xlsx") -> dict[str, object]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "modifiedTime": "2026-08-12T10:00:00Z",
        "createdTime": "2026-08-01T08:00:00Z",
        "size": "2048",
        "md5Checksum": "abc123",
        "parents": ["folder-1"],
        "webViewLink": "https://drive.google.com/file/d/file-1/view",
    }


def test_drive_service_metadata_normalization() -> None:
    request = Mock()
    request.execute.return_value = api_file()
    files = Mock()
    files.get.return_value = request
    api = Mock()
    api.files.return_value = files

    result = GoogleDriveService(api=api).get_file_metadata("file-1")

    assert result.file_id == "file-1"
    assert result.modified_time == datetime(2026, 8, 12, 10, tzinfo=UTC)
    assert result.size == 2048
    assert result.md5_checksum == "abc123"
    assert result.parent_ids == ("folder-1",)
    files.get.assert_called_once_with(
        fileId="file-1",
        fields=FILE_FIELDS,
        supportsAllDrives=True,
    )


def test_drive_service_lists_with_shared_drive_flags() -> None:
    request = Mock()
    request.execute.return_value = {"files": [api_file()]}
    files = Mock()
    files.list.return_value = request
    api = Mock()
    api.files.return_value = files

    assert len(GoogleDriveService(api=api).list_files("folder-1")) == 1
    kwargs = files.list.call_args.kwargs
    assert kwargs["supportsAllDrives"] is True
    assert kwargs["includeItemsFromAllDrives"] is True


def test_authentication_http_error_is_safely_translated() -> None:
    response = Mock(status=403, reason="Forbidden")
    request = Mock()
    request.execute.side_effect = HttpError(response, b'"private technical detail"')
    files = Mock()
    files.get.return_value = request
    api = Mock()
    api.files.return_value = files

    with pytest.raises(DrivePermissionError, match="permission denied") as error:
        GoogleDriveService(api=api).get_file_metadata("secret-file")
    assert "private technical detail" not in str(error.value)


def test_folder_metadata_requires_folder() -> None:
    request = Mock()
    payload = api_file("folder", "Not a folder")
    payload["mimeType"] = FOLDER_MIME_TYPE
    request.execute.return_value = payload
    files = Mock()
    files.get.return_value = request
    api = Mock()
    api.files.return_value = files
    assert GoogleDriveService(api=api).get_folder_metadata("folder").is_folder
