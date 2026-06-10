from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

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
    TransactionListResponse,
)


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class ODengiError(Exception):
    """Base error for O!Dengi integration failures."""


class ODengiAPIError(ODengiError):
    def __init__(self, status_code: int, message: str, response_text: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class ODengiTransportError(ODengiError):
    """Network or timeout error while calling O!Dengi."""


class AsyncODengiClient:
    def __init__(
        self,
        *,
        sid: str,
        password: str,
        base_url: str = "https://mw-api-test.dengi.kg/api/json/json.php",
        api_version: int = 1005,
        lang: str = "ru",
        test: int = 1,
        currency: str = "KGS",
        result_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        retry_base_seconds: float = 0.3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.sid = sid
        self.password = password
        self.base_url = base_url
        self.api_version = api_version
        self.lang = lang
        self.test = test
        self.currency = currency
        self.result_url = result_url
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout or httpx.Timeout(20.0, connect=5.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AsyncODengiClient:
        timeout = httpx.Timeout(
            settings.request_timeout_read,
            connect=settings.request_timeout_connect,
            write=settings.request_timeout_write,
            pool=settings.request_timeout_pool,
        )
        if settings.odengi_sid is None:
            raise ValueError("ODENGI_SID is not configured")
        return cls(
            sid=settings.odengi_sid,
            password=settings.odengi_password_value,
            base_url=settings.odengi_base_url,
            api_version=settings.odengi_api_version,
            lang=settings.odengi_lang,
            test=settings.odengi_test,
            currency=settings.odengi_currency,
            result_url=settings.odengi_result_url,
            timeout=timeout,
            max_retries=settings.request_max_retries,
            retry_base_seconds=settings.request_retry_base_seconds,
        )

    async def __aenter__(self) -> AsyncODengiClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        metadata = self._metadata(payload.metadata)
        order_id = self._order_id(metadata)
        long_term = 1 if payload.is_long_living else 0
        data = self._invoice_data(
            order_id=order_id,
            amount=payload.amount,
            metadata=metadata,
            long_term=long_term,
        )
        response_data = await self._command("createInvoice", data)
        return self._dynamic_response(
            order_id=order_id,
            amount=payload.amount,
            metadata=metadata,
            response_data=response_data,
            long_term=long_term,
        )

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        metadata = self._metadata(payload.metadata)
        order_id = self._order_id(metadata)
        data = self._invoice_data(
            order_id=order_id,
            amount=payload.amount,
            metadata=metadata,
            long_term=1,
        )
        response_data = await self._command("createInvoice", data)
        qr_link = self._qr_payload(response_data)
        return StaticQRResponse(
            id=order_id,
            static_qr_link=qr_link,
            branch=payload.branch,
            cashier=payload.cashier,
            amount=payload.amount,
            change_amount=payload.change_amount,
            metadata=self._response_metadata(order_id, metadata, response_data),
            provider_transaction_id=response_data.get("invoice_id"),
            invoice_id=response_data.get("invoice_id"),
            qr=response_data.get("qr"),
            emv_qr=response_data.get("emv_qr"),
            qr_url=response_data.get("qr_url"),
            link_app=response_data.get("link_app"),
            site_pay=response_data.get("site_pay"),
        )

    async def get_transaction(self, transaction_id: str) -> Transaction:
        response_data = await self._command(
            "statusPayment",
            {
                "order_id": transaction_id,
                "mark": 1,
            },
        )
        payment = self._latest_payment(response_data)
        status_value = payment.get("status") if payment else response_data.get("status")
        amount = payment.get("amount") if payment else response_data.get("amount")
        paid_at = payment.get("date_pay") if payment else response_data.get("date_pay")
        metadata = payment.get("fields_other") if payment else response_data.get("fields_other")
        return Transaction(
            id=transaction_id,
            amount=amount,
            status=self._status(status_value),
            transaction_type="qr",
            paid_at=paid_at,
            metadata=metadata or {"order_id": transaction_id},
            odengi_status=status_value,
            payments=response_data.get("payments"),
        )

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        response_data = await self._command(
            "invoiceCancel",
            {
                "invoice_id": transaction_id,
            },
        )
        message = "OK" if response_data.get("success") is True else json.dumps(response_data)
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
        return TransactionListResponse(count=0, results=[])

    async def transaction_details(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
        page: int | None = None,
    ) -> TransactionDetailListResponse:
        return TransactionDetailListResponse(count=0, results=[])

    async def branches(self, *, page: int | None = None) -> BranchListResponse:
        return BranchListResponse(count=0, results=[])

    async def _command(self, cmd: str, data: Mapping[str, Any]) -> dict[str, Any]:
        body = self._signed_body(cmd, data)
        response = await self._request("POST", json=body)
        try:
            parsed = response.json()
        except ValueError as exc:
            raise ODengiAPIError(response.status_code, "O!Dengi response is not JSON", response.text) from exc
        response_data = parsed.get("data")
        if not isinstance(response_data, dict):
            raise ODengiAPIError(
                response.status_code,
                f"O!Dengi response has invalid data: {parsed}",
                response.text,
            )
        if "error" in response_data:
            raise ODengiAPIError(
                response.status_code,
                f"O!Dengi API error {response_data.get('error')}: {response_data.get('desc')}",
                response.text,
            )
        return response_data

    async def _request(self, method: str, *, json: Mapping[str, Any]) -> httpx.Response:
        last_transport_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    self.base_url,
                    json=json,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                last_transport_error = exc
                if attempt >= self.max_retries:
                    raise ODengiTransportError(f"O!Dengi request timed out: {method}") from exc
                await self._sleep_before_retry(attempt)
                continue
            except httpx.HTTPError as exc:
                last_transport_error = exc
                if attempt >= self.max_retries:
                    raise ODengiTransportError(f"O!Dengi request failed: {method}") from exc
                await self._sleep_before_retry(attempt)
                continue

            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                await self._sleep_before_retry(attempt, response=response)
                continue

            if response.is_error:
                raise ODengiAPIError(
                    response.status_code,
                    f"O!Dengi API HTTP error {response.status_code}",
                    response.text,
                )
            return response

        raise ODengiTransportError(f"O!Dengi request failed: {method}") from last_transport_error

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

    def _signed_body(self, cmd: str, data: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "cmd": cmd,
            "version": self.api_version,
            "sid": self.sid,
            "mktime": str(int(time.time())),
            "lang": self.lang,
            "data": self._without_none(data),
        }
        body["hash"] = self._signature(body)
        return body

    def _signature(self, body_without_hash: Mapping[str, Any]) -> str:
        json_body = self._json_dumps(body_without_hash)
        return hmac.new(
            self.password.encode("utf-8"),
            json_body.encode("utf-8"),
            hashlib.md5,
        ).hexdigest()

    def _invoice_data(
        self,
        *,
        order_id: str,
        amount: int | None,
        metadata: dict[str, str],
        long_term: int,
    ) -> dict[str, Any]:
        return self._without_none(
            {
                "order_id": order_id,
                "desc": metadata.get("description") or metadata.get("invoice_number") or order_id,
                "amount": amount,
                "currency": self.currency,
                "test": self.test,
                "long_term": long_term,
                "result_url": self.result_url,
                "fields_other": metadata or None,
            }
        )

    def _dynamic_response(
        self,
        *,
        order_id: str,
        amount: int,
        metadata: dict[str, str],
        response_data: Mapping[str, Any],
        long_term: int,
    ) -> DynamicQRResponse:
        return DynamicQRResponse(
            id=order_id,
            amount=amount,
            status="waiting",
            transaction_type="static" if long_term else "qr",
            payment_token=self._qr_payload(response_data),
            metadata=self._response_metadata(order_id, metadata, response_data),
            provider_transaction_id=response_data.get("invoice_id"),
            invoice_id=response_data.get("invoice_id"),
            qr=response_data.get("qr"),
            emv_qr=response_data.get("emv_qr"),
            qr_url=response_data.get("qr_url"),
            link_app=response_data.get("link_app"),
            site_pay=response_data.get("site_pay"),
        )

    def _response_metadata(
        self,
        order_id: str,
        metadata: dict[str, str],
        response_data: Mapping[str, Any],
    ) -> dict[str, str]:
        response_metadata = dict(metadata)
        response_metadata.setdefault("order_id", order_id)
        invoice_id = response_data.get("invoice_id")
        if invoice_id is not None:
            response_metadata["invoice_id"] = str(invoice_id)
        return response_metadata

    @staticmethod
    def _metadata(value: dict[str, str] | None) -> dict[str, str]:
        return dict(value or {})

    @staticmethod
    def _order_id(metadata: Mapping[str, str]) -> str:
        order_id = metadata.get("order_id") or metadata.get("invoice_number")
        if order_id:
            return order_id[:64]
        return f"TGW-{uuid.uuid4().hex[:20]}"

    @staticmethod
    def _qr_payload(response_data: Mapping[str, Any]) -> str:
        for key in ("qr_url", "link_app", "qr", "emv_qr", "site_pay"):
            value = response_data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        raise ODengiAPIError(200, f"O!Dengi response does not contain a QR link: {response_data}")

    @staticmethod
    def _latest_payment(response_data: Mapping[str, Any]) -> dict[str, Any] | None:
        payments = response_data.get("payments")
        if isinstance(payments, list) and payments:
            latest = payments[-1]
            if isinstance(latest, dict):
                return latest
        return None

    @staticmethod
    def _status(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"3", "approved"}:
            return "paid"
        if normalized in {"2", "canceled", "cancelled"}:
            return "canceled"
        if normalized in {"1", "processing"}:
            return "waiting"
        return "unknown"

    @staticmethod
    def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _json_dumps(value: Mapping[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

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


@dataclass(frozen=True)
class ODengiProvider:
    client: AsyncODengiClient
    name: str = "odengi"

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
