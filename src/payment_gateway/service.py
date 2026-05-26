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

    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        provider = self.gateway.provider()
        response = await provider.create_dynamic_qr(payload)
        self.store.upsert_transaction_payload(response)
        return response

    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        provider = self.gateway.provider()
        response = await provider.create_static_qr(payload)
        self.store.upsert_transaction_payload(response)
        return response

    async def get_transaction(self, transaction_id: str) -> Transaction:
        provider = self.gateway.provider()
        response = await provider.get_transaction(transaction_id)
        self.store.upsert_transaction_payload(response)
        return response

    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        response = await self.gateway.provider().cancel_transaction(transaction_id)
        self.store.update_transaction_status(transaction_id, status="canceled")
        return response

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
    ) -> TransactionListResponse:
        return await self.gateway.provider().list_transactions(
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
    ) -> TransactionDetailListResponse:
        return await self.gateway.provider().transaction_details(
            start_date=start_date,
            end_date=end_date,
            page=page,
        )

    async def branches(self, *, page: int | None = None) -> BranchListResponse:
        return await self.gateway.provider().branches(page=page)

    def save_webhook(self, payload: WebhookPayload) -> WebhookStoreResult:
        return self.store.save_webhook(payload, provider=self.gateway.default_provider)
