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
    invoice_scope_file_id: str | None = None
    invoice_scope_worksheet: str = "CASH-CO"
    invoice_scope_column_map: dict[str, str] = Field(default_factory=dict)
    partner_legal_master_file_id: str | None = (
        "1oHvDXkKdqOIiw8JVknoO65dkAw3p2NaD209l3jkPuAQ"
    )
    partner_legal_master_worksheet: str = "PARTNERS"
    partner_legal_master_column_map: dict[str, str] = Field(default_factory=dict)
    partner_legal_master_cache_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    # LEGACY / DEPRECATED / NOT USED. Permanent corrections belong in Invoice Scope.
    invoice_scope_alias_map: dict[str, str] = Field(default_factory=dict)
    # LEGACY / DEPRECATED / NOT USED by active CashCo V2 runtime. These fields
    # remain temporarily so historical tests and local environments can load.
    payment_scope_folder_id: str | None = None
    payment_scope_column_map: dict[str, str] = Field(default_factory=dict)
    payment_scope_max_file_mb: int = Field(default=25, gt=0, le=250)
    payment_scope_csv_encoding: str = "utf-8-sig"
    rst_list_file_id: str | None = None
    rst_list_folder_id: str | None = None
    rst_column_map: dict[str, str] = Field(default_factory=dict)
    rst_max_file_mb: int = Field(default=50, gt=0, le=500)
    rst_csv_encoding: str = "utf-8-sig"
    restaurant_registry_path: Path = Path("data/local/restaurant_registry.sqlite3")
    processed_folder_id: str | None = None
    config_folder_id: str | None = None
    partners_folder_id: str | None = None
    documents_folder_id: str | None = None
    document_storage_mode: Literal["DISABLED", "SHARED_DRIVE", "OAUTH_USER"] = (
        "DISABLED"
    )
    document_storage_provider: Literal["DISABLED", "R2"] = "DISABLED"
    r2_endpoint_url: str | None = Field(
        default=None, validation_alias=AliasChoices("R2_ENDPOINT", "CASHCO_R2_ENDPOINT_URL")
    )
    r2_bucket: str | None = Field(
        default=None, validation_alias=AliasChoices("R2_BUCKET_NAME", "CASHCO_R2_BUCKET")
    )
    r2_access_key_id: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("R2_ACCESS_KEY_ID", "CASHCO_R2_ACCESS_KEY_ID")
    )
    r2_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("R2_SECRET_ACCESS_KEY", "CASHCO_R2_SECRET_ACCESS_KEY"),
    )
    r2_signed_url_expiry_seconds: int = Field(
        default=300,
        ge=60,
        le=900,
        validation_alias=AliasChoices(
            "R2_SIGNED_URL_EXPIRY_SECONDS",
            "CASHCO_R2_SIGNED_URL_EXPIRY_SECONDS",
        ),
    )
    documents_shared_drive_id: str | None = None
    audit_folder_id: str | None = None
    document_registry_path: Path = Path("data/local/document_registry.sqlite3")
    document_publication_registry_path: Path = Path(
        "data/local/document_publications.sqlite3"
    )
    document_publish_mode: Literal["PREVIEW", "SAMPLE", "PRODUCTION"] = "PREVIEW"
    document_sample_size: int = Field(default=1, ge=1, le=3)
    email_registry_path: Path = Path("data/local/email_registry.sqlite3")
    authorization_registry_path: Path = Path(
        "data/local/authorization_registry.sqlite3"
    )
    payment_registry_path: Path = Path("data/local/payment_registry.sqlite3")
    period_lock_registry_path: Path = Path("data/local/period_lock_registry.sqlite3")
    financial_override_registry_path: Path = Path(
        "data/local/financial_override_registry.sqlite3"
    )
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
    google_oauth_user_json: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_OAUTH_USER_JSON", "CASHCO_GOOGLE_OAUTH_USER_JSON"
        ),
    )
    test_email_recipient: str | None = None
    email_default_mode: Literal["OFF", "PREVIEW", "DRAFT", "SEND"] = "OFF"
    email_allow_drafts: bool = False
    email_allow_send: bool = False
    production_email_send_enabled: bool = False
    gmail_auth_mode: Literal["NOT_CONFIGURED", "OAUTH", "DOMAIN_DELEGATION"] = (
        "NOT_CONFIGURED"
    )
    gmail_sender_email: str | None = None
    gmail_domain_delegated_user: str | None = None
    gmail_oauth_user_json: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GMAIL_OAUTH_USER_JSON", "CASHCO_GMAIL_OAUTH_USER_JSON"
        ),
    )
    gmail_execution_mode: Literal["DISABLED", "SANDBOX", "PRODUCTION"] = "DISABLED"
    gmail_sandbox_recipient: str | None = None
    # Deprecated compatibility switches. Draft permission is now derived from
    # SANDBOX mode plus validated auth/sender/recipient; sending remains gated
    # exclusively by the explicit send flag below.
    gmail_sandbox_allow_drafts: bool = False
    gmail_sandbox_allow_send: bool = False
    gmail_sandbox_send_enabled: bool = False
    email_workflow_registry_path: Path = Path(
        "data/local/email_workflow_registry.sqlite3"
    )
    billing_operations_registry_path: Path = Path(
        "data/local/billing_operations_registry.sqlite3"
    )
    review_registry_path: Path = Path("data/local/review_registry.sqlite3")

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
            self.invoice_scope_file_id,
            self.partner_legal_master_file_id,
            self.rst_list_file_id,
            self.config_folder_id,
            self.processed_folder_id,
            self.partners_folder_id,
            self.documents_folder_id,
            self.audit_folder_id,
        )
        return all(value and value.strip() for value in required_ids)

    @property
    def r2_configured(self) -> bool:
        return bool(
            self.r2_endpoint_url
            and self.r2_bucket
            and self.r2_access_key_id
            and self.r2_secret_access_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
