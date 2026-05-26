from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field("development", alias="APP_ENV")
    default_payment_provider: str = Field("mkassa", alias="DEFAULT_PAYMENT_PROVIDER")
    mkassa_base_url: str = Field("https://api.mkassa.kg", alias="MKASSA_BASE_URL")
    mkassa_api_key: SecretStr = Field(..., alias="MKASSA_API_KEY")
    integration_keys: SecretStr | None = Field(None, alias="INTEGRATION_KEYS")
    payment_admin_api_key: SecretStr | None = Field(None, alias="PAYMENT_ADMIN_API_KEY")
    webhook_shared_secret: SecretStr | None = Field(None, alias="WEBHOOK_SHARED_SECRET")
    database_url: str = Field("sqlite:///./data/payment_gateway.db", alias="DATABASE_URL")
    auto_create_schema: bool = Field(True, alias="AUTO_CREATE_SCHEMA")
    request_timeout_connect: float = Field(5.0, alias="REQUEST_TIMEOUT_CONNECT", gt=0)
    request_timeout_read: float = Field(20.0, alias="REQUEST_TIMEOUT_READ", gt=0)
    request_timeout_write: float = Field(10.0, alias="REQUEST_TIMEOUT_WRITE", gt=0)
    request_timeout_pool: float = Field(5.0, alias="REQUEST_TIMEOUT_POOL", gt=0)
    request_max_retries: int = Field(2, alias="REQUEST_MAX_RETRIES", ge=0, le=5)
    request_retry_base_seconds: float = Field(0.3, alias="REQUEST_RETRY_BASE_SECONDS", ge=0)

    @field_validator("mkassa_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("MKASSA_BASE_URL must not be empty")
        return normalized

    @field_validator("mkassa_api_key")
    @classmethod
    def validate_mkassa_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("MKASSA_API_KEY must not be empty")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        supported_prefixes = ("sqlite:///", "postgresql+psycopg://")
        if not value.startswith(supported_prefixes):
            raise ValueError(
                "DATABASE_URL must start with sqlite:/// or postgresql+psycopg://"
            )
        return value

    @property
    def database_path(self) -> Path:
        raw_path = self.database_url.removeprefix("sqlite:///")
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    @property
    def mkassa_authorization_header(self) -> str:
        api_key = self.mkassa_api_key.get_secret_value().strip()
        if api_key.lower().startswith("api-key "):
            return api_key
        return f"api-key {api_key}"

    @property
    def integration_key_pool(self) -> dict[str, str]:
        if self.integration_keys is not None:
            raw_pool = self.integration_keys.get_secret_value().strip()
            if raw_pool:
                return self._parse_key_pool(raw_pool)
        return {}

    @staticmethod
    def _parse_key_pool(raw_pool: str) -> dict[str, str]:
        pool: dict[str, str] = {}
        for item in raw_pool.split(","):
            entry = item.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    "INTEGRATION_KEYS must use integration_name:key pairs separated by commas"
                )
            integration_name, key = entry.split(":", 1)
            integration_name = integration_name.strip()
            key = key.strip()
            if not integration_name or not key:
                raise ValueError("INTEGRATION_KEYS contains an empty integration_name or key")
            pool[integration_name] = key
        return pool


@lru_cache
def get_settings() -> Settings:
    return Settings()
