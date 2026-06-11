from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from payment_gateway.config import Settings
from payment_gateway.models import (
    BranchListResponse,
    CancelResponse,
    DynamicQRCreate,
    DynamicQRResponse,
    StaticQRCreate,
    StaticQRResponse,
    Transaction,
    TransactionDetailListResponse,
    TransactionFilters,
    TransactionListResponse,
)
from payment_gateway.providers.base import PaymentProvider


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class MKassaError(Exception):
    """Base error for MKassa integration failures."""


class MKassaAPIError(MKassaError):
    def __init__(self, status_code: int, message: str, response_text: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class MKassaTransportError(MKassaError):
    """Network or timeout error while calling MKassa."""


class AsyncMKassaClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.mkassa.kg",
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        retry_base_seconds: float = 0.3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.authorization_header = self._normalize_authorization(api_key)
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout or httpx.Timeout(20.0, connect=5.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AsyncMKassaClient:
        timeout = httpx.Timeout(
            settings.request_timeout_read,
            connect=settings.request_timeout_connect,
            write=settings.request_timeout_write,
            pool=settings.request_timeout_pool,
        )
        return cls(
            api_key=settings.mkassa_authorization_header,
            base_url=settings.mkassa_base_url,
            timeout=timeout,
            max_retries=settings.request_max_retries,
            retry_base_seconds=settings.request_retry_base_seconds,
        )

    async def __aenter__(self) -> AsyncMKassaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        response = await self._request(
            "POST",
            "/api/partners/transactions/init_payment/",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return DynamicQRResponse.model_validate(response.json())

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        response = await self._request(
            "POST",
            "/api/partners/qr-static/create_static_qr/",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return StaticQRResponse.model_validate(response.json())

    async def get_transaction(self, transaction_id: str) -> Transaction:
        response = await self._request(
            "GET",
            f"/api/partners/transactions/{quote(transaction_id, safe='')}/",
        )
        return Transaction.model_validate(response.json())

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        response = await self._request(
            "PUT",
            f"/api/partners/transactions/{quote(transaction_id, safe='')}/cancel/",
        )
        message = self._response_message(response)
        return CancelResponse(transaction_id=transaction_id, message=message)

    async def list_transactions(
        self,
        *,
        page: int | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        branch: int | None = None,
        cashier: int | None = None,
    ) -> TransactionListResponse:
        filters = TransactionFilters(
            page=page,
            status=status,
            type=transaction_type,
            start_date=start_date,
            end_date=end_date,
            branch=branch,
            cashier=cashier,
        )
        response = await self._request(
            "GET",
            "/api/partners/v1/transactions/",
            params=self._params(filters.model_dump(mode="json", exclude_none=True)),
        )
        return TransactionListResponse.model_validate(response.json())

    async def iter_transactions(self, **filters: Any) -> AsyncIterator[Transaction]:
        page = int(filters.pop("page", 1) or 1)
        while True:
            result = await self.list_transactions(page=page, **filters)
            for item in result.results:
                yield item
            if not result.next or (result.page_count is not None and page >= result.page_count):
                break
            page += 1

    async def transaction_details(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
        page: int | None = None,
    ) -> TransactionDetailListResponse:
        params = self._params(
            {
                "start_date": start_date,
                "end_date": end_date,
                "page": page,
            }
        )
        response = await self._request(
            "GET",
            "/api/partners/transactions-detail/",
            params=params,
        )
        return TransactionDetailListResponse.model_validate(response.json())

    async def branches(self, *, page: int | None = None) -> BranchListResponse:
        response = await self._request(
            "GET",
            "/api/partners/branches/",
            params=self._params({"page": page}),
        )
        return BranchListResponse.model_validate(response.json())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"
        headers = {
            "Authorization": self.authorization_header,
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"

        last_transport_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                last_transport_error = exc
                if attempt >= self.max_retries:
                    raise MKassaTransportError(f"MKassa request timed out: {method} {path}") from exc
                await self._sleep_before_retry(attempt)
                continue
            except httpx.HTTPError as exc:
                last_transport_error = exc
                if attempt >= self.max_retries:
                    raise MKassaTransportError(f"MKassa request failed: {method} {path}") from exc
                await self._sleep_before_retry(attempt)
                continue

            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                await self._sleep_before_retry(attempt, response=response)
                continue

            if response.is_error:
                raise self._api_error(response)
            return response

        raise MKassaTransportError(f"MKassa request failed: {method} {path}") from last_transport_error

    async def _sleep_before_retry(
        self,
        attempt: int,
        *,
        response: httpx.Response | None = None,
    ) -> None:
        retry_after = self._retry_after_seconds(response) if response is not None else None
        delay = retry_after if retry_after is not None else self.retry_base_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _retry_after_seconds(response: httpx.Response | None) -> float | None:
        if response is None:
            return None
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            return None

    @staticmethod
    def _api_error(response: httpx.Response) -> MKassaAPIError:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        message = f"MKassa API error {response.status_code}: {detail}"
        return MKassaAPIError(response.status_code, message, response.text)

    @staticmethod
    def _response_message(response: httpx.Response) -> str:
        try:
            parsed = response.json()
        except ValueError:
            parsed = response.text
        if isinstance(parsed, str):
            return parsed
        return response.text.strip() or "OK"

    @staticmethod
    def _params(values: Mapping[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for key, value in values.items():
            if value is None:
                continue
            params[key] = value.isoformat() if hasattr(value, "isoformat") else value
        return params

    @staticmethod
    def _normalize_authorization(api_key: str) -> str:
        normalized = api_key.strip()
        if normalized.lower().startswith("api-key "):
            return normalized
        return f"api-key {normalized}"


@dataclass(frozen=True)
class MKassaProvider(PaymentProvider):
    client: AsyncMKassaClient
    name: str = "mkassa"

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        return await self.client.create_dynamic_qr(payload)

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        return await self.client.create_static_qr(payload)

    async def get_transaction(self, transaction_id: str) -> Transaction:
        return await self.client.get_transaction(transaction_id)

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        return await self.client.cancel_transaction(transaction_id)

    async def list_transactions(self, **filters: object) -> TransactionListResponse:
        return await self.client.list_transactions(**filters)

    async def transaction_details(self, **filters: object) -> TransactionDetailListResponse:
        return await self.client.transaction_details(**filters)

    async def branches(self, **filters: object) -> BranchListResponse:
        return await self.client.branches(**filters)
