from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from src.google.exceptions import DriveConnectionError


class GoogleGmailService:
    def __init__(self, api: Any) -> None:
        self._api = api

    def create_draft(
        self, recipient: str, subject: str, body: str, attachments: list[bytes]
    ) -> str:
        raw = self._raw(recipient, subject, body, attachments)
        try:
            result = (
                self._api.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
            return str(result["id"])
        except Exception as exc:
            raise DriveConnectionError("Gmail draft creation failed") from exc

    def send_message(
        self, recipient: str, subject: str, body: str, attachments: list[bytes]
    ) -> str:
        raw = self._raw(recipient, subject, body, attachments)
        try:
            result = (
                self._api.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            return str(result["id"])
        except Exception as exc:
            raise DriveConnectionError("Gmail send failed") from exc

    @staticmethod
    def _raw(recipient: str, subject: str, body: str, attachments: list[bytes]) -> str:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        for index, content in enumerate(attachments, start=1):
            message.add_attachment(
                content,
                maintype="application",
                subtype="pdf",
                filename=f"attachment-{index}.pdf",
            )
        return base64.urlsafe_b64encode(message.as_bytes()).decode()
