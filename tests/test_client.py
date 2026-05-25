from __future__ import annotations

import json

import httpx
import pytest

from mbank_integration.client import AsyncMKassaClient, MKassaAPIError
from mbank_integration.models import DynamicQRCreate, StaticQRCreate


@pytest.mark.asyncio
async def test_create_dynamic_qr_sends_expected_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/partners/transactions/init_payment/"
        assert request.headers["Authorization"] == "api-key secret"
        assert json.loads(request.content) == {
            "amount": 100,
            "branch": 12345,
            "cashier": 1234,
            "is_long_living": True,
            "metadata": {"invoice_number": "TIGER-FACTURE-1001"},
        }
        return httpx.Response(
            200,
            json={
                "id": "MKSA-1",
                "amount": 100,
                "status": "inited",
                "transaction_type": "qr",
                "created_at": "2026-02-13T12:00:00+06:00",
                "branch": 12345,
                "cashier": 1234,
                "payment_token": "https://app.mbank.kg/qr#abc",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncMKassaClient(
        api_key="secret",
        base_url="https://api.mkassa.kg",
        http_client=http_client,
    ) as client:
        response = await client.create_dynamic_qr(
            DynamicQRCreate(
                amount=100,
                branch=12345,
                cashier=1234,
                is_long_living=True,
                metadata={"invoice_number": "TIGER-FACTURE-1001"},
            )
        )

    await http_client.aclose()
    assert response.id == "MKSA-1"
    assert response.payment_token == "https://app.mbank.kg/qr#abc"


@pytest.mark.asyncio
async def test_create_static_qr_sends_expected_mkassa_field_names() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/partners/qr-static/create_static_qr/"
        assert request.headers["Authorization"] == "api-key secret"
        assert json.loads(request.content) == {
            "branch": 12345,
            "cashier": 1234,
            "amount": 100,
            "change_amount": False,
            "metadata": {
                "payer_code": "12345678901234",
                "payer_full_name": "ОсОО Тест",
                "invoice_number": "TIGER-FACTURE-1001",
            },
        }
        return httpx.Response(
            200,
            json={
                "id": 1,
                "static_qr_link": "https://app.mbank.kg/qr#static",
                "branch": "ЦУМ",
                "cashier": "Кассир",
                "amount": 100,
                "change_amount": False,
                "metadata": {
                    "payer_code": "12345678901234",
                    "payer_full_name": "ОсОО Тест",
                    "invoice_number": "TIGER-FACTURE-1001",
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncMKassaClient(
        api_key="secret",
        base_url="https://api.mkassa.kg",
        http_client=http_client,
    ) as client:
        response = await client.create_static_qr(
            StaticQRCreate(
                branch=12345,
                cashier=1234,
                amount=100,
                change_amount=False,
                metadata={
                    "payer_code": "12345678901234",
                    "payer_full_name": "ОсОО Тест",
                    "invoice_number": "TIGER-FACTURE-1001",
                },
            )
        )

    await http_client.aclose()
    assert response.id == 1
    assert response.static_qr_link == "https://app.mbank.kg/qr#static"


@pytest.mark.asyncio
async def test_list_transactions_sends_mkassa_filter_names() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/partners/v1/transactions/"
        assert dict(request.url.params) == {
            "page": "2",
            "status": "paid",
            "type": "qr",
            "start_date": "2026-05-01",
            "end_date": "2026-05-25",
            "branch": "12345",
            "cashier": "1234",
        }
        return httpx.Response(
            200,
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "page_count": 0,
                "results": [],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncMKassaClient(api_key="secret", http_client=http_client) as client:
        response = await client.list_transactions(
            page=2,
            status="paid",
            transaction_type="qr",
            start_date="2026-05-01",
            end_date="2026-05-25",
            branch=12345,
            cashier=1234,
        )

    await http_client.aclose()
    assert response.count == 0


@pytest.mark.asyncio
async def test_retries_transient_errors() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="temporary")
        return httpx.Response(
            200,
            json={
                "id": "MKSA-1",
                "amount": 100,
                "status": "inited",
                "transaction_type": "qr",
                "payment_token": "https://app.mbank.kg/qr#abc",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncMKassaClient(
        api_key="secret",
        retry_base_seconds=0,
        max_retries=1,
        http_client=http_client,
    ) as client:
        response = await client.create_dynamic_qr(DynamicQRCreate(amount=100))

    await http_client.aclose()
    assert calls == 2
    assert response.id == "MKSA-1"


@pytest.mark.asyncio
async def test_api_error_is_raised_without_retry_for_400() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad request"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncMKassaClient(api_key="secret", http_client=http_client) as client:
        with pytest.raises(MKassaAPIError) as exc:
            await client.create_dynamic_qr(DynamicQRCreate(amount=100))

    await http_client.aclose()
    assert exc.value.status_code == 400
