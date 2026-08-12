from __future__ import annotations

import json
import logging
from typing import Any

from google.oauth2 import service_account

import google.auth
from src.config import Settings
from src.google.exceptions import GoogleAuthenticationError

LOGGER = logging.getLogger(__name__)
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
REQUIRED_SERVICE_ACCOUNT_FIELDS = frozenset(
    {
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "token_uri",
    }
)


def parse_service_account_json(settings: Settings) -> dict[str, str]:
    """Validate the in-memory service-account secret without persisting it."""
    if not settings.google_credentials_configured:
        raise GoogleAuthenticationError("Google credentials are not configured")
    try:
        parsed = json.loads(settings.google_service_account_json.get_secret_value())  # type: ignore[union-attr]
    except (TypeError, json.JSONDecodeError) as exc:
        raise GoogleAuthenticationError(
            "Google authentication configuration is invalid"
        ) from exc
    if not isinstance(parsed, dict):
        raise GoogleAuthenticationError("Google authentication configuration is invalid")
    if parsed.get("type") != "service_account" or any(
        not isinstance(parsed.get(field), str) or not parsed[field].strip()
        for field in REQUIRED_SERVICE_ACCOUNT_FIELDS
    ):
        raise GoogleAuthenticationError("Google authentication configuration is invalid")
    return {field: parsed[field] for field in REQUIRED_SERVICE_ACCOUNT_FIELDS}


def build_google_credentials(settings: Settings) -> Any:
    """Build read-only credentials without exposing or persisting credential data."""
    LOGGER.info(
        "google_authentication_attempt", extra={"auth_mode": settings.google_auth_mode}
    )
    try:
        if settings.google_auth_mode == "SERVICE_ACCOUNT" or (
            settings.google_auth_mode == "NOT_CONFIGURED"
            and settings.google_credentials_configured
        ):
            credential_info = parse_service_account_json(settings)
            return service_account.Credentials.from_service_account_info(
                credential_info, scopes=[DRIVE_READONLY_SCOPE]
            )
        if settings.google_auth_mode == "ADC":
            credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
            return credentials
        raise GoogleAuthenticationError("Google Drive is not configured")
    except GoogleAuthenticationError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning(
            "google_authentication_failed",
            extra={"auth_mode": settings.google_auth_mode},
        )
        raise GoogleAuthenticationError(
            "Google authentication configuration is invalid"
        ) from exc
    except Exception as exc:
        LOGGER.warning(
            "google_authentication_failed",
            extra={"auth_mode": settings.google_auth_mode},
        )
        raise GoogleAuthenticationError("Google authentication failed") from exc
