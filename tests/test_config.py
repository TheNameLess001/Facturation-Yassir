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
        "CASHCO_INVOICE_SCOPE_FILE_ID": "invoice-scope-file",
        "CASHCO_PARTNER_LEGAL_MASTER_FILE_ID": "partner-legal-master",
        "CASHCO_CONFIG_FOLDER_ID": "config",
        "CASHCO_PROCESSED_FOLDER_ID": "processed",
        "CASHCO_PARTNERS_FOLDER_ID": "partners",
        "CASHCO_DOCUMENTS_FOLDER_ID": "documents",
        "CASHCO_AUDIT_FOLDER_ID": "audit",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.invoice_scope_file_id == "invoice-scope-file"
    assert settings.partner_legal_master_file_id == "partner-legal-master"
    assert settings.partner_legal_master_worksheet == "PARTNERS"
    assert settings.config_folder_id == "config"
    assert settings.drive_sources_configured is True


def test_legacy_finance_environment_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("CASHCO_FINANCE_TRACKING_FILE_ID", "legacy-file")
    monkeypatch.setenv("CASHCO_FINANCE_TRACKING_FOLDER_ID", "legacy-folder")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "finance_tracking_file_id")
    assert not hasattr(settings, "finance_tracking_folder_id")
