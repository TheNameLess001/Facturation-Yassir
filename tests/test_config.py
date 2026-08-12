from src.config import Settings
from src.models.enums import Role


def test_safe_development_defaults(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    settings = Settings(_env_file=None)
    assert settings.default_user_role == Role.ADMIN
    assert settings.drive_root_folder_id is None
    assert settings.currency == "MAD"
    assert settings.google_credentials_configured is False
    assert settings.drive_sources_configured is False


def test_confirmed_drive_settings_are_centrally_loaded(monkeypatch) -> None:
    values = {
        "CASHCO_ADMIN_EARNINGS_FOLDER_ID": "admin",
        "CASHCO_RST_LIST_FILE_ID": "rst",
        "CASHCO_FINANCE_TRACKING_FILE_ID": "finance-file",
        "CASHCO_FINANCE_TRACKING_FOLDER_ID": "finance-folder",
        "CASHCO_CONFIG_FOLDER_ID": "config",
        "CASHCO_PROCESSED_FOLDER_ID": "processed",
        "CASHCO_PARTNERS_FOLDER_ID": "partners",
        "CASHCO_DOCUMENTS_FOLDER_ID": "documents",
        "CASHCO_AUDIT_FOLDER_ID": "audit",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.finance_tracking_file_id == "finance-file"
    assert settings.finance_tracking_folder_id == "finance-folder"
    assert settings.config_folder_id == "config"
    assert settings.drive_sources_configured is True
