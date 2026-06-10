from __future__ import annotations

import pytest
from pydantic import SecretStr
from pydantic import ValidationError

from payment_gateway.config import Settings
from payment_gateway.models import DynamicQRCreate, TransactionDetail, WebhookPayload


def test_dynamic_qr_metadata_limits() -> None:
    with pytest.raises(ValidationError):
        DynamicQRCreate(
            amount=100,
            metadata={
                "a": "1",
                "b": "2",
                "c": "3",
                "d": "4",
                "e": "5",
                "f": "6",
            },
        )


def test_dynamic_qr_metadata_value_length() -> None:
    with pytest.raises(ValidationError):
        DynamicQRCreate(amount=100, metadata={"order": "x" * 151})


def test_webhook_amount_accepts_string_tyiyn() -> None:
    payload = WebhookPayload(id="MKSA-1", status="paid", amount="100")

    assert payload.amount == 100


def test_bank_payloads_tolerate_document_type_inconsistencies() -> None:
    webhook = WebhookPayload(id=123, status=1, amount=100, metadata=["key1", "value1"])
    detail = TransactionDetail(transaction_id=456, transaction_status=1)

    assert webhook.id == "123"
    assert webhook.status == "1"
    assert webhook.metadata == ["key1", "value1"]
    assert detail.transaction_id == "456"
    assert detail.transaction_status == "1"


def test_settings_accepts_postgresql_database_url() -> None:
    settings = Settings(
        mkassa_api_key=SecretStr("secret"),
        database_url="postgresql+psycopg://user:password@localhost:5432/payments",
    )

    assert settings.database_url.startswith("postgresql+psycopg://")


def test_settings_maps_integration_to_provider() -> None:
    settings = Settings(
        mkassa_api_key=SecretStr("secret"),
        odengi_sid="8087710950",
        odengi_password=SecretStr("odengi-secret"),
        integration_keys=SecretStr("mkassa:mkassa-secret,odengi:odengi-secret"),
        payment_provider_by_integration="odengi:odengi",
    )

    assert settings.provider_for_integration("odengi") == "odengi"
    assert settings.provider_for_integration("mkassa") == "mkassa"
