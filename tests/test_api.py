from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from mbank_integration.api import create_app
from mbank_integration.config import Settings
from mbank_integration.models import DynamicQRResponse
from mbank_integration.store import SQLiteMKassaStore


class FakeMKassaClient:
    async def create_dynamic_qr(self, payload):
        return DynamicQRResponse(
            id="MKSA-1",
            amount=payload.amount,
            status="inited",
            transaction_type="qr",
            branch=payload.branch,
            cashier=payload.cashier,
            metadata=payload.metadata,
            payment_token="https://app.mbank.kg/qr#abc",
        )

    async def aclose(self) -> None:
        return None


def make_settings(db_path: Path) -> Settings:
    return Settings(
        mkassa_api_key=SecretStr("secret"),
        integration_keys=SecretStr("pos:pos-secret,erp:erp-secret"),
        webhook_shared_secret=SecretStr("hook-secret"),
        database_url=f"sqlite:///{db_path}",
    )


def test_service_api_key_protects_control_endpoints(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLiteMKassaStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        unauthorized = client.post("/api/v1/qr/dynamic", json={"amount": 100})
        authorized = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "pos-secret"},
            json={"amount": 100, "branch": 12345, "cashier": 1234},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["id"] == "MKSA-1"


def test_integration_key_pool_identifies_integration_name(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLiteMKassaStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        integration = client.get(
            "/api/v1/integration",
            headers={"X-Integration-Key": "erp-secret"},
        )
        events = client.get(
            "/api/v1/local/access-events",
            headers={"X-Integration-Key": "erp-secret"},
        )

    assert integration.status_code == 200
    assert integration.json() == {"integration_name": "erp"}
    assert events.status_code == 200
    assert [event["integration_name"] for event in events.json()] == ["erp"]


def test_legacy_service_api_key_header_is_still_supported(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLiteMKassaStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/integration",
            headers={"X-Service-API-Key": "pos-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"integration_name": "pos"}


def test_webhook_is_idempotent_and_does_not_require_service_api_key(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=SQLiteMKassaStore(db_path),
    )
    payload = {
        "id": "MKSA-2",
        "status": "paid",
        "amount": "100",
        "created_at": "2026-02-13T12:00:00+06:00",
        "paid_at": "2026-02-13T12:00:05+06:00",
        "metadata": {"order_id": "ORD-2"},
    }

    with TestClient(app) as client:
        missing_secret = client.post("/api/v1/webhooks/mkassa", json=payload)
        first = client.post("/api/v1/webhooks/mkassa?secret=hook-secret", json=payload)
        second = client.post("/api/v1/webhooks/mkassa?secret=hook-secret", json=payload)
        local = client.get(
            "/api/v1/local/transactions/MKSA-2",
            headers={"X-Integration-Key": "pos-secret"},
        )

    assert missing_secret.status_code == 401
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert local.status_code == 200
    assert local.json()["status"] == "paid"
