from __future__ import annotations

from datetime import date

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
from payment_gateway.store import PaymentStore, WebhookStoreResult


class PaymentService:
    def __init__(self, *, gateway: PaymentGateway, store: PaymentStore) -> None:
        self.gateway = gateway
        self.store = store

    async def create_dynamic_qr(
        self,
        payload: DynamicQRCreate,
        *,
        provider_name: str | None = None,
    ) -> DynamicQRResponse:
        provider = self.gateway.provider(provider_name)
        response = await provider.create_dynamic_qr(payload)
        self.store.upsert_transaction_payload(response, provider=provider.name)
        return response

    async def create_static_qr(
        self,
        payload: StaticQRCreate,
        *,
        provider_name: str | None = None,
    ) -> StaticQRResponse:
        provider = self.gateway.provider(provider_name)
        response = await provider.create_static_qr(payload)
        self.store.upsert_transaction_payload(response, provider=provider.name)
        return response

    async def get_transaction(
        self,
        transaction_id: str,
        *,
        provider_name: str | None = None,
    ) -> Transaction:
        provider = self.gateway.provider(self._saved_provider_name(transaction_id) or provider_name)
        response = await provider.get_transaction(transaction_id)
        self.store.upsert_transaction_payload(
            response,
            provider=provider.name,
            transaction_id_override=transaction_id,
        )
        return response

    async def cancel_transaction(
        self,
        transaction_id: str,
        *,
        provider_name: str | None = None,
    ) -> CancelResponse:
        provider = self.gateway.provider(self._saved_provider_name(transaction_id) or provider_name)
        provider_transaction_id = self._provider_transaction_id(transaction_id)
        response = await provider.cancel_transaction(provider_transaction_id)
        self.store.update_transaction_status(
            transaction_id,
            status="canceled",
            provider=provider.name,
        )
        return CancelResponse(transaction_id=transaction_id, message=response.message)

    async def list_transactions(
        self,
        *,
        page: int | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        branch: int | None = None,
        cashier: int | None = None,
        provider_name: str | None = None,
    ) -> TransactionListResponse:
        return await self.gateway.provider(provider_name).list_transactions(
            page=page,
            status=status,
            transaction_type=transaction_type,
            start_date=start_date,
            end_date=end_date,
            branch=branch,
            cashier=cashier,
        )

    async def transaction_details(
        self,
        *,
        start_date: date,
        end_date: date,
        page: int | None = None,
        provider_name: str | None = None,
    ) -> TransactionDetailListResponse:
        return await self.gateway.provider(provider_name).transaction_details(
            start_date=start_date,
            end_date=end_date,
            page=page,
        )

    async def branches(
        self,
        *,
        page: int | None = None,
        provider_name: str | None = None,
    ) -> BranchListResponse:
        return await self.gateway.provider(provider_name).branches(page=page)

    def save_webhook(
        self,
        payload: WebhookPayload,
        *,
        provider_name: str | None = None,
    ) -> WebhookStoreResult:
        provider = self.gateway.provider(provider_name)
        return self.store.save_webhook(payload, provider=provider.name)

    def _saved_provider_name(self, transaction_id: str) -> str | None:
        saved = self.store.get_transaction(transaction_id)
        if saved is None:
            return None
        provider = saved.get("provider")
        return provider if isinstance(provider, str) and provider.strip() else None

    def _provider_transaction_id(self, transaction_id: str) -> str:
        saved = self.store.get_transaction(transaction_id)
        if saved is None:
            return transaction_id
        raw_payload = saved.get("raw_payload")
        if not isinstance(raw_payload, dict):
            return transaction_id
        provider_transaction_id = raw_payload.get("provider_transaction_id") or raw_payload.get(
            "invoice_id"
        )
        if provider_transaction_id is None:
            return transaction_id
        return str(provider_transaction_id)
