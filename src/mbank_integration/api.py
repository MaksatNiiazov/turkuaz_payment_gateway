from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from mbank_integration.client import AsyncMKassaClient, MKassaAPIError, MKassaTransportError
from mbank_integration.config import Settings, get_settings
from mbank_integration.models import (
    BranchListResponse,
    CancelResponse,
    DynamicQRCreate,
    DynamicQRResponse,
    StaticQRCreate,
    StaticQRResponse,
    Transaction,
    TransactionDetailListResponse,
    TransactionListResponse,
    WebhookAck,
    WebhookPayload,
)
from mbank_integration.store import SQLiteMKassaStore


integration_key_scheme = APIKeyHeader(
    name="X-Integration-Key",
    scheme_name="X-Integration-Key",
    auto_error=False,
    description="Integration key issued by this microservice owner.",
)
legacy_service_key_scheme = APIKeyHeader(
    name="X-Service-API-Key",
    scheme_name="X-Service-API-Key (legacy)",
    auto_error=False,
    description="Legacy integration key header. Prefer X-Integration-Key.",
)


def create_app(
    *,
    settings: Settings | None = None,
    client: AsyncMKassaClient | None = None,
    store: SQLiteMKassaStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or get_settings()
        resolved_store = store or SQLiteMKassaStore(resolved_settings.database_path)
        resolved_store.initialize()
        resolved_client = client or AsyncMKassaClient.from_settings(resolved_settings)

        app.state.settings = resolved_settings
        app.state.store = resolved_store
        app.state.mkassa_client = resolved_client
        try:
            yield
        finally:
            if client is None:
                await resolved_client.aclose()

    app = FastAPI(
        title="MBank MKassa Integration",
        version="0.1.0",
        description="Standalone microservice for MKassa QR payments integration.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def audit_service_client_requests(request: Request, call_next):
        response = await call_next(request)
        integration_name = getattr(request.state, "integration_name", None)
        if integration_name:
            remote_addr = request.client.host if request.client else None
            storage(request).save_api_access(
                integration_name=integration_name,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                user_agent=request.headers.get("user-agent"),
                remote_addr=remote_addr,
            )
        return response

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    protected_router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_service_api_key)],
    )

    @protected_router.post(
        "/qr/dynamic",
        response_model=DynamicQRResponse,
        tags=["qr"],
    )
    async def create_dynamic_qr(request: Request, payload: DynamicQRCreate) -> DynamicQRResponse:
        response = await mkassa(request).create_dynamic_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.post(
        "/qr/static",
        response_model=StaticQRResponse,
        tags=["qr"],
    )
    async def create_static_qr(request: Request, payload: StaticQRCreate) -> StaticQRResponse:
        response = await mkassa(request).create_static_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.get(
        "/transactions/{transaction_id}",
        response_model=Transaction,
        tags=["transactions"],
    )
    async def get_transaction(request: Request, transaction_id: str) -> Transaction:
        response = await mkassa(request).get_transaction(transaction_id)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.put(
        "/transactions/{transaction_id}/cancel",
        response_model=CancelResponse,
        tags=["transactions"],
    )
    async def cancel_transaction(request: Request, transaction_id: str) -> CancelResponse:
        response = await mkassa(request).cancel_transaction(transaction_id)
        storage(request).upsert_transaction(transaction_id=transaction_id, status="canceled")
        return response

    @protected_router.get(
        "/transactions",
        response_model=TransactionListResponse,
        tags=["transactions"],
    )
    async def list_transactions(
        request: Request,
        page: Annotated[int | None, Query(ge=1)] = None,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
        transaction_type: Annotated[str | None, Query(alias="type")] = None,
        start_date: date | None = None,
        end_date: date | None = None,
        branch: Annotated[int | None, Query(gt=0)] = None,
        cashier: Annotated[int | None, Query(gt=0)] = None,
    ) -> TransactionListResponse:
        return await mkassa(request).list_transactions(
            page=page,
            status=status_filter,
            transaction_type=transaction_type,
            start_date=start_date,
            end_date=end_date,
            branch=branch,
            cashier=cashier,
        )

    @protected_router.get(
        "/transaction-details",
        response_model=TransactionDetailListResponse,
        tags=["transactions"],
    )
    async def transaction_details(
        request: Request,
        start_date: date,
        end_date: date,
        page: Annotated[int | None, Query(ge=1)] = None,
    ) -> TransactionDetailListResponse:
        return await mkassa(request).transaction_details(
            start_date=start_date,
            end_date=end_date,
            page=page,
        )

    @protected_router.get(
        "/branches",
        response_model=BranchListResponse,
        tags=["branches"],
    )
    async def branches(
        request: Request,
        page: Annotated[int | None, Query(ge=1)] = None,
    ) -> BranchListResponse:
        return await mkassa(request).branches(page=page)

    @protected_router.get(
        "/integration",
        tags=["system"],
    )
    async def current_integration(request: Request) -> dict[str, str]:
        return {"integration_name": request.state.integration_name}

    @protected_router.get(
        "/local/transactions/{transaction_id}",
        tags=["local"],
    )
    async def local_transaction(request: Request, transaction_id: str) -> dict:
        item = storage(request).get_transaction(transaction_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
        return item

    @protected_router.get(
        "/local/webhooks",
        tags=["local"],
    )
    async def local_webhooks(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        return storage(request).list_webhook_events(limit=limit)

    @protected_router.get(
        "/local/access-events",
        tags=["local"],
    )
    async def local_access_events(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        return storage(request).list_api_access_events(limit=limit)

    webhook_router = APIRouter(prefix="/api/v1")

    @webhook_router.post(
        "/webhooks/mkassa",
        response_model=WebhookAck,
        tags=["webhooks"],
    )
    async def mkassa_webhook(
        request: Request,
        payload: WebhookPayload,
        secret: str | None = None,
        x_mkassa_webhook_secret: str | None = Header(None, alias="X-MKassa-Webhook-Secret"),
        x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    ) -> WebhookAck:
        verify_webhook_secret(
            settings_from_request(request),
            candidates=[secret, x_mkassa_webhook_secret, x_webhook_secret],
        )
        result = storage(request).save_webhook(payload)
        return WebhookAck(transaction_id=result.transaction_id, duplicate=result.duplicate)

    app.include_router(protected_router)
    app.include_router(webhook_router)
    app.add_exception_handler(MKassaAPIError, mkassa_api_error_handler)
    app.add_exception_handler(MKassaTransportError, mkassa_transport_error_handler)
    return app


def settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def mkassa(request: Request) -> AsyncMKassaClient:
    return request.app.state.mkassa_client


def storage(request: Request) -> SQLiteMKassaStore:
    return request.app.state.store


async def require_service_api_key(
    request: Request,
    x_integration_key: str | None = Depends(integration_key_scheme),
    x_service_api_key: str | None = Depends(legacy_service_key_scheme),
) -> None:
    key_pool = settings_from_request(request).service_key_pool
    if not key_pool:
        request.state.integration_name = "anonymous"
        return
    provided_key = x_integration_key or x_service_api_key
    if provided_key:
        for integration_name, expected in key_pool.items():
            if hmac.compare_digest(provided_key, expected):
                request.state.integration_name = integration_name
                return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid integration key")


def verify_webhook_secret(settings: Settings, *, candidates: list[str | None]) -> None:
    configured = settings.webhook_shared_secret
    if configured is None or not configured.get_secret_value():
        return
    expected = configured.get_secret_value()
    if any(candidate and hmac.compare_digest(candidate, expected) for candidate in candidates):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


async def mkassa_api_error_handler(_: Request, exc: MKassaAPIError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "message": "MKassa API returned an error",
            "mkassa_status_code": exc.status_code,
            "mkassa_response": exc.response_text,
        },
    )


async def mkassa_transport_error_handler(_: Request, exc: MKassaTransportError):
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"message": str(exc)},
    )
