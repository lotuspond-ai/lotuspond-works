import base64
import binascii
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    id: str = Field(min_length=1, max_length=160)
    input_usd_per_million: Decimal = Field(ge=0, le=1_000_000_000, alias="inputUsdPerMillion")
    output_usd_per_million: Decimal = Field(ge=0, le=1_000_000_000, alias="outputUsdPerMillion")

    @field_validator("input_usd_per_million", "output_usd_per_million")
    @classmethod
    def finite_rate(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Provider prices must be finite")
        return value


class AlertWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    url: str = Field(min_length=1, max_length=2000)
    secret: SecretStr = Field(min_length=32, max_length=1024)
    enabled: bool = False

    @model_validator(mode="after")
    def safe_url(self):
        endpoint = urlsplit(self.url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError(
                "Alert webhooks require HTTPS without credentials, query, or fragment"
            )
        self.url = self.url.rstrip("/")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOTUSPOND_", extra="ignore", hide_input_in_errors=True
    )
    env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://lotuspond@127.0.0.1:55439/lotuspond_platform_dev"
    )
    redis_url: SecretStr | None = None
    origin: str = "http://127.0.0.1:8000"
    registration_enabled: bool = False
    session_hours: int = Field(default=24, ge=1, le=168)
    max_request_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    network_mode: Literal["connected", "airgap"] = "connected"
    typesafe_base_url: str | None = None
    typesafe_api_key: SecretStr | None = None
    typesafe_model: str = "jev-latest"
    typesafe_timeout_seconds: float = Field(default=20, gt=0, le=60)
    provider_url: str | None = None
    provider_key: SecretStr | None = None
    provider_models: list[ProviderModel] = Field(default_factory=list)
    provider_timeout_seconds: float = Field(default=20, gt=0, le=60)
    provider_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    alert_webhooks: list[AlertWebhook] = Field(default_factory=list, max_length=20)
    alert_timeout_seconds: float = Field(default=10, gt=0, le=30)
    mail_enabled: bool = False
    mail_encryption_key: SecretStr | None = None
    mfa_encryption_key: SecretStr | None = None
    require_mfa: bool = False
    smtp_host: str | None = Field(default=None, min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: SecretStr | None = None
    smtp_from: EmailStr | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=15, gt=0, le=30)
    smtp_conversation_seconds: float = Field(default=45, gt=0, le=120)
    web_dist: Path = Path(__file__).resolve().parents[2] / "dist"

    @field_validator(
        "typesafe_base_url",
        "typesafe_api_key",
        "provider_url",
        "provider_key",
        "redis_url",
        "mail_encryption_key",
        "mfa_encryption_key",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        mode="before",
    )
    @classmethod
    def empty_optional_configuration(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def production_requirements(self):
        origin = urlsplit(self.origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.path not in {"", "/"}
        ):
            raise ValueError("Origin must be an HTTP(S) origin without a path")
        self.origin = self.origin.rstrip("/")
        if origin.username or origin.password or origin.query or origin.fragment:
            raise ValueError("Origin must not contain credentials, query parameters, or a fragment")
        if self.provider_url:
            provider = urlsplit(self.provider_url)
            if (
                provider.scheme != "https"
                or not provider.hostname
                or provider.username
                or provider.password
                or provider.query
                or provider.fragment
            ):
                raise ValueError(
                    "The model provider must be an HTTPS URL without credentials, query, or fragment"
                )
            self.provider_url = self.provider_url.rstrip("/")
        identifiers = [model.id for model in self.provider_models]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Provider model identifiers must be unique")
        alert_names = [webhook.name for webhook in self.alert_webhooks]
        if len(alert_names) != len(set(alert_names)):
            raise ValueError("Alert webhook names must be unique")
        if self.network_mode == "airgap" and any(item.enabled for item in self.alert_webhooks):
            raise ValueError("Alert webhooks cannot be enabled in airgap mode")
        if self.mail_encryption_key:
            try:
                key = base64.b64decode(
                    self.mail_encryption_key.get_secret_value(), altchars=b"-_", validate=True
                )
            except (ValueError, binascii.Error) as error:
                raise ValueError("Mail encryption key must be URL-safe base64") from error
            if len(key) != 32:
                raise ValueError("Mail encryption key must decode to exactly 32 bytes")
        if self.mfa_encryption_key:
            try:
                key = base64.b64decode(
                    self.mfa_encryption_key.get_secret_value(), altchars=b"-_", validate=True
                )
            except (ValueError, binascii.Error) as error:
                raise ValueError("MFA encryption key must be URL-safe base64") from error
            if len(key) != 32:
                raise ValueError("MFA encryption key must decode to exactly 32 bytes")
        if self.require_mfa and not self.mfa_encryption_key:
            raise ValueError(
                "An installation-wide authenticator requirement needs the MFA encryption key"
            )
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP username and password must be configured together")
        if self.smtp_conversation_seconds < self.smtp_timeout_seconds:
            raise ValueError(
                "The SMTP conversation bound must be at least the single-operation timeout"
            )
        if self.smtp_host and (
            any(character in self.smtp_host for character in "/@?#")
            or any(character.isspace() or ord(character) < 32 for character in self.smtp_host)
            or (":" in self.smtp_host and self.smtp_host != "::1")
        ):
            raise ValueError("SMTP host must be a hostname or the ::1 loopback address")
        if self.mail_enabled:
            if self.network_mode == "airgap":
                raise ValueError("Mail delivery cannot be enabled in airgap mode")
            if not self.mail_encryption_key or not self.smtp_host or not self.smtp_from:
                raise ValueError("Enabled mail requires encryption key, SMTP host, and from address")
            loopback = self.smtp_host in {"localhost", "127.0.0.1", "::1"}
            if not self.smtp_starttls and (self.env == "production" or not loopback):
                raise ValueError("SMTP requires STARTTLS except for loopback development/test servers")
        if self.env == "production":
            if origin.scheme != "https" or origin.hostname in {"localhost", "127.0.0.1"}:
                raise ValueError("Production requires a configured HTTPS origin")
            if not self.redis_url:
                raise ValueError("Production requires Redis for distributed request limits")
            database = urlsplit(self.database_url.get_secret_value())
            if not database.scheme.startswith("postgresql") or not database.password:
                raise ValueError(
                    "Production requires an explicit authenticated PostgreSQL connection"
                )
        return self
