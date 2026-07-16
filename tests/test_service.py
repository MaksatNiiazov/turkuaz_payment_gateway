from __future__ import annotations

import pytest

from payment_gateway.gateway import PaymentGateway
from payment_gateway.models import (
    BranchListResponse,
    CancelResponse,
    DynamicQRCreate,
    DynamicQRResponse,
    StaticQRCreate,
    StaticQRResponse,
    Transaction,
    TransactionDetailListResponse,
    TransactionListResponse,
    WebhookPayload,
)
from payment_gateway.providers.base import PaymentProvider
from payment_gateway.service import PaymentService
from payment_gateway.store import SQLitePaymentStore, tiger_invoice_exports


class FakeProvider(PaymentProvider):
    def __init__(self, name: str = "fake", transaction_id: str = "FAKE-1") -> None:
        self.name = name
        self.transaction_id = transaction_id
        self.canceled_transaction_id: str | None = None
        self.canceled_transaction_ids: list[str] = []

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        return DynamicQRResponse(
            id=self.transaction_id,
            amount=payload.amount,
            status="inited",
            transaction_type="qr",
            payment_token="https://example.com/pay",
            metadata=payload.metadata,
        )

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        return StaticQRResponse(
            id=1,
            static_qr_link="https://example.com/static",
            branch=payload.branch,
            cashier=payload.cashier,
            amount=payload.amount,
            change_amount=payload.change_amount,
            metadata=payload.metadata,
        )

    async def get_transaction(self, transaction_id: str) -> Transaction:
        return Transaction(id=transaction_id, status="paid")

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        self.canceled_transaction_id = transaction_id
        self.canceled_transaction_ids.append(transaction_id)
        return CancelResponse(transaction_id=transaction_id, message="OK")

    async def list_transactions(self, **_: object) -> TransactionListResponse:
        return TransactionListResponse(count=0, results=[])

    async def transaction_details(self, **_: object) -> TransactionDetailListResponse:
        return TransactionDetailListResponse(count=0, results=[])

    async def branches(self, **_: object) -> BranchListResponse:
        return BranchListResponse(count=0, results=[])


class DuckProvider:
    name = "duck"

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        return DynamicQRResponse(
            id="DUCK-1",
            amount=payload.amount,
            status="inited",
            transaction_type="qr",
            payment_token="https://example.com/pay",
            metadata=payload.metadata,
        )

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        return StaticQRResponse(
            id=1,
            static_qr_link="https://example.com/static",
            amount=payload.amount,
            change_amount=payload.change_amount,
            metadata=payload.metadata,
        )

    async def get_transaction(self, transaction_id: str) -> Transaction:
        return Transaction(id=transaction_id, status="paid")

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        return CancelResponse(transaction_id=transaction_id, message="OK")

    async def list_transactions(self, **_: object) -> TransactionListResponse:
        return TransactionListResponse(count=0, results=[])

    async def transaction_details(self, **_: object) -> TransactionDetailListResponse:
        return TransactionDetailListResponse(count=0, results=[])

    async def branches(self, **_: object) -> BranchListResponse:
        return BranchListResponse(count=0, results=[])


def test_payment_gateway_requires_provider_base_class() -> None:
    with pytest.raises(TypeError, match="must inherit from PaymentProvider"):
        PaymentGateway([DuckProvider()], default_provider="duck")  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_payment_service_persists_provider_transaction(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    service = PaymentService(
        gateway=PaymentGateway([FakeProvider()], default_provider="fake"),
        store=store,
    )

    response = await service.create_dynamic_qr(
        DynamicQRCreate(
            amount=100,
            metadata={
                "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                "invoice_number": "TIGER-1",
            },
        )
    )
    saved = store.get_transaction(response.id)

    assert response.id == "FAKE-1"
    assert saved is not None
    assert saved["provider"] == "fake"
    assert saved["status"] == "inited"
    assert saved["external_invoice_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert saved["metadata"] == {
        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
        "invoice_number": "TIGER-1",
    }


@pytest.mark.asyncio
async def test_payment_service_cancels_by_saved_provider_transaction_id(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    provider = FakeProvider()
    service = PaymentService(
        gateway=PaymentGateway([provider], default_provider="fake"),
        store=store,
    )
    store.upsert_transaction(
        transaction_id="TIGER-1",
        status="waiting",
        transaction_type="qr",
        raw_payload={
            "id": "TIGER-1",
            "provider_transaction_id": "553459220202",
            "invoice_id": "553459220202",
        },
        provider="fake",
    )

    response = await service.cancel_transaction("TIGER-1")

    assert response.transaction_id == "TIGER-1"
    assert provider.canceled_transaction_id == "553459220202"
    assert store.get_transaction("TIGER-1")["status"] == "canceled"


@pytest.mark.asyncio
async def test_payment_service_auto_cancels_other_invoice_qr_after_paid_webhook(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    mkassa = FakeProvider("mkassa", "MBANK-1")
    odengi = FakeProvider("odengi", "OBANK-1")
    service = PaymentService(
        gateway=PaymentGateway([mkassa, odengi], default_provider="mkassa"),
        store=store,
    )
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
        metadata={
            "invoice_id": invoice_id,
            "print_qr_code": "obank",
            "provider_invoice_id": "987654321",
        },
        raw_payload={
            "id": "OBANK-1",
            "provider_transaction_id": "987654321",
            "invoice_id": "987654321",
        },
        provider="odengi",
    )

    await service.save_webhook(
        WebhookPayload(
            id="MBANK-1",
            status="paid",
            amount=100,
            metadata={"invoice_id": invoice_id, "print_qr_code": "mbank"},
        ),
        provider_name="mkassa",
    )

    assert store.get_transaction("MBANK-1")["status"] == "paid"
    assert store.get_transaction("OBANK-1")["status"] == "canceled"
    assert odengi.canceled_transaction_ids == ["987654321"]
    assert mkassa.canceled_transaction_ids == []


@pytest.mark.asyncio
async def test_payment_service_does_not_export_second_paid_invoice_qr(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    provider = FakeProvider("mkassa", "QR-2")
    service = PaymentService(
        gateway=PaymentGateway([provider], default_provider="mkassa"),
        store=store,
    )
    invoice_id = "550e8400-e29b-41d4-a716-446655440000"
    store.upsert_transaction(
        transaction_id="QR-1",
        status="paid",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id, "print_qr_code": "mbank"},
        provider="mkassa",
    )
    store.upsert_transaction(
        transaction_id="QR-2",
        status="waiting",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id, "print_qr_code": "obank"},
        provider="mkassa",
    )

    await service.save_webhook(
        WebhookPayload(
            id="QR-2",
            status="paid",
            amount=100,
            metadata={"invoice_id": invoice_id, "print_qr_code": "obank"},
        ),
        provider_name="mkassa",
    )
    await service.save_webhook(
        WebhookPayload(id="QR-2", status="canceled", amount=100),
        provider_name="mkassa",
    )

    assert store.get_transaction("QR-2")["status"] == "duplicate"
    assert store.list_one_c_payment_exports(limit=10) == []
    assert store.list_tiger_invoice_exports(limit=10) == []


@pytest.mark.asyncio
async def test_payment_service_refresh_keeps_second_paid_invoice_qr_duplicate(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    provider = FakeProvider("mkassa", "QR-2")
    service = PaymentService(
        gateway=PaymentGateway([provider], default_provider="mkassa"),
        store=store,
    )
    invoice_id = "INV-REFRESH-DUPLICATE"
    store.upsert_transaction(
        transaction_id="QR-1",
        status="paid",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
        provider="mkassa",
    )
    store.upsert_transaction(
        transaction_id="QR-2",
        status="waiting",
        transaction_type="qr",
        amount=100,
        external_invoice_id=invoice_id,
        metadata={"invoice_id": invoice_id},
        provider="mkassa",
    )

    refreshed = await service.get_transaction("QR-2")

    assert refreshed.status == "duplicate"
    assert store.get_transaction("QR-2")["status"] == "duplicate"
    assert store.list_one_c_payment_exports(limit=10) == []
    assert store.list_tiger_invoice_exports(limit=10) == []


def test_tiger_error_event_requires_explicit_reset(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    event = {
        "invoiceId": "INV-TIGER-ERROR",
        "paidTransactionId": "PAY-TIGER-ERROR",
        "paidProvider": "mkassa",
        "targetBankAccountCode": "BANK-KGS",
        "clientCode": "CARI.001",
        "amountTyiyn": 100,
        "amount": 1.0,
        "currency": "KGS",
    }
    created = store.upsert_tiger_invoice_export(event)
    failed = store.update_tiger_invoice_export_result(
        created["id"],
        status="error",
        error_message="temporary failure",
    )
    rebuilt = store.upsert_tiger_invoice_export(event, status="pending")

    assert failed is not None
    assert failed["status"] == "error"
    assert rebuilt["status"] == "error"
    assert rebuilt["error_message"] == "temporary failure"


def test_tiger_processing_lease_can_be_reclaimed_after_expiry(tmp_path) -> None:
    store = SQLitePaymentStore(tmp_path / "app.db")
    store.initialize()
    event = {
        "invoiceId": "INV-TIGER-LEASE",
        "paidTransactionId": "PAY-TIGER-LEASE",
        "paidProvider": "mkassa",
        "targetBankAccountCode": "BANK-KGS",
        "clientCode": "CARI.001",
        "amountTyiyn": 100,
        "amount": 1.0,
        "currency": "KGS",
    }
    created = store.upsert_tiger_invoice_export(event)
    first_claim = store.claim_tiger_invoice_exports(lease_seconds=30)
    with store.engine.begin() as connection:
        connection.execute(
            tiger_invoice_exports.update()
            .where(tiger_invoice_exports.c.id == created["id"])
            .values(last_attempt_at=None)
        )
    reclaimed = store.claim_tiger_invoice_exports(lease_seconds=30)

    assert first_claim[0]["status"] == "processing"
    assert reclaimed[0]["id"] == created["id"]
    assert reclaimed[0]["status"] == "processing"
