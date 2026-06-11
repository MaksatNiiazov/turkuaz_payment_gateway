from __future__ import annotations

from abc import ABC, abstractmethod

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


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    async def create_dynamic_qr(self, payload: DynamicQRCreate) -> DynamicQRResponse:
        raise NotImplementedError

    @abstractmethod
    async def create_static_qr(self, payload: StaticQRCreate) -> StaticQRResponse:
        raise NotImplementedError

    @abstractmethod
    async def get_transaction(self, transaction_id: str) -> Transaction:
        raise NotImplementedError

    @abstractmethod
    async def cancel_transaction(self, transaction_id: str) -> CancelResponse:
        raise NotImplementedError

    @abstractmethod
    async def list_transactions(self, **filters: object) -> TransactionListResponse:
        raise NotImplementedError

    @abstractmethod
    async def transaction_details(self, **filters: object) -> TransactionDetailListResponse:
        raise NotImplementedError

    @abstractmethod
    async def branches(self, **filters: object) -> BranchListResponse:
        raise NotImplementedError
