import pytest

from src.config import Settings
from src.google.auth import build_google_credentials, parse_service_account_json
from src.google.exceptions import GoogleAuthenticationError


def test_invalid_service_account_json_is_safely_rejected() -> None:
    settings = Settings(
        _env_file=None,
        google_auth_mode="SERVICE_ACCOUNT",
        GOOGLE_SERVICE_ACCOUNT_JSON="{invalid-json",
    )
    with pytest.raises(
        GoogleAuthenticationError, match="configuration is invalid"
    ) as error:
        build_google_credentials(settings)
    assert "invalid-json" not in str(error.value)


def test_missing_credentials_are_not_treated_as_connected(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    settings = Settings(_env_file=None, google_auth_mode="SERVICE_ACCOUNT")
    with pytest.raises(GoogleAuthenticationError, match="not configured"):
        build_google_credentials(settings)


def test_service_account_json_requires_minimum_fields() -> None:
    settings = Settings(
        _env_file=None,
        GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
    )
    with pytest.raises(GoogleAuthenticationError, match="configuration is invalid"):
        parse_service_account_json(settings)
