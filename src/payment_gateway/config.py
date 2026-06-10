from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROVIDER_MKASSA = "mkassa"
PROVIDER_ODENGI = "odengi"
SUPPORTED_PAYMENT_PROVIDERS = {PROVIDER_MKASSA, PROVIDER_ODENGI}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field("development", alias="APP_ENV")
    default_payment_provider: str = Field(PROVIDER_MKASSA, alias="DEFAULT_PAYMENT_PROVIDER")
    payment_provider_by_integration: str | None = Field(
        None,
        alias="PAYMENT_PROVIDER_BY_INTEGRATION",
        description="Comma-separated integration_name:provider pairs.",
    )
    mkassa_base_url: str = Field("https://api.mkassa.kg", alias="MKASSA_BASE_URL")
    mkassa_api_key: SecretStr | None = Field(None, alias="MKASSA_API_KEY")
    odengi_base_url: str = Field(
        "https://mw-api-test.dengi.kg/api/json/json.php",
        alias="ODENGI_BASE_URL",
    )
    odengi_sid: str | None = Field(None, alias="ODENGI_SID")
    odengi_password: SecretStr | None = Field(None, alias="ODENGI_PASSWORD")
    odengi_api_version: int = Field(1005, alias="ODENGI_API_VERSION")
    odengi_lang: str = Field("ru", alias="ODENGI_LANG")
    odengi_test: int = Field(1, alias="ODENGI_TEST", ge=0, le=1)
    odengi_currency: str = Field("KGS", alias="ODENGI_CURRENCY")
    odengi_result_url: str | None = Field(None, alias="ODENGI_RESULT_URL")
    integration_keys: SecretStr | None = Field(None, alias="INTEGRATION_KEYS")
    payment_admin_api_key: SecretStr | None = Field(None, alias="PAYMENT_ADMIN_API_KEY")
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

    @field_validator("default_payment_provider")
    @classmethod
    def validate_default_payment_provider(cls, value: str) -> str:
        return cls._normalize_provider_name(value)

    @field_validator("mkassa_api_key")
    @classmethod
    def validate_mkassa_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("MKASSA_API_KEY must not be empty")
        return value

    @field_validator("odengi_base_url", "odengi_result_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized.rstrip("/") if value != normalized else normalized

    @field_validator("odengi_sid")
    @classmethod
    def normalize_odengi_sid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("odengi_password")
    @classmethod
    def validate_odengi_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("ODENGI_PASSWORD must not be empty")
        return value

    @field_validator("odengi_lang")
    @classmethod
    def normalize_odengi_lang(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"ru", "en"}:
            raise ValueError("ODENGI_LANG must be ru or en")
        return normalized

    @field_validator("odengi_currency")
    @classmethod
    def normalize_odengi_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3:
            raise ValueError("ODENGI_CURRENCY must be a 3-letter code")
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        supported_prefixes = ("sqlite:///", "postgresql+psycopg://")
        if not value.startswith(supported_prefixes):
            raise ValueError(
                "DATABASE_URL must start with sqlite:/// or postgresql+psycopg://"
            )
        return value

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> Settings:
        required = {self.default_payment_provider, *self.integration_provider_map.values()}
        if PROVIDER_MKASSA in required and self.mkassa_api_key is None:
            raise ValueError("MKASSA_API_KEY is required when mkassa provider is enabled")
        if PROVIDER_ODENGI in required and (
            self.odengi_sid is None or self.odengi_password is None
        ):
            raise ValueError("ODENGI_SID and ODENGI_PASSWORD are required when odengi provider is enabled")
        return self

    @property
    def database_path(self) -> Path:
        raw_path = self.database_url.removeprefix("sqlite:///")
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    @property
    def mkassa_authorization_header(self) -> str:
        if self.mkassa_api_key is None:
            raise ValueError("MKASSA_API_KEY is not configured")
        api_key = self.mkassa_api_key.get_secret_value().strip()
        if api_key.lower().startswith("api-key "):
            return api_key
        return f"api-key {api_key}"

    @property
    def odengi_password_value(self) -> str:
        if self.odengi_password is None:
            raise ValueError("ODENGI_PASSWORD is not configured")
        return self.odengi_password.get_secret_value().strip()

    @property
    def integration_key_pool(self) -> dict[str, str]:
        if self.integration_keys is not None:
            raw_pool = self.integration_keys.get_secret_value().strip()
            if raw_pool:
                return self._parse_key_pool(raw_pool)
        return {}

    @property
    def integration_provider_map(self) -> dict[str, str]:
        if self.payment_provider_by_integration:
            return self._parse_provider_map(self.payment_provider_by_integration)
        return {}

    def provider_for_integration(self, integration_name: str | None) -> str:
        if integration_name:
            mapped = self.integration_provider_map.get(integration_name)
            if mapped:
                return mapped
        return self.default_payment_provider

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

    @classmethod
    def _parse_provider_map(cls, raw_map: str) -> dict[str, str]:
        provider_map: dict[str, str] = {}
        for item in raw_map.split(","):
            entry = item.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    "PAYMENT_PROVIDER_BY_INTEGRATION must use integration_name:provider pairs"
                )
            integration_name, provider = entry.split(":", 1)
            integration_name = integration_name.strip()
            provider = cls._normalize_provider_name(provider)
            if not integration_name:
                raise ValueError("PAYMENT_PROVIDER_BY_INTEGRATION contains an empty integration_name")
            provider_map[integration_name] = provider
        return provider_map

    @staticmethod
    def _normalize_provider_name(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_PAYMENT_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PAYMENT_PROVIDERS))
            raise ValueError(f"Unsupported payment provider '{value}'. Supported: {supported}")
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
