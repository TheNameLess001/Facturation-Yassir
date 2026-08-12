from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.models.enums import Role


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CASHCO_", env_file=".env", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    app_title: str = "CashCo"
    default_user_id: str = "admin.demo"
    default_user_name: str = "Demo Admin"
    default_user_email: str = "admin@example.com"
    default_user_role: Role = Role.ADMIN
    currency: str = "MAD"
    timezone: str = "Africa/Casablanca"
    settlement_rules: dict[str, object] = Field(default_factory=dict)
    google_auth_mode: Literal["NOT_CONFIGURED", "SERVICE_ACCOUNT", "ADC", "MOCK"] = (
        "NOT_CONFIGURED"
    )
    drive_root_folder_id: str | None = None
    admin_earnings_folder_id: str | None = None
    admin_earnings_column_map: dict[str, str] = Field(default_factory=dict)
    admin_earnings_max_file_mb: int = Field(default=100, gt=0, le=1000)
    admin_earnings_date_day_first: bool = False
    admin_earnings_csv_encoding: str = "utf-8-sig"
    payment_scope_folder_id: str | None = None
    payment_scope_column_map: dict[str, str] = Field(default_factory=dict)
    payment_scope_max_file_mb: int = Field(default=25, gt=0, le=250)
    payment_scope_csv_encoding: str = "utf-8-sig"
    rst_list_file_id: str | None = None
    rst_list_folder_id: str | None = None
    finance_tracking_file_id: str | None = None
    finance_tracking_folder_id: str | None = None
    rst_column_map: dict[str, str] = Field(default_factory=dict)
    rst_max_file_mb: int = Field(default=50, gt=0, le=500)
    rst_csv_encoding: str = "utf-8-sig"
    restaurant_registry_path: Path = Path("data/local/restaurant_registry.sqlite3")
    processed_folder_id: str | None = None
    config_folder_id: str | None = None
    partners_folder_id: str | None = None
    documents_folder_id: str | None = None
    audit_folder_id: str | None = None
    document_registry_path: Path = Path("data/local/document_registry.sqlite3")
    email_registry_path: Path = Path("data/local/email_registry.sqlite3")
    authorization_registry_path: Path = Path(
        "data/local/authorization_registry.sqlite3"
    )
    payment_registry_path: Path = Path("data/local/payment_registry.sqlite3")
    period_lock_registry_path: Path = Path("data/local/period_lock_registry.sqlite3")
    processed_data_path: Path = Path("data/processed")
    source_registry_path: Path = Path("data/local/source_registry.sqlite3")
    payment_scope_registry_path: Path = Path(
        "data/local/payment_scope_registry.sqlite3"
    )
    payment_scope_period_map: dict[str, str] = Field(default_factory=dict)
    google_service_account_json: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_SERVICE_ACCOUNT_JSON", "CASHCO_GOOGLE_SERVICE_ACCOUNT_JSON"
        ),
    )
    test_email_recipient: str | None = None

    @property
    def google_credentials_configured(self) -> bool:
        """Report secret presence without parsing, logging, or exposing its value."""
        return bool(
            self.google_service_account_json
            and self.google_service_account_json.get_secret_value().strip()
        )

    @property
    def drive_sources_configured(self) -> bool:
        """Report whether every confirmed Drive source/workspace ID is present."""
        required_ids = (
            self.admin_earnings_folder_id,
            self.rst_list_file_id,
            self.finance_tracking_file_id,
            self.finance_tracking_folder_id,
            self.config_folder_id,
            self.processed_folder_id,
            self.partners_folder_id,
            self.documents_folder_id,
            self.audit_folder_id,
        )
        return all(value and value.strip() for value in required_ids)


@lru_cache
def get_settings() -> Settings:
    return Settings()
