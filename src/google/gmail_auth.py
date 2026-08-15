from __future__ import annotations

import json
from typing import Any

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as AuthorizedUserCredentials
from googleapiclient.discovery import build

from src.config import Settings
from src.google.auth import parse_service_account_json
from src.google.exceptions import GoogleAuthenticationError, GoogleConfigurationError

GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


def _parse_gmail_oauth_user(settings: Settings) -> dict[str, object]:
    secret = settings.gmail_oauth_user_json
    if secret is None or not secret.get_secret_value().strip():
        raise GoogleConfigurationError("Gmail OAuth user is not configured")
    try:
        parsed = json.loads(secret.get_secret_value())
    except (TypeError, json.JSONDecodeError) as exc:
        raise GoogleConfigurationError("Gmail OAuth configuration is invalid") from exc
    required = ("client_id", "client_secret", "refresh_token")
    if not isinstance(parsed, dict) or any(
        not isinstance(parsed.get(field), str) or not parsed[field].strip()
        for field in required
    ):
        raise GoogleConfigurationError("Gmail OAuth configuration is invalid")
    return parsed


def build_gmail_credentials(settings: Settings) -> Any:
    """Build Gmail-only credentials from external secrets."""
    if not settings.gmail_sender_email or not settings.gmail_sender_email.strip():
        raise GoogleConfigurationError("Gmail sender is not configured")
    try:
        if settings.gmail_auth_mode == "OAUTH":
            return AuthorizedUserCredentials.from_authorized_user_info(
                _parse_gmail_oauth_user(settings), scopes=[GMAIL_COMPOSE_SCOPE]
            )
        if settings.gmail_auth_mode == "DOMAIN_DELEGATION":
            delegated_user = (settings.gmail_domain_delegated_user or "").strip()
            if not delegated_user:
                raise GoogleConfigurationError(
                    "Gmail delegated user is not configured"
                )
            return service_account.Credentials.from_service_account_info(
                parse_service_account_json(settings), scopes=[GMAIL_COMPOSE_SCOPE]
            ).with_subject(delegated_user)
    except GoogleConfigurationError:
        raise
    except (TypeError, ValueError) as exc:
        raise GoogleConfigurationError("Gmail authentication is invalid") from exc
    raise GoogleConfigurationError("Gmail authentication is not configured")


def build_gmail_api(settings: Settings) -> Any:
    try:
        return build(
            "gmail",
            "v1",
            credentials=build_gmail_credentials(settings),
            cache_discovery=False,
        )
    except GoogleConfigurationError:
        raise
    except Exception as exc:
        raise GoogleAuthenticationError("Gmail authentication failed") from exc
