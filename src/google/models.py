from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SUPPORTED_SOURCE_MIME_TYPES = frozenset(
    {
        "text/csv",
        "application/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.google-apps.spreadsheet",
    }
)


class DriveFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file_id: str
    name: str
    mime_type: str
    modified_time: datetime
    created_time: datetime | None = None
    size: int | None = None
    md5_checksum: str | None = None
    parent_ids: tuple[str, ...] = ()
    web_view_link: str | None = None
    is_folder: bool = False
    capabilities: dict[str, bool] = {}

    @classmethod
    def from_api(cls, value: dict[str, object]) -> DriveFile:
        mime_type = str(value["mimeType"])
        return cls(
            file_id=str(value["id"]),
            name=str(value["name"]),
            mime_type=mime_type,
            modified_time=datetime.fromisoformat(
                str(value["modifiedTime"]).replace("Z", "+00:00")
            ),
            created_time=(
                datetime.fromisoformat(str(value["createdTime"]).replace("Z", "+00:00"))
                if value.get("createdTime")
                else None
            ),
            size=int(str(value["size"])) if value.get("size") is not None else None,
            md5_checksum=str(value["md5Checksum"])
            if value.get("md5Checksum")
            else None,
            parent_ids=tuple(str(item) for item in value.get("parents", [])),  # type: ignore[arg-type]
            web_view_link=str(value["webViewLink"])
            if value.get("webViewLink")
            else None,
            is_folder=mime_type == FOLDER_MIME_TYPE,
            capabilities={
                str(key): bool(item)
                for key, item in dict(value.get("capabilities", {})).items()  # type: ignore[arg-type]
            },
        )


class DriveConnectionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    connected: bool
    root_name: str | None = None


class AccessLevel(StrEnum):
    READABLE = "READABLE"
    READ_WRITE = "READ / WRITE"
    READ_ONLY = "READ ONLY"
    INACCESSIBLE = "INACCESSIBLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class DriveAccessResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: str
    object_id: str | None = None
    access: AccessLevel
    readable: bool = False
    writable: bool | None = None
    object: DriveFile | None = None
    message: str | None = None
