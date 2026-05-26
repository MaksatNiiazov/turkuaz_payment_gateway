from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from payment_gateway.api import create_app
from payment_gateway.config import Settings
from payment_gateway.models import CancelResponse, DynamicQRResponse, StaticQRResponse, Transaction
from payment_gateway.store import SQLitePaymentStore


class FakeMKassaClient:
    def __init__(self) -> None:
        self.last_dynamic_payload = None
        self.last_static_payload = None
        self.canceled_transaction_id = None
        self.transaction_statuses: dict[str, str] = {}

    async def create_dynamic_qr(self, payload):
        self.last_dynamic_payload = payload
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

    async def create_static_qr(self, payload):
        self.last_static_payload = payload
        return StaticQRResponse(
            id=1,
            static_qr_link="https://app.mbank.kg/qr#static",
            branch=payload.branch,
            cashier=payload.cashier,
            amount=payload.amount,
            change_amount=payload.change_amount,
            metadata=payload.metadata,
        )

    async def cancel_transaction(self, transaction_id: str):
        self.canceled_transaction_id = transaction_id
        return CancelResponse(transaction_id=transaction_id, message="canceled")

    async def get_transaction(self, transaction_id: str):
        return Transaction(
            id=transaction_id,
            amount=100,
            status=self.transaction_statuses.get(transaction_id, "inited"),
            transaction_type="qr",
        )

    async def aclose(self) -> None:
        return None


def make_settings(db_path: Path) -> Settings:
    return Settings(
        mkassa_api_key=SecretStr("secret"),
        integration_keys=SecretStr("pos:pos-secret,erp:erp-secret"),
        payment_admin_api_key=SecretStr("admin-secret"),
        webhook_shared_secret=SecretStr("hook-secret"),
        database_url=f"sqlite:///{db_path}",
    )


def test_integration_key_protects_control_endpoints(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
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


def test_backend_demo_page_is_not_registered(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get("/demo")

    assert response.status_code == 404


def test_admin_pages_render(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        transactions = client.get("/ui/transactions")
        webhooks = client.get("/ui/webhooks")
        access_events = client.get("/ui/access-events")

    assert transactions.status_code == 200
    assert "Payment Gateway Admin" in transactions.text
    assert "/api/v1/local/transactions" in transactions.text
    assert webhooks.status_code == 200
    assert "Webhook события" in webhooks.text
    assert access_events.status_code == 200
    assert "Доступы" in access_events.text


def test_dynamic_qr_form_builds_payload_from_fields(tmp_path: Path) -> None:
    fake_client = FakeMKassaClient()
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/qr/dynamic/form",
            headers={"X-Integration-Key": "pos-secret"},
            data={
                "amount": "100",
                "invoice_number": "TIGER-FACTURE-1001",
                "source": "tiger",
            },
        )

    assert response.status_code == 200
    assert fake_client.last_dynamic_payload.amount == 100
    assert fake_client.last_dynamic_payload.metadata == {
        "invoice_number": "TIGER-FACTURE-1001",
        "source": "tiger",
    }


def test_static_qr_form_builds_payload_from_fields(tmp_path: Path) -> None:
    fake_client = FakeMKassaClient()
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/qr/static/form",
            headers={"X-Integration-Key": "pos-secret"},
            data={
                "branch": "236366",
                "cashier": "130610",
                "amount": "100",
                "change_amount": "false",
                "invoice_number": "TIGER-FACTURE-1001",
                "payer_code": "12345678901234",
            },
        )

    assert response.status_code == 200
    assert fake_client.last_static_payload.branch == 236366
    assert fake_client.last_static_payload.cashier == 130610
    assert fake_client.last_static_payload.metadata == {
        "invoice_number": "TIGER-FACTURE-1001",
        "source": "tiger",
        "payer_code": "12345678901234",
    }


def test_qr_render_returns_png(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/qr/render",
            headers={"X-Integration-Key": "pos-secret"},
            params={"data": "https://app.mbank.kg/qr/#test"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_local_transactions_list_returns_saved_transactions(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "pos-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-1"}},
        )
        listed = client.get(
            "/api/v1/local/transactions",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert create.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == "MKSA-1"
    assert listed.json()[0]["metadata"] == {"invoice_number": "TIGER-1"}


def test_local_admin_endpoints_use_admin_key_not_integration_key(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        integration_key = client.get(
            "/api/v1/local/transactions",
            headers={"X-Integration-Key": "pos-secret"},
        )
        admin_key = client.get(
            "/api/v1/local/transactions",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert integration_key.status_code == 401
    assert admin_key.status_code == 200


def test_admin_can_cancel_unpaid_dynamic_qr(tmp_path: Path) -> None:
    fake_client = FakeMKassaClient()
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "pos-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-1"}},
        )
        cancel = client.put(
            "/api/v1/local/transactions/MKSA-1/cancel",
            headers={"X-Admin-Key": "admin-secret"},
        )
        local = client.get(
            "/api/v1/local/transactions/MKSA-1",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert create.status_code == 200
    assert cancel.status_code == 200
    assert cancel.json()["transaction_id"] == "MKSA-1"
    assert fake_client.canceled_transaction_id == "MKSA-1"
    assert local.json()["status"] == "canceled"


def test_admin_can_refresh_local_transaction_status(tmp_path: Path) -> None:
    fake_client = FakeMKassaClient()
    fake_client.transaction_statuses["MKSA-REFRESH"] = "failed"
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "pos-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-1"}},
        )
        # Rename the locally stored test row so the fake MKassa status map can target it.
        app.state.store.upsert_transaction(
            transaction_id="MKSA-REFRESH",
            status="inited",
            transaction_type="qr",
            amount=100,
        )
        refresh = client.put(
            "/api/v1/local/transactions/MKSA-REFRESH/refresh",
            headers={"X-Admin-Key": "admin-secret"},
        )
        local = client.get(
            "/api/v1/local/transactions/MKSA-REFRESH",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert create.status_code == 200
    assert refresh.status_code == 200
    assert refresh.json()["status"] == "failed"
    assert local.json()["status"] == "failed"


def test_admin_qr_demo_creates_dynamic_qr_and_renders_png(tmp_path: Path) -> None:
    fake_client = FakeMKassaClient()
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/admin/qr/dynamic",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "amount": 100,
                "metadata": {
                    "invoice_number": "TIGER-FACTURE-1001",
                    "source": "tiger",
                },
            },
        )
        render = client.get(
            "/api/v1/admin/qr/render",
            headers={"X-Admin-Key": "admin-secret"},
            params={"data": "https://app.mbank.kg/qr/#test"},
        )

    assert create.status_code == 200
    assert create.json()["payment_token"] == "https://app.mbank.kg/qr#abc"
    assert fake_client.last_dynamic_payload.amount == 100
    assert render.status_code == 200
    assert render.headers["content-type"] == "image/png"
    assert render.content.startswith(b"\x89PNG")


def test_admin_cannot_cancel_paid_transaction(tmp_path: Path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    store.upsert_transaction(
        transaction_id="MKSA-PAID",
        status="paid",
        transaction_type="qr",
        amount=100,
    )
    fake_client = FakeMKassaClient()
    fake_client.transaction_statuses["MKSA-PAID"] = "paid"
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=fake_client,
        store=store,
    )

    with TestClient(app) as client:
        response = client.put(
            "/api/v1/local/transactions/MKSA-PAID/cancel",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Transaction cannot be canceled from status paid"
    assert store.get_transaction("MKSA-PAID")["status"] == "paid"


def test_integration_key_pool_identifies_integration_name(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        integration = client.get(
            "/api/v1/integration",
            headers={"X-Integration-Key": "erp-secret"},
        )
        events = client.get(
            "/api/v1/local/access-events",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert integration.status_code == 200
    assert integration.json() == {"integration_name": "erp"}
    assert events.status_code == 200
    assert [event["integration_name"] for event in events.json()] == ["erp"]


def test_webhook_is_idempotent_and_does_not_require_integration_key(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(db_path),
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
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert missing_secret.status_code == 401
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert local.status_code == 200
    assert local.json()["status"] == "paid"
