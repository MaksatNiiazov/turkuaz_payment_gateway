from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from payment_gateway.models import DynamicQRCreate
from payment_gateway.providers.odengi import AsyncODengiClient, ODengiAPIError


def signed_json(body: dict) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.asyncio
async def test_create_dynamic_qr_sends_signed_odengi_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://mw-api-test.dengi.kg/api/json/json.php"
        body = json.loads(request.content)
        signature_body = dict(body)
        signature = signature_body.pop("hash")
        expected = hmac.new(
            b"merchant-password",
            signed_json(signature_body).encode("utf-8"),
            hashlib.md5,
        ).hexdigest()

        assert signature == expected
        assert body["cmd"] == "createInvoice"
        assert body["version"] == 1005
        assert body["sid"] == "8087710950"
        assert body["lang"] == "ru"
        assert body["data"] == {
            "order_id": "TIGER-FACTURE-1001",
            "desc": "TIGER-FACTURE-1001",
            "amount": 100,
            "currency": "KGS",
            "test": 1,
            "long_term": 0,
            "result_url": "https://payments.example/api/v1/webhooks/odengi",
            "fields_other": {
                "invoice_number": "TIGER-FACTURE-1001",
                "source": "tiger",
            },
        }
        return httpx.Response(
            200,
            json={
                "version": 1005,
                "sid": "8087710950",
                "cmd": "createInvoice",
                "data": {
                    "invoice_id": "172030403548",
                    "qr": "https://test4-mwallet.dengi.kg/qr.php?data=abc",
                    "qr_url": "https://test4-mwallet.dengi.kg/#abc",
                    "link_app": "https://o.kg/l/a?t=wl_unpbill&id=172030403548",
                    "site_pay": "https://test4-mwallet.dengi.kg/_test",
                },
                "hash": "response-hash",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncODengiClient(
        sid="8087710950",
        password="merchant-password",
        result_url="https://payments.example/api/v1/webhooks/odengi",
        retry_base_seconds=0,
        max_retries=0,
        http_client=http_client,
    ) as client:
        response = await client.create_dynamic_qr(
            DynamicQRCreate(
                amount=100,
                metadata={
                    "invoice_number": "TIGER-FACTURE-1001",
                    "source": "tiger",
                },
            )
        )

    await http_client.aclose()
    assert response.id == "TIGER-FACTURE-1001"
    assert response.payment_token == "https://test4-mwallet.dengi.kg/#abc"
    assert response.metadata == {
        "invoice_number": "TIGER-FACTURE-1001",
        "source": "tiger",
        "order_id": "TIGER-FACTURE-1001",
        "invoice_id": "172030403548",
    }
    assert response.invoice_id == "172030403548"
    assert response.link_app == "https://o.kg/l/a?t=wl_unpbill&id=172030403548"


@pytest.mark.asyncio
async def test_get_transaction_maps_odengi_status_payment() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "cmd": "statusPayment",
                "data": {
                    "payments": [
                        {
                            "trans_id": "754147413495",
                            "date_pay": "2024-01-12 10:23:47.983877",
                            "amount": "100",
                            "status": "approved",
                            "fields_other": {"invoice_number": "TIGER-1"},
                        }
                    ]
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncODengiClient(
        sid="8087710950",
        password="merchant-password",
        retry_base_seconds=0,
        max_retries=0,
        http_client=http_client,
    ) as client:
        response = await client.get_transaction("TIGER-1")

    await http_client.aclose()
    assert response.id == "TIGER-1"
    assert response.status == "paid"
    assert response.amount == 100
    assert response.metadata == {"invoice_number": "TIGER-1"}


@pytest.mark.asyncio
async def test_get_transaction_maps_missing_odengi_payment_to_waiting() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "cmd": "statusPayment",
                "data": {
                    "error": 63,
                    "desc": "Транзакций по mark 1 не найдено",
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncODengiClient(
        sid="8087710950",
        password="merchant-password",
        retry_base_seconds=0,
        max_retries=0,
        http_client=http_client,
    ) as client:
        response = await client.get_transaction("TIGER-1")

    await http_client.aclose()
    assert response.id == "TIGER-1"
    assert response.status == "waiting"
    assert response.transaction_type == "qr"
    assert response.metadata == {"order_id": "TIGER-1"}


@pytest.mark.asyncio
async def test_odengi_api_error_is_raised_from_error_payload() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"cmd": "createInvoice", "data": {"error": 37, "desc": "bad invoice"}},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncODengiClient(
        sid="8087710950",
        password="merchant-password",
        retry_base_seconds=0,
        max_retries=0,
        http_client=http_client,
    ) as client:
        with pytest.raises(ODengiAPIError) as exc:
            await client.create_dynamic_qr(DynamicQRCreate(amount=100))

    await http_client.aclose()
    assert exc.value.status_code == 200
    assert "bad invoice" in str(exc.value)
