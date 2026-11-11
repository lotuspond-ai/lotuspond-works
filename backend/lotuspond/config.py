import base64
import binascii
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    fleet_enabled: bool = False
    fleet_installation_id: str | None = None
    # The operator authority directory (`python -m lotuspond.authority`). With it and
    # the fleet enabled, the API builds its execution services; without it they refuse.
    authority_dir: str | None = None
    session_hours: int = Field(default=24, ge=1, le=168)
    max_request_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    mail_enabled: bool = False
    mail_encryption_key: SecretStr | None = None
    mfa_encryption_key: SecretStr | None = None
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
        if self.fleet_enabled or self.fleet_installation_id is not None:
            from lotuspond_runtime.protocol import OpaqueId
            from pydantic import TypeAdapter

            try:
                TypeAdapter(OpaqueId).validate_python(self.fleet_installation_id, strict=True)
            except ValueError:
                raise ValueError("Fleet requires a valid installation ID") from None
        if self.authority_dir is not None and (
            not self.fleet_enabled or not self.authority_dir.startswith("/")
        ):
            raise ValueError("The authority directory needs the fleet and an absolute path")
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
            if not self.mail_encryption_key or not self.smtp_host or not self.smtp_from:
                raise ValueError(
                    "Enabled mail requires encryption key, SMTP host, and from address"
                )
            loopback = self.smtp_host in {"localhost", "127.0.0.1", "::1"}
            if not self.smtp_starttls and (self.env == "production" or not loopback):
                raise ValueError(
                    "SMTP requires STARTTLS except for loopback development/test servers"
                )
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
