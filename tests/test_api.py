from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from payment_gateway.api import create_app
from payment_gateway.config import Settings
from payment_gateway.models import (
    BranchListResponse,
    CancelResponse,
    DynamicQRResponse,
    StaticQRResponse,
    Transaction,
    TransactionDetailListResponse,
    TransactionListResponse,
)
from payment_gateway.providers.base import PaymentProvider
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


class FakeProvider(PaymentProvider):
    def __init__(self, name: str, transaction_id: str) -> None:
        self.name = name
        self.transaction_id = transaction_id
        self.last_dynamic_payload = None
        self.canceled_transaction_id = None
        self.canceled_transaction_ids: list[str] = []
        self.dynamic_create_count = 0

    async def create_dynamic_qr(self, payload):
        self.dynamic_create_count += 1
        self.last_dynamic_payload = payload
        return DynamicQRResponse(
            id=self.transaction_id,
            amount=payload.amount,
            status="waiting",
            transaction_type="qr",
            metadata=payload.metadata,
            payment_token=f"https://example.com/{self.name}/qr",
        )

    async def create_static_qr(self, payload):
        return StaticQRResponse(
            id=self.transaction_id,
            static_qr_link=f"https://example.com/{self.name}/static",
            amount=payload.amount,
            change_amount=payload.change_amount,
            metadata=payload.metadata,
        )

    async def get_transaction(self, transaction_id: str):
        return Transaction(id=transaction_id, status="paid", transaction_type="qr", amount=100)

    async def cancel_transaction(self, transaction_id: str):
        self.canceled_transaction_id = transaction_id
        self.canceled_transaction_ids.append(transaction_id)
        return CancelResponse(transaction_id=transaction_id, message="canceled")

    async def list_transactions(self, **_: object):
        return TransactionListResponse(count=0, results=[])

    async def transaction_details(self, **_: object):
        return TransactionDetailListResponse(count=0, results=[])

    async def branches(self, **_: object):
        return BranchListResponse(count=0, results=[])


def make_settings(db_path: Path) -> Settings:
    return Settings(
        mkassa_api_key=SecretStr("secret"),
        integration_keys=SecretStr("pos:pos-secret,1c:1c-secret,tiger:tiger-secret"),
        payment_admin_api_key=SecretStr("admin-secret"),
        database_url=f"sqlite:///{db_path}",
    )


def make_multi_provider_settings(db_path: Path) -> Settings:
    return Settings(
        mkassa_api_key=SecretStr("secret"),
        odengi_sid="8087710950",
        odengi_password=SecretStr("odengi-secret"),
        integration_keys=SecretStr(
            "mkassa:mkassa-secret,odengi:odengi-secret,1c:1c-secret,tiger:tiger-secret"
        ),
        payment_provider_by_integration="odengi:odengi",
        payment_admin_api_key=SecretStr("admin-secret"),
        database_url=f"sqlite:///{db_path}",
    )


def seed_waiting_transaction(
    store: SQLitePaymentStore,
    transaction_id: str,
    *,
    amount: int = 100,
    invoice_id: str | None = None,
    metadata: dict | None = None,
    provider: str = "mkassa",
) -> None:
    store.initialize()
    store.upsert_transaction(
        transaction_id=transaction_id,
        status="waiting",
        transaction_type="qr",
        amount=amount,
        external_invoice_id=invoice_id,
        metadata=metadata,
        raw_payload={"id": transaction_id, "metadata": metadata or {}},
        provider=provider,
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


def test_integration_keys_are_required_to_enable_control_endpoints(tmp_path: Path) -> None:
    settings = Settings(
        mkassa_api_key=SecretStr("secret"),
        integration_keys=None,
        payment_admin_api_key=SecretStr("admin-secret"),
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
    )
    app = create_app(
        settings=settings,
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/integration")

    assert response.status_code == 503
    assert response.json()["detail"] == "Integration keys are not configured"


def test_service_health_endpoints_do_not_require_integration_key(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        ready = client.get("/api/v1/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_swagger_exposes_only_public_integration_key_scheme(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    openapi = app.openapi()

    assert sorted(openapi["components"]["securitySchemes"]) == ["X-Integration-Key"]
    assert openapi["paths"]["/api/v1/qr/dynamic"]["post"]["security"] == [
        {"X-Integration-Key": []}
    ]
    assert openapi["paths"]["/health"]["get"].get("security") is None
    assert openapi["paths"]["/api/v1/webhooks/mkassa"]["post"].get("security") is None
    assert "/api/v1/local/transactions" not in openapi["paths"]


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
                "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                "invoice_number": "TIGER-FACTURE-1001",
                "source": "tiger",
            },
        )

    assert response.status_code == 200
    assert fake_client.last_dynamic_payload.amount == 100
    assert fake_client.last_dynamic_payload.metadata == {
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
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
                "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                "invoice_number": "TIGER-FACTURE-1001",
                "payer_code": "12345678901234",
            },
        )

    assert response.status_code == 200
    assert fake_client.last_static_payload.branch == 236366
    assert fake_client.last_static_payload.cashier == 130610
    assert fake_client.last_static_payload.metadata == {
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
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
            json={
                "amount": 100,
                "metadata": {
                    "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                    "invoice_number": "TIGER-1",
                },
            },
        )
        filtered = client.get(
            "/api/v1/local/transactions",
            headers={"X-Admin-Key": "admin-secret"},
            params={"invoice_id": "550e8400-e29b-41d4-a716-446655440000"},
        )
        listed = client.get(
            "/api/v1/local/transactions",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert create.status_code == 200
    assert filtered.status_code == 200
    assert listed.status_code == 200
    assert filtered.json()[0]["id"] == "MKSA-1"
    assert filtered.json()[0]["external_invoice_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert listed.json()[0]["id"] == "MKSA-1"
    assert listed.json()[0]["external_invoice_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert listed.json()[0]["metadata"] == {
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
        "invoice_number": "TIGER-1",
    }


def test_admin_print_qr_codes_can_be_reordered_and_renamed(tmp_path: Path) -> None:
    app = create_app(
        settings=make_multi_provider_settings(tmp_path / "app.db"),
        providers=[
            FakeProvider("mkassa", "MBANK-1"),
            FakeProvider("odengi", "OBANK-1"),
        ],
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        defaults = client.get(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
        )
        saved = client.put(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "items": [
                    {
                        "code": "obank",
                        "label": "О!Банк",
                        "provider": "odengi",
                        "enabled": True,
                        "slot": 2,
                        "sort_order": 10,
                        "tiger_bank_account_code": "102.OBANK",
                    },
                    {
                        "code": "mbank",
                        "label": "MBank QR",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 1,
                        "sort_order": 20,
                        "tiger_bank_account_code": "102.MBANK",
                    },
                    {
                        "code": "qr_3",
                        "label": "QR 3",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 3,
                        "sort_order": 30,
                        "tiger_bank_account_code": None,
                    },
                    {
                        "code": "qr_4",
                        "label": "QR 4",
                        "provider": "odengi",
                        "enabled": False,
                        "slot": 4,
                        "sort_order": 40,
                        "tiger_bank_account_code": None,
                    },
                ]
            },
        )

    assert defaults.status_code == 200
    assert [item["code"] for item in defaults.json()] == ["mbank", "obank", "qr_3", "qr_4"]
    assert saved.status_code == 200
    assert saved.json() == [
        {
            "code": "obank",
            "label": "О!Банк",
            "provider": "odengi",
            "enabled": True,
            "slot": 2,
            "sort_order": 10,
            "tiger_bank_account_code": "102.OBANK",
        },
        {
            "code": "mbank",
            "label": "MBank QR",
            "provider": "mkassa",
            "enabled": False,
            "slot": 1,
            "sort_order": 20,
            "tiger_bank_account_code": "102.MBANK",
        },
        {
            "code": "qr_3",
            "label": "QR 3",
            "provider": "mkassa",
            "enabled": False,
            "slot": 3,
            "sort_order": 30,
            "tiger_bank_account_code": None,
        },
        {
            "code": "qr_4",
            "label": "QR 4",
            "provider": "odengi",
            "enabled": False,
            "slot": 4,
            "sort_order": 40,
            "tiger_bank_account_code": None,
        },
    ]


def test_invoice_qr_codes_endpoint_uses_config_and_reuses_existing_qr(tmp_path: Path) -> None:
    mkassa = FakeProvider("mkassa", "MBANK-1")
    odengi = FakeProvider("odengi", "OBANK-1")
    app = create_app(
        settings=make_multi_provider_settings(tmp_path / "app.db"),
        providers=[mkassa, odengi],
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    payload = {
        "amount": 1500000,
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
        "invoice_number": "TIGER-1001",
        "client_code": "120.TEST.001",
    }
    with TestClient(app) as client:
        configured = client.put(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "items": [
                    {
                        "code": "obank",
                        "label": "О!Банк",
                        "provider": "odengi",
                        "enabled": True,
                        "slot": 2,
                        "sort_order": 10,
                        "tiger_bank_account_code": "102.OBANK",
                    },
                    {
                        "code": "mbank",
                        "label": "MBank",
                        "provider": "mkassa",
                        "enabled": True,
                        "slot": 1,
                        "sort_order": 20,
                        "tiger_bank_account_code": "102.MBANK",
                    },
                    {
                        "code": "qr_3",
                        "label": "QR 3",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 3,
                        "sort_order": 30,
                    },
                    {
                        "code": "qr_4",
                        "label": "QR 4",
                        "provider": "odengi",
                        "enabled": False,
                        "slot": 4,
                        "sort_order": 40,
                    },
                ]
            },
        )
        first = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json=payload,
        )
        second = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json=payload,
        )

    assert configured.status_code == 200
    assert first.status_code == 200
    assert second.status_code == 200
    assert [item["code"] for item in first.json()["items"]] == ["obank", "mbank"]
    assert [item["slot"] for item in first.json()["items"]] == [2, 1]
    assert [item["label"] for item in first.json()["items"]] == ["О!Банк", "MBank"]
    assert [item["transaction_id"] for item in first.json()["items"]] == ["OBANK-1", "MBANK-1"]
    assert [item["reused"] for item in first.json()["items"]] == [False, False]
    assert [item["reused"] for item in second.json()["items"]] == [True, True]
    assert odengi.dynamic_create_count == 1
    assert mkassa.dynamic_create_count == 1
    assert odengi.last_dynamic_payload.is_long_living is None
    assert mkassa.last_dynamic_payload.is_long_living is True
    assert odengi.last_dynamic_payload.metadata == {
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
        "invoice_number": "TIGER-1001",
        "print_qr_code": "obank",
        "client_code": "120.TEST.001",
        "tiger_bank_account_code": "102.OBANK",
    }


def test_invoice_qr_reuse_refreshes_existing_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    store.initialize()
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    store.upsert_transaction(
        transaction_id=invoice_id,
        status="waiting",
        transaction_type="qr",
        amount=1500000,
        external_invoice_id=invoice_id,
        metadata={
            "invoice_id": invoice_id,
            "invoice_number": "TIGER-1001",
            "print_qr_code": "obank",
            "print_qr_slot": "2",
            "source": "1c",
        },
        raw_payload={
            "id": invoice_id,
            "metadata": {
                "invoice_id": invoice_id,
                "print_qr_code": "obank",
                "print_qr_slot": "2",
                "source": "1c",
            },
        },
        provider="odengi",
    )
    mkassa = FakeProvider("mkassa", "MBANK-1")
    odengi = FakeProvider("odengi", "OBANK-1")
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[mkassa, odengi],
        store=store,
    )

    with TestClient(app) as client:
        configured = client.put(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "items": [
                    {
                        "code": "obank",
                        "label": "О!Банк",
                        "provider": "odengi",
                        "enabled": True,
                        "slot": 2,
                        "sort_order": 10,
                        "tiger_bank_account_code": "102.OBANK",
                    },
                    {
                        "code": "mbank",
                        "label": "MBank",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 1,
                        "sort_order": 20,
                        "tiger_bank_account_code": None,
                    },
                    {
                        "code": "qr_3",
                        "label": "QR 3",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 3,
                        "sort_order": 30,
                        "tiger_bank_account_code": None,
                    },
                    {
                        "code": "qr_4",
                        "label": "QR 4",
                        "provider": "odengi",
                        "enabled": False,
                        "slot": 4,
                        "sort_order": 40,
                        "tiger_bank_account_code": None,
                    },
                ]
            },
        )
        response = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json={
                "amount": 1500000,
                "invoice_id": invoice_id,
                "invoice_number": "TIGER-1001",
                "client_code": "120.TEST.001",
            },
        )
        saved = client.get(
            f"/api/v1/local/transactions/{invoice_id}",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert configured.status_code == 200
    assert response.status_code == 200
    assert response.json()["items"][0]["reused"] is True
    assert odengi.dynamic_create_count == 0
    assert saved.status_code == 200
    assert saved.json()["metadata"] == {
        "invoice_id": invoice_id,
        "invoice_number": "TIGER-1001",
        "print_qr_code": "obank",
        "print_qr_slot": "2",
        "source": "1c",
        "client_code": "120.TEST.001",
        "tiger_bank_account_code": "102.OBANK",
    }
    assert saved.json()["raw_payload"]["metadata"]["client_code"] == "120.TEST.001"
    assert saved.json()["raw_payload"]["metadata"]["tiger_bank_account_code"] == "102.OBANK"


def test_invoice_qr_for_paid_invoice_returns_only_paid_codes(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    store.initialize()
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    store.upsert_transaction(
        transaction_id="MBANK-PAID",
        status="paid",
        transaction_type="qr",
        amount=1500000,
        external_invoice_id=invoice_id,
        metadata={
            "invoice_id": invoice_id,
            "invoice_number": "TIGER-1001",
            "print_qr_code": "mbank",
        },
        raw_payload={
            "id": "MBANK-PAID",
            "metadata": {
                "invoice_id": invoice_id,
                "print_qr_code": "mbank",
            },
        },
        provider="mkassa",
    )
    store.upsert_transaction(
        transaction_id="OBANK-WAITING",
        status="waiting",
        transaction_type="qr",
        amount=1500000,
        external_invoice_id=invoice_id,
        metadata={
            "invoice_id": invoice_id,
            "invoice_number": "TIGER-1001",
            "print_qr_code": "obank",
        },
        raw_payload={"id": "OBANK-WAITING"},
        provider="odengi",
    )
    mkassa = FakeProvider("mkassa", "MBANK-NEW")
    odengi = FakeProvider("odengi", "OBANK-NEW")
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[mkassa, odengi],
        store=store,
    )

    with TestClient(app) as client:
        configured = client.put(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "items": [
                    {
                        "code": "mbank",
                        "label": "MBank",
                        "provider": "mkassa",
                        "enabled": True,
                        "slot": 1,
                        "sort_order": 10,
                        "tiger_bank_account_code": "102.MBANK",
                    },
                    {
                        "code": "obank",
                        "label": "О!Банк",
                        "provider": "odengi",
                        "enabled": True,
                        "slot": 2,
                        "sort_order": 20,
                        "tiger_bank_account_code": "102.OBANK",
                    },
                    {
                        "code": "qr_3",
                        "label": "QR 3",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 3,
                        "sort_order": 30,
                        "tiger_bank_account_code": None,
                    },
                    {
                        "code": "qr_4",
                        "label": "QR 4",
                        "provider": "odengi",
                        "enabled": False,
                        "slot": 4,
                        "sort_order": 40,
                        "tiger_bank_account_code": None,
                    },
                ]
            },
        )
        response = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json={
                "amount": 1500000,
                "invoice_id": invoice_id,
                "invoice_number": "TIGER-1001",
                "client_code": "120.TEST.001",
            },
        )
        paid = client.get(
            "/api/v1/local/transactions/MBANK-PAID",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert configured.status_code == 200
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "code": "mbank",
            "label": "MBank",
            "provider": "mkassa",
            "slot": 1,
            "transaction_id": "MBANK-PAID",
            "status": "paid",
            "amount": 1500000,
            "image_path": "/api/v1/qr/render/transaction/MBANK-PAID",
            "reused": True,
        }
    ]
    assert mkassa.dynamic_create_count == 0
    assert odengi.dynamic_create_count == 0
    assert paid.json()["metadata"]["client_code"] == "120.TEST.001"
    assert paid.json()["metadata"]["tiger_bank_account_code"] == "102.MBANK"


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


def test_local_admin_endpoints_accept_identity_bearer_token(tmp_path: Path, monkeypatch) -> None:
    verified_tokens: list[str] = []

    async def fake_verify_identity_admin_token(settings: Settings, token: str) -> None:
        verified_tokens.append(token)

    monkeypatch.setattr(
        "payment_gateway.api.verify_identity_admin_token",
        fake_verify_identity_admin_token,
    )
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/local/transactions",
            headers={"Authorization": "Bearer identity-token"},
        )

    assert response.status_code == 200
    assert verified_tokens == ["identity-token"]


def test_local_admin_endpoints_are_open_when_admin_key_is_not_configured(tmp_path: Path) -> None:
    settings = Settings(
        mkassa_api_key=SecretStr("secret"),
        integration_keys=SecretStr("pos:pos-secret"),
        payment_admin_api_key=None,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
    )
    app = create_app(
        settings=settings,
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/local/transactions")

    assert response.status_code == 200


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
                "branch": 236366,
                "cashier": 130610,
                "metadata": {
                    "invoice_number": "TIGER-FACTURE-1001",
                    "source": "tiger",
                    "payer_code": "12345678901234",
                    "payer_full_name": "ОсОО Тест",
                    "tiger_facture_id": "TF-1001",
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
    assert fake_client.last_dynamic_payload.branch == 236366
    assert fake_client.last_dynamic_payload.cashier == 130610
    assert fake_client.last_dynamic_payload.metadata == {
        "invoice_number": "TIGER-FACTURE-1001",
        "source": "tiger",
        "payer_code": "12345678901234",
        "payer_full_name": "ОсОО Тест",
        "tiger_facture_id": "TF-1001",
    }
    assert render.status_code == 200
    assert render.headers["content-type"] == "image/png"
    assert render.content.startswith(b"\x89PNG")


def test_integration_can_render_saved_qr_by_transaction_id(tmp_path: Path) -> None:
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
        unauthorized = client.get("/api/v1/qr/render/transaction/MKSA-1")
        render = client.get(
            "/api/v1/qr/render/transaction/MKSA-1",
            headers={"X-Integration-Key": "pos-secret"},
        )

    assert create.status_code == 200
    assert unauthorized.status_code == 401
    assert render.status_code == 200
    assert render.headers["content-type"] == "image/png"
    assert render.content.startswith(b"\x89PNG")


def test_render_saved_qr_by_transaction_id_reports_missing_transaction(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        render = client.get(
            "/api/v1/qr/render/transaction/MKSA-MISSING",
            headers={"X-Integration-Key": "pos-secret"},
        )

    assert render.status_code == 404
    assert render.json()["detail"] == "Transaction not found"


def test_render_saved_qr_by_transaction_id_requires_saved_qr_payload(tmp_path: Path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    store.upsert_transaction(
        transaction_id="MKSA-NO-QR",
        status="inited",
        transaction_type="qr",
        amount=100,
    )
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        render = client.get(
            "/api/v1/qr/render/transaction/MKSA-NO-QR",
            headers={"X-Integration-Key": "pos-secret"},
        )

    assert render.status_code == 409
    assert render.json()["detail"] == "Transaction does not have a saved QR payload"


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
            headers={"X-Integration-Key": "tiger-secret"},
        )
        events = client.get(
            "/api/v1/local/access-events",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert integration.status_code == 200
    assert integration.json() == {"integration_name": "tiger"}
    assert events.status_code == 200
    assert [event["integration_name"] for event in events.json()] == ["tiger"]


def test_integration_key_roles_limit_1c_and_tiger_queues(tmp_path: Path) -> None:
    app = create_app(
        settings=make_settings(tmp_path / "app.db"),
        client=FakeMKassaClient(),
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        one_c_with_pos = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "pos-secret"},
        )
        one_c_with_tiger = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )
        one_c_with_1c = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        tiger_with_1c = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        tiger_with_tiger = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )

    assert one_c_with_pos.status_code == 403
    assert one_c_with_tiger.status_code == 403
    assert one_c_with_1c.status_code == 200
    assert tiger_with_1c.status_code == 403
    assert tiger_with_tiger.status_code == 200


def test_webhook_is_idempotent_and_does_not_require_integration_key(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    seed_waiting_transaction(store, "MKSA-2")
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
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
        first = client.post("/api/v1/webhooks/mkassa", json=payload)
        second = client.post("/api/v1/webhooks/mkassa", json=payload)
        local = client.get(
            "/api/v1/local/transactions/MKSA-2",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert local.status_code == 200
    assert local.json()["status"] == "paid"


def test_webhook_for_unknown_transaction_is_audited_without_creating_payment(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )
    payload = {
        "id": "MKSA-UNKNOWN-WEBHOOK",
        "status": "paid",
        "amount": "100",
        "paid_at": "2026-02-13T12:00:05+06:00",
        "metadata": {"invoice_id": "550e8400-e29b-41d4-a716-446655440000"},
    }

    with TestClient(app) as client:
        webhook = client.post("/api/v1/webhooks/mkassa", json=payload)
        local = client.get(
            "/api/v1/local/transactions/MKSA-UNKNOWN-WEBHOOK",
            headers={"X-Admin-Key": "admin-secret"},
        )
        webhooks = client.get(
            "/api/v1/local/webhooks",
            headers={"X-Admin-Key": "admin-secret"},
        )
        one_c_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )

    assert webhook.status_code == 200
    assert webhook.json()["duplicate"] is False
    assert local.status_code == 404
    assert webhooks.status_code == 200
    assert webhooks.json()[0]["transaction_id"] == "MKSA-UNKNOWN-WEBHOOK"
    assert one_c_pending.status_code == 200
    assert one_c_pending.json() == []


def test_webhook_does_not_update_existing_transaction_on_amount_mismatch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-SPOOFED-AMOUNT",
        amount=100,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MKSA-SPOOFED-AMOUNT",
                "status": "paid",
                "amount": "200",
                "paid_at": "2026-02-13T12:00:05+06:00",
                "metadata": {"invoice_id": invoice_id},
            },
        )
        local = client.get(
            "/api/v1/local/transactions/MKSA-SPOOFED-AMOUNT",
            headers={"X-Admin-Key": "admin-secret"},
        )
        one_c_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )

    assert webhook.status_code == 200
    assert local.status_code == 200
    assert local.json()["status"] == "waiting"
    assert one_c_pending.status_code == 200
    assert one_c_pending.json() == []


def test_webhook_does_not_update_existing_transaction_on_provider_mismatch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    seed_waiting_transaction(
        store,
        "PROVIDER-MISMATCH",
        amount=100,
        provider="odengi",
    )
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[FakeProvider("mkassa", "MKSA-1"), FakeProvider("odengi", "PROVIDER-MISMATCH")],
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "PROVIDER-MISMATCH",
                "status": "paid",
                "amount": "100",
                "paid_at": "2026-02-13T12:00:05+06:00",
            },
        )
        local = client.get(
            "/api/v1/local/transactions/PROVIDER-MISMATCH",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert webhook.status_code == 200
    assert local.status_code == 200
    assert local.json()["provider"] == "odengi"
    assert local.json()["status"] == "waiting"


def test_tiger_event_preview_builds_paid_payment_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-TIGER-1",
        amount=1500000,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MKSA-TIGER-1",
                "status": "paid",
                "amount": "1500000",
                "paid_at": "2026-06-24T10:30:00+06:00",
                "metadata": {
                    "invoice_id": invoice_id,
                    "invoice_number": "TIGER-FACTURE-1001",
                    "client_code": "CARI.001",
                    "tiger_bank_account_code": "MKASSA_KGS",
                },
            },
        )
        preview = client.get(
            "/api/v1/local/transactions/MKSA-TIGER-1/tiger-event-preview",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert webhook.status_code == 200
    assert preview.status_code == 200
    assert preview.json() == {
        "invoiceId": invoice_id,
        "invoiceNumber": "TIGER-FACTURE-1001",
        "paidTransactionId": "MKSA-TIGER-1",
        "paidProvider": "mkassa",
        "providerPaymentId": "MKSA-TIGER-1",
        "targetBankCode": "MKASSA",
        "targetBankAccountCode": "MKASSA_KGS",
        "paidAt": "2026-06-24T10:30:00+06:00",
        "amountTyiyn": 1500000,
        "amount": 15000.0,
        "currency": "KGS",
        "clientCode": "CARI.001",
        "paymentMethod": "qr",
        "description": "QR payment for TIGER-FACTURE-1001",
    }


def test_paid_webhook_creates_tiger_invoice_export_event(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-TIGER-QUEUE-1",
        amount=1500000,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MKSA-TIGER-QUEUE-1",
                "status": "paid",
                "amount": "1500000",
                "paid_at": "2026-06-24T10:30:00+06:00",
                "metadata": {
                    "invoice_id": invoice_id,
                    "invoice_number": "TIGER-FACTURE-1001",
                    "client_code": "CARI.001",
                    "tiger_bank_account_code": "MKASSA_KGS",
                },
            },
        )
        pending = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )

    assert webhook.status_code == 200
    assert pending.status_code == 200
    events = pending.json()
    assert len(events) == 1
    assert events[0]["status"] == "pending"
    assert events[0]["invoice_id"] == invoice_id
    assert events[0]["paid_transaction_id"] == "MKSA-TIGER-QUEUE-1"
    assert events[0]["paid_provider"] == "mkassa"
    assert events[0]["target_bank_account_code"] == "MKASSA_KGS"
    assert events[0]["event_payload"]["invoiceId"] == invoice_id


def test_tiger_worker_can_report_success_and_admin_can_reset(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-TIGER-RESULT-1",
        amount=1500000,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MKSA-TIGER-RESULT-1",
                "status": "paid",
                "amount": "1500000",
                "paid_at": "2026-06-24T10:30:00+06:00",
                "metadata": {
                    "invoice_id": invoice_id,
                    "client_code": "CARI.001",
                    "tiger_bank_account_code": "MKASSA_KGS",
                },
            },
        )
        pending = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )
        event_id = pending.json()[0]["id"]
        result = client.post(
            f"/api/v1/local/tiger/invoice-events/{event_id}/result",
            headers={"X-Integration-Key": "tiger-secret"},
            json={
                "success": True,
                "tiger_logical_ref": "12345",
                "tiger_fiche_no": "BN-1001",
            },
        )
        after_success = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )
        mixed_statuses = client.get(
            "/api/v1/local/tiger/invoice-events?status=pending&status=success",
            headers={"X-Admin-Key": "admin-secret"},
        )
        success_status = client.get(
            "/api/v1/local/tiger/invoice-events?status=success",
            headers={"X-Admin-Key": "admin-secret"},
        )
        invalid_status = client.get(
            "/api/v1/local/tiger/invoice-events?status=unknown",
            headers={"X-Admin-Key": "admin-secret"},
        )
        reset = client.post(
            f"/api/v1/local/tiger/invoice-events/{event_id}/reset",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert result.status_code == 200
    assert result.json()["status"] == "success"
    assert result.json()["tiger_logical_ref"] == "12345"
    assert result.json()["tiger_fiche_no"] == "BN-1001"
    assert after_success.status_code == 200
    assert after_success.json() == []
    assert mixed_statuses.status_code == 200
    assert [item["status"] for item in mixed_statuses.json()] == ["success"]
    assert success_status.status_code == 200
    assert [item["status"] for item in success_status.json()] == ["success"]
    assert invalid_status.status_code == 422
    assert reset.status_code == 200
    assert reset.json()["status"] == "pending"
    assert reset.json()["tiger_logical_ref"] is None
    assert reset.json()["attempt_count"] == 1


def test_paid_webhook_with_incomplete_tiger_metadata_goes_to_error_queue(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-TIGER-INVALID-1",
        amount=1500000,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MKSA-TIGER-INVALID-1",
                "status": "paid",
                "amount": "1500000",
                "paid_at": "2026-06-24T10:30:00+06:00",
                "metadata": {"invoice_id": invoice_id},
            },
        )
        tiger_pending = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )
        tiger_errors = client.get(
            "/api/v1/local/tiger/invoice-events?status=error",
            headers={"X-Admin-Key": "admin-secret"},
        )
        one_c_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )

    assert webhook.status_code == 200
    assert tiger_pending.status_code == 200
    assert tiger_pending.json() == []
    assert tiger_errors.status_code == 200
    assert tiger_errors.json()[0]["status"] == "error"
    assert tiger_errors.json()[0]["invoice_id"] == invoice_id
    assert "targetBankAccountCode" in tiger_errors.json()[0]["error_message"]
    assert one_c_pending.status_code == 200
    assert one_c_pending.json()[0]["payment_id"] == "MKSA-TIGER-INVALID-1"


def test_one_c_can_pull_acknowledge_and_retry_paid_payment(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    seed_waiting_transaction(
        store,
        "MKSA-1C-RESULT-1",
        amount=1500000,
        invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id, "print_qr_code": "mbank"},
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        payload = {
            "id": "MKSA-1C-RESULT-1",
            "status": "paid",
            "amount": "1500000",
            "paid_at": "2026-06-30T10:30:00+06:00",
            "metadata": {
                "invoice_id": invoice_id,
                "invoice_number": "TIGER-FACTURE-1001",
                "client_code": "CARI.001",
                "print_qr_code": "mbank",
            },
        }
        first_webhook = client.post("/api/v1/webhooks/mkassa", json=payload)
        duplicate_webhook = client.post("/api/v1/webhooks/mkassa", json=payload)
        pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        event_id = pending.json()[0]["id"]
        failed_result = client.post(
            f"/api/v1/local/1c/payment-events/{event_id}/result",
            headers={"X-Integration-Key": "1c-secret"},
            json={"success": False, "error_message": "1C document is locked"},
        )
        retry_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        retry_reset = client.post(
            f"/api/v1/local/1c/payment-events/{event_id}/reset",
            headers={"X-Admin-Key": "admin-secret"},
        )
        reset_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        result = client.post(
            f"/api/v1/local/1c/payment-events/{event_id}/result",
            headers={"X-Integration-Key": "1c-secret"},
            json={"success": True, "one_c_document_id": "1C-PAYMENT-1001"},
        )
        after_success = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        admin_mixed_statuses = client.get(
            "/api/v1/local/1c/payment-events?status=pending&status=success",
            headers={"X-Admin-Key": "admin-secret"},
        )
        reset = client.post(
            f"/api/v1/local/1c/payment-events/{event_id}/reset",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert first_webhook.status_code == 200
    assert duplicate_webhook.json()["duplicate"] is True
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["payment_id"] == "MKSA-1C-RESULT-1"
    assert pending.json()[0]["invoice_id"] == invoice_id
    assert pending.json()[0]["payment_code"] == "mbank"
    assert pending.json()[0]["event_payload"] == {
        "paymentId": "MKSA-1C-RESULT-1",
        "invoiceId": invoice_id,
        "invoiceNumber": "TIGER-FACTURE-1001",
        "paymentCode": "mbank",
        "paidProvider": "mkassa",
        "providerPaymentId": "MKSA-1C-RESULT-1",
        "paidAt": "2026-06-30T10:30:00+06:00",
        "amountTyiyn": 1500000,
        "amount": 15000.0,
        "currency": "KGS",
        "clientCode": "CARI.001",
        "paymentMethod": "qr",
        "status": "paid",
    }
    assert failed_result.status_code == 200
    assert failed_result.json()["status"] == "error"
    assert failed_result.json()["attempt_count"] == 1
    assert retry_pending.json() == []
    assert retry_reset.status_code == 200
    assert retry_reset.json()["status"] == "pending"
    assert retry_reset.json()["attempt_count"] == 1
    assert reset_pending.json()[0]["id"] == event_id
    assert result.status_code == 200
    assert result.json()["status"] == "success"
    assert result.json()["one_c_document_id"] == "1C-PAYMENT-1001"
    assert result.json()["attempt_count"] == 2
    assert after_success.json() == []
    assert admin_mixed_statuses.status_code == 200
    assert [item["status"] for item in admin_mixed_statuses.json()] == ["success"]
    assert reset.status_code == 200
    assert reset.json()["status"] == "pending"
    assert reset.json()["one_c_document_id"] is None
    assert reset.json()["attempt_count"] == 2


def test_one_c_keeps_each_paid_transaction_while_tiger_keeps_one_invoice(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    for transaction_id in ("PAID-BANK-A", "PAID-BANK-B"):
        seed_waiting_transaction(
            store,
            transaction_id,
            amount=10000,
            invoice_id=invoice_id,
            metadata={"invoice_id": invoice_id},
        )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        for transaction_id, bank_account_code in (
            ("PAID-BANK-A", "BANK-A-KGS"),
            ("PAID-BANK-B", "BANK-B-KGS"),
        ):
            response = client.post(
                "/api/v1/webhooks/mkassa",
                json={
                    "id": transaction_id,
                    "status": "paid",
                    "amount": 10000,
                    "paid_at": "2026-06-24T10:30:00+06:00",
                    "metadata": {
                        "invoice_id": invoice_id,
                        "client_code": "CARI.001",
                        "tiger_bank_account_code": bank_account_code,
                    },
                },
            )
            assert response.status_code == 200

        one_c_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )
        tiger_pending = client.get(
            "/api/v1/local/tiger/invoice-events/pending",
            headers={"X-Integration-Key": "tiger-secret"},
        )

    assert {item["payment_id"] for item in one_c_pending.json()} == {
        "PAID-BANK-A",
        "PAID-BANK-B",
    }
    assert len(tiger_pending.json()) == 1
    assert tiger_pending.json()[0]["invoice_id"] == invoice_id
    assert tiger_pending.json()[0]["paid_transaction_id"] == "PAID-BANK-A"
    assert tiger_pending.json()[0]["target_bank_account_code"] == "BANK-A-KGS"


def test_invoice_qr_rejects_existing_qr_amount_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    store.initialize()
    waiting_invoice_id = "550e8400-e29b-41d4-a716-446655440001"
    paid_invoice_id = "550e8400-e29b-41d4-a716-446655440002"
    store.upsert_transaction(
        transaction_id="MBANK-WAITING-AMOUNT",
        status="waiting",
        transaction_type="qr",
        amount=10000,
        external_invoice_id=waiting_invoice_id,
        metadata={"invoice_id": waiting_invoice_id, "print_qr_code": "mbank"},
        raw_payload={"id": "MBANK-WAITING-AMOUNT"},
        provider="mkassa",
    )
    store.upsert_transaction(
        transaction_id="MBANK-PAID-AMOUNT",
        status="paid",
        transaction_type="qr",
        amount=10000,
        external_invoice_id=paid_invoice_id,
        metadata={"invoice_id": paid_invoice_id, "print_qr_code": "mbank"},
        raw_payload={"id": "MBANK-PAID-AMOUNT"},
        provider="mkassa",
    )
    mkassa = FakeProvider("mkassa", "MBANK-NEW")
    odengi = FakeProvider("odengi", "OBANK-NEW")
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[mkassa, odengi],
        store=store,
    )

    with TestClient(app) as client:
        configured = client.put(
            "/api/v1/admin/print-qr-codes",
            headers={"X-Admin-Key": "admin-secret"},
            json={
                "items": [
                    {
                        "code": "mbank",
                        "label": "MBank",
                        "provider": "mkassa",
                        "enabled": True,
                        "slot": 1,
                        "sort_order": 10,
                        "tiger_bank_account_code": "102.MBANK",
                    },
                    {
                        "code": "obank",
                        "label": "О!Банк",
                        "provider": "odengi",
                        "enabled": True,
                        "slot": 2,
                        "sort_order": 20,
                        "tiger_bank_account_code": "102.OBANK",
                    },
                    {
                        "code": "qr_3",
                        "label": "QR 3",
                        "provider": "mkassa",
                        "enabled": False,
                        "slot": 3,
                        "sort_order": 30,
                        "tiger_bank_account_code": None,
                    },
                    {
                        "code": "qr_4",
                        "label": "QR 4",
                        "provider": "odengi",
                        "enabled": False,
                        "slot": 4,
                        "sort_order": 40,
                        "tiger_bank_account_code": None,
                    },
                ]
            },
        )
        waiting = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json={
                "amount": 20000,
                "invoice_id": waiting_invoice_id,
                "invoice_number": "TIGER-WAITING-AMOUNT",
                "client_code": "120.TEST.001",
            },
        )
        paid = client.post(
            "/api/v1/invoice/qr-codes",
            headers={"X-Integration-Key": "1c-secret"},
            json={
                "amount": 20000,
                "invoice_id": paid_invoice_id,
                "invoice_number": "TIGER-PAID-AMOUNT",
                "client_code": "120.TEST.001",
            },
        )

    assert configured.status_code == 200
    assert waiting.status_code == 409
    assert "amount does not match" in waiting.json()["detail"]
    assert paid.status_code == 409
    assert "amount does not match" in paid.json()["detail"]
    assert mkassa.dynamic_create_count == 0
    assert odengi.dynamic_create_count == 0


def test_tiger_event_preview_rejects_unpaid_transaction(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    store.initialize()
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    store.upsert_transaction(
        transaction_id="MKSA-WAITING",
        status="waiting",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
        raw_payload={"id": "MKSA-WAITING"},
        provider="mkassa",
    )
    app = create_app(
        settings=make_settings(db_path),
        client=FakeMKassaClient(),
        store=store,
    )

    with TestClient(app) as client:
        preview = client.get(
            "/api/v1/local/transactions/MKSA-WAITING/tiger-event-preview",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert preview.status_code == 409
    assert preview.json()["detail"] == "Only paid invoice transactions can be exported to Tiger"


def test_paid_webhook_cancels_other_active_transaction_for_same_invoice(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    store.initialize()
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    store.upsert_transaction(
        transaction_id="MBANK-1",
        status="waiting",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id, "print_qr_code": "mbank"},
        raw_payload={"id": "MBANK-1"},
        provider="mkassa",
    )
    store.upsert_transaction(
        transaction_id="OBANK-1",
        status="waiting",
        transaction_type="qr",
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id, "print_qr_code": "obank"},
        raw_payload={
            "id": "OBANK-1",
            "provider_transaction_id": "987654321",
            "invoice_id": "987654321",
        },
        provider="odengi",
    )
    mkassa = FakeProvider("mkassa", "MBANK-1")
    odengi = FakeProvider("odengi", "OBANK-1")
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[mkassa, odengi],
        store=store,
    )

    with TestClient(app) as client:
        webhook = client.post(
            "/api/v1/webhooks/mkassa",
            json={
                "id": "MBANK-1",
                "status": "paid",
                "amount": "100",
                "metadata": {"invoice_id": invoice_id},
            },
        )
        other = client.get(
            "/api/v1/local/transactions/OBANK-1",
            headers={"X-Admin-Key": "admin-secret"},
        )
        one_c_pending = client.get(
            "/api/v1/local/1c/payment-events/pending",
            headers={"X-Integration-Key": "1c-secret"},
        )

    assert webhook.status_code == 200
    assert webhook.json()["duplicate"] is False
    assert other.status_code == 200
    assert other.json()["status"] == "canceled"
    assert one_c_pending.status_code == 200
    assert one_c_pending.json()[0]["payment_code"] == "mbank"
    assert one_c_pending.json()[0]["event_payload"]["paymentCode"] == "mbank"
    assert odengi.canceled_transaction_ids == ["987654321"]
    assert mkassa.canceled_transaction_ids == []


def test_provider_routing_uses_integration_name_mapping(tmp_path: Path) -> None:
    mkassa_provider = FakeProvider("mkassa", "MKSA-ROUTED")
    odengi_provider = FakeProvider("odengi", "TIGER-ODENGI")
    app = create_app(
        settings=make_multi_provider_settings(tmp_path / "app.db"),
        providers=[mkassa_provider, odengi_provider],
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        mkassa = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "mkassa-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-MKASSA"}},
        )
        odengi = client.post(
            "/api/v1/qr/dynamic",
            headers={"X-Integration-Key": "odengi-secret"},
            json={"amount": 200, "metadata": {"invoice_number": "TIGER-ODENGI"}},
        )
        local_mkassa = client.get(
            "/api/v1/local/transactions/MKSA-ROUTED",
            headers={"X-Admin-Key": "admin-secret"},
        )
        local_odengi = client.get(
            "/api/v1/local/transactions/TIGER-ODENGI",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert mkassa.status_code == 200
    assert mkassa.json()["id"] == "MKSA-ROUTED"
    assert odengi.status_code == 200
    assert odengi.json()["id"] == "TIGER-ODENGI"
    assert mkassa_provider.last_dynamic_payload.amount == 100
    assert odengi_provider.last_dynamic_payload.amount == 200
    assert local_mkassa.json()["provider"] == "mkassa"
    assert local_odengi.json()["provider"] == "odengi"


def test_odengi_static_qr_does_not_require_mkassa_branch_fields(tmp_path: Path) -> None:
    mkassa_provider = FakeProvider("mkassa", "MKSA-STATIC")
    odengi_provider = FakeProvider("odengi", "TIGER-STATIC")
    app = create_app(
        settings=make_multi_provider_settings(tmp_path / "app.db"),
        providers=[mkassa_provider, odengi_provider],
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        missing_mkassa_fields = client.post(
            "/api/v1/qr/static",
            headers={"X-Integration-Key": "mkassa-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-MKASSA"}},
        )
        odengi = client.post(
            "/api/v1/qr/static",
            headers={"X-Integration-Key": "odengi-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-STATIC"}},
        )
        local_odengi = client.get(
            "/api/v1/local/transactions/TIGER-STATIC",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert missing_mkassa_fields.status_code == 422
    assert missing_mkassa_fields.json()["detail"] == (
        "branch and cashier are required for MKassa static QR"
    )
    assert odengi.status_code == 200
    assert odengi.json()["id"] == "TIGER-STATIC"
    assert local_odengi.json()["provider"] == "odengi"


def test_admin_qr_demo_accepts_provider_query(tmp_path: Path) -> None:
    mkassa_provider = FakeProvider("mkassa", "MKSA-ADMIN")
    odengi_provider = FakeProvider("odengi", "TIGER-ADMIN")
    app = create_app(
        settings=make_multi_provider_settings(tmp_path / "app.db"),
        providers=[mkassa_provider, odengi_provider],
        store=SQLitePaymentStore(tmp_path / "app.db"),
    )

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/admin/qr/dynamic",
            params={"provider": "odengi"},
            headers={"X-Admin-Key": "admin-secret"},
            json={"amount": 100, "metadata": {"invoice_number": "TIGER-ADMIN"}},
        )
        local = client.get(
            "/api/v1/local/transactions/TIGER-ADMIN",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert create.status_code == 200
    assert create.json()["id"] == "TIGER-ADMIN"
    assert odengi_provider.last_dynamic_payload.metadata == {"invoice_number": "TIGER-ADMIN"}
    assert local.json()["provider"] == "odengi"


def test_odengi_webhook_is_public_and_updates_order_id_transaction(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    seed_waiting_transaction(
        store,
        "TIGER-1",
        amount=100,
        metadata={"invoice_number": "TIGER-1", "source": "tiger"},
        provider="odengi",
    )
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[FakeProvider("mkassa", "MKSA-1"), FakeProvider("odengi", "TIGER-1")],
        store=store,
    )
    payload = {
        "trans_id": "754147413495",
        "status_pay": 3,
        "site_id": "8087710950",
        "order_id": "TIGER-1",
        "amount": 100,
        "currency": "KGS",
        "date_pay": "2026-07-13 08:30:00",
        "test": 1,
        "fields_other": {"invoice_number": "TIGER-1", "source": "tiger"},
    }

    with TestClient(app) as client:
        first = client.post("/api/v1/webhooks/odengi", json=payload)
        second = client.post("/api/v1/webhooks/odengi", json=payload)
        local = client.get(
            "/api/v1/local/transactions/TIGER-1",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert first.status_code == 200
    assert first.json() == {"ok": True, "transaction_id": "TIGER-1", "duplicate": False}
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert local.status_code == 200
    assert local.json()["provider"] == "odengi"
    assert local.json()["status"] == "paid"
    assert local.json()["paid_at"] == "2026-07-13T08:30:00"
    assert local.json()["metadata"] == {"invoice_number": "TIGER-1", "source": "tiger"}


def test_odengi_webhook_uses_callback_time_when_payment_time_is_omitted(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    seed_waiting_transaction(
        store,
        "TIGER-2",
        amount=100,
        metadata={"invoice_number": "TIGER-2", "source": "tiger"},
        provider="odengi",
    )
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[FakeProvider("mkassa", "MKSA-1"), FakeProvider("odengi", "TIGER-2")],
        store=store,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/odengi",
            json={
                "status_pay": 3,
                "order_id": "TIGER-2",
                "amount": 100,
                "mktime": "2026-07-13 08:31:00",
            },
        )
        local = client.get(
            "/api/v1/local/transactions/TIGER-2",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert response.status_code == 200
    assert local.status_code == 200
    assert local.json()["paid_at"] == "2026-07-13T08:31:00"


def test_odengi_webhook_normalizes_unix_callback_time(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    store = SQLitePaymentStore(db_path)
    seed_waiting_transaction(
        store,
        "TIGER-3",
        amount=100,
        metadata={"invoice_number": "TIGER-3", "source": "tiger"},
        provider="odengi",
    )
    app = create_app(
        settings=make_multi_provider_settings(db_path),
        providers=[FakeProvider("mkassa", "MKSA-1"), FakeProvider("odengi", "TIGER-3")],
        store=store,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/odengi",
            json={"status_pay": 3, "order_id": "TIGER-3", "amount": 100, "mktime": 1783924260},
        )
        local = client.get(
            "/api/v1/local/transactions/TIGER-3",
            headers={"X-Admin-Key": "admin-secret"},
        )

    assert response.status_code == 200
    assert local.status_code == 200
    assert local.json()["paid_at"] == "2026-07-13T12:31:00+06:00"
