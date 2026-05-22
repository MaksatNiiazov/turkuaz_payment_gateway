from __future__ import annotations

import json

import httpx
import pytest

from mbank_integration.client import AsyncMKassaClient, MKassaAPIError
from mbank_integration.models import DynamicQRCreate


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
            "metadata": {"order_id": "ORD-1"},
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
                metadata={"order_id": "ORD-1"},
            )
        )

    await http_client.aclose()
    assert response.id == "MKSA-1"
    assert response.payment_token == "https://app.mbank.kg/qr#abc"


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
