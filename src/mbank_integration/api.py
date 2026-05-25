from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, Form, HTTPException, Query, Request, status
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
    description=(
        "Integration key issued by Turkuaz for 1C/site/POS/ERP integrations. "
        "Paste only the key value, not 'integration_name:key'."
    ),
)

OPENAPI_TAGS = [
    {
        "name": "qr",
        "description": "Create dynamic and static MKassa QR links.",
    },
    {
        "name": "transactions",
        "description": "Read MKassa transaction status, cancel dynamic QR, and fetch reports.",
    },
    {
        "name": "branches",
        "description": "Read MKassa branches and cashiers when the MKassa key has permission.",
    },
    {
        "name": "webhooks",
        "description": "Endpoint called by MKassa after payment status changes.",
    },
    {
        "name": "local",
        "description": "Local audit and saved callback data for support/debugging.",
    },
    {
        "name": "system",
        "description": "Health and integration-key diagnostics.",
    },
]


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
        description=(
            "Standalone adapter for MKassa QR payments.\n\n"
            "Use **Authorize** and paste the issued `X-Integration-Key` once. "
            "Amounts are sent in **tyiyn**: `100` means `1 som`. "
            "For Tiger facture codes use `metadata.invoice_number`."
        ),
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
        swagger_ui_parameters={
            "docExpansion": "none",
            "defaultModelsExpandDepth": 1,
            "defaultModelExpandDepth": 2,
            "displayRequestDuration": True,
            "filter": True,
            "persistAuthorization": True,
            "tryItOutEnabled": True,
        },
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

    @app.get(
        "/health",
        tags=["system"],
        summary="Health check",
        description="Does not require `X-Integration-Key`.",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    protected_router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_integration_key)],
    )

    @protected_router.post(
        "/qr/dynamic",
        response_model=DynamicQRResponse,
        tags=["qr"],
        summary="Create dynamic QR",
        description=(
            "Creates a dynamic MKassa QR transaction and returns `payment_token`, "
            "which can be encoded as a QR code. Dynamic QR usually expires after "
            "about 60 seconds unless MKassa enabled `is_long_living`."
        ),
        response_description="Dynamic QR transaction created by MKassa.",
    )
    async def create_dynamic_qr(
        request: Request,
        payload: DynamicQRCreate = Body(
            openapi_examples={
                "tiger_invoice": {
                    "summary": "Tiger facture code",
                    "description": "Recommended request for Tiger/1C integration.",
                    "value": {
                        "amount": 100,
                        "metadata": {
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
                "explicit_branch_cashier": {
                    "summary": "Explicit branch and cashier",
                    "description": "Use when MKassa asks to send branch/cashier explicitly.",
                    "value": {
                        "amount": 100,
                        "branch": 236366,
                        "cashier": 130610,
                        "is_long_living": True,
                        "metadata": {
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
            }
        ),
    ) -> DynamicQRResponse:
        response = await mkassa(request).create_dynamic_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.post(
        "/qr/static",
        response_model=StaticQRResponse,
        tags=["qr"],
        summary="Create static QR",
        description=(
            "Creates a static MKassa QR link. `branch` and `cashier` are required "
            "by MKassa for static QR. Static QR is not canceled through this API."
        ),
        response_description="Static QR link created by MKassa.",
    )
    async def create_static_qr(
        request: Request,
        payload: StaticQRCreate = Body(
            openapi_examples={
                "fixed_amount": {
                    "summary": "Fixed amount static QR",
                    "value": {
                        "branch": 236366,
                        "cashier": 130610,
                        "amount": 100,
                        "change_amount": False,
                        "metadata": {
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
                "accounting_metadata": {
                    "summary": "Accounting metadata",
                    "value": {
                        "branch": 236366,
                        "cashier": 130610,
                        "amount": 100,
                        "change_amount": False,
                        "metadata": {
                            "payer_code": "12345678901234",
                            "payer_full_name": "ОсОО Тест",
                            "invoice_number": "TIGER-FACTURE-1001",
                        },
                    },
                },
            }
        ),
    ) -> StaticQRResponse:
        response = await mkassa(request).create_static_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.post(
        "/qr/dynamic/form",
        response_model=DynamicQRResponse,
        tags=["qr"],
        summary="Create dynamic QR using form fields",
        description=(
            "Swagger-friendly version of dynamic QR creation. Fill separate fields instead "
            "of writing JSON. Internally sends the same MKassa payload as `/qr/dynamic`."
        ),
        response_description="Dynamic QR transaction created by MKassa.",
    )
    async def create_dynamic_qr_form(
        request: Request,
        amount: Annotated[int, Form(gt=0, description="Amount in tyiyn. 100 = 1 som.")],
        branch: Annotated[
            int | None,
            Form(description="Optional MKassa branch ID for dynamic QR."),
        ] = None,
        cashier: Annotated[
            int | None,
            Form(description="Optional MKassa cashier ID for dynamic QR."),
        ] = None,
        is_long_living: Annotated[
            bool | None,
            Form(description="Use only if MKassa enabled long-living dynamic QR."),
        ] = None,
        invoice_number: Annotated[
            str | None,
            Form(description="Tiger facture code. Goes to metadata.invoice_number."),
        ] = None,
        source: Annotated[
            str | None,
            Form(description="Optional source label. Example: tiger."),
        ] = "tiger",
        payer_code: Annotated[
            str | None,
            Form(description="Optional payer INN/code. Goes to metadata.payer_code."),
        ] = None,
        payer_full_name: Annotated[
            str | None,
            Form(description="Optional payer name. Goes to metadata.payer_full_name."),
        ] = None,
        metadata_key_1: Annotated[
            str | None,
            Form(description="Optional custom metadata key."),
        ] = None,
        metadata_value_1: Annotated[
            str | None,
            Form(description="Optional custom metadata value."),
        ] = None,
    ) -> DynamicQRResponse:
        payload = DynamicQRCreate(
            amount=amount,
            branch=branch,
            cashier=cashier,
            is_long_living=is_long_living,
            metadata=build_form_metadata(
                invoice_number=invoice_number,
                source=source,
                payer_code=payer_code,
                payer_full_name=payer_full_name,
                metadata_key_1=metadata_key_1,
                metadata_value_1=metadata_value_1,
            ),
        )
        response = await mkassa(request).create_dynamic_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.post(
        "/qr/static/form",
        response_model=StaticQRResponse,
        tags=["qr"],
        summary="Create static QR using form fields",
        description=(
            "Swagger-friendly version of static QR creation. `branch` and `cashier` "
            "are required by MKassa for static QR."
        ),
        response_description="Static QR link created by MKassa.",
    )
    async def create_static_qr_form(
        request: Request,
        branch: Annotated[int, Form(gt=0, description="Required MKassa branch ID.")],
        cashier: Annotated[int, Form(gt=0, description="Required MKassa cashier ID.")],
        amount: Annotated[
            int | None,
            Form(gt=0, description="Optional amount in tyiyn. 100 = 1 som."),
        ] = None,
        change_amount: Annotated[
            bool | None,
            Form(description="Whether payer may edit amount during payment."),
        ] = False,
        invoice_number: Annotated[
            str | None,
            Form(description="Tiger facture code. Goes to metadata.invoice_number."),
        ] = None,
        source: Annotated[
            str | None,
            Form(description="Optional source label. Example: tiger."),
        ] = "tiger",
        payer_code: Annotated[
            str | None,
            Form(description="Optional payer INN/code. Goes to metadata.payer_code."),
        ] = None,
        payer_full_name: Annotated[
            str | None,
            Form(description="Optional payer name. Goes to metadata.payer_full_name."),
        ] = None,
        metadata_key_1: Annotated[
            str | None,
            Form(description="Optional custom metadata key."),
        ] = None,
        metadata_value_1: Annotated[
            str | None,
            Form(description="Optional custom metadata value."),
        ] = None,
    ) -> StaticQRResponse:
        payload = StaticQRCreate(
            branch=branch,
            cashier=cashier,
            amount=amount,
            change_amount=change_amount,
            metadata=build_form_metadata(
                invoice_number=invoice_number,
                source=source,
                payer_code=payer_code,
                payer_full_name=payer_full_name,
                metadata_key_1=metadata_key_1,
                metadata_value_1=metadata_value_1,
            ),
        )
        response = await mkassa(request).create_static_qr(payload)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.get(
        "/transactions/{transaction_id}",
        response_model=Transaction,
        tags=["transactions"],
        summary="Get transaction status",
        description="Reads the current transaction state from MKassa by transaction ID.",
        response_description="Current MKassa transaction state.",
    )
    async def get_transaction(request: Request, transaction_id: str) -> Transaction:
        response = await mkassa(request).get_transaction(transaction_id)
        storage(request).upsert_transaction_payload(response)
        return response

    @protected_router.put(
        "/transactions/{transaction_id}/cancel",
        response_model=CancelResponse,
        tags=["transactions"],
        summary="Cancel dynamic QR",
        description=(
            "Cancels a dynamic QR transaction before payment. MKassa may need a few "
            "seconds before `GET /transactions/{id}` starts returning `canceled`."
        ),
        response_description="MKassa cancel response.",
    )
    async def cancel_transaction(request: Request, transaction_id: str) -> CancelResponse:
        response = await mkassa(request).cancel_transaction(transaction_id)
        return response

    @protected_router.get(
        "/transactions",
        response_model=TransactionListResponse,
        tags=["transactions"],
        summary="List/filter transactions",
        description=(
            "Reads MKassa transaction list. Without dates MKassa returns its default period. "
            "Use `start_date` and `end_date` for reconciliation."
        ),
        response_description="Paginated MKassa transaction list.",
    )
    async def list_transactions(
        request: Request,
        page: Annotated[int | None, Query(ge=1, description="MKassa page number.")] = None,
        status_filter: Annotated[
            str | None,
            Query(alias="status", description="MKassa status, e.g. paid, failed, canceled."),
        ] = None,
        transaction_type: Annotated[
            str | None,
            Query(alias="type", description="MKassa transaction type, e.g. qr, static, card."),
        ] = None,
        start_date: Annotated[
            date | None,
            Query(description="Start date for filtering, YYYY-MM-DD."),
        ] = None,
        end_date: Annotated[
            date | None,
            Query(description="End date for filtering, YYYY-MM-DD."),
        ] = None,
        branch: Annotated[int | None, Query(gt=0, description="MKassa branch ID.")] = None,
        cashier: Annotated[int | None, Query(gt=0, description="MKassa cashier ID.")] = None,
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
        summary="Get transaction details report",
        description="Reads detailed MKassa transaction report for a date period.",
        response_description="Paginated MKassa transaction details report.",
    )
    async def transaction_details(
        request: Request,
        start_date: Annotated[date, Query(description="Start date, YYYY-MM-DD.")],
        end_date: Annotated[date, Query(description="End date, YYYY-MM-DD.")],
        page: Annotated[int | None, Query(ge=1, description="MKassa page number.")] = None,
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
        summary="List branches and cashiers",
        description=(
            "Reads MKassa branches and cashiers. Some cashier-level MKassa keys return "
            "`403 permission_denied`; in that case use branch/cashier IDs provided by MKassa."
        ),
        response_description="Paginated MKassa branch list.",
    )
    async def branches(
        request: Request,
        page: Annotated[int | None, Query(ge=1, description="MKassa page number.")] = None,
    ) -> BranchListResponse:
        return await mkassa(request).branches(page=page)

    @protected_router.get(
        "/integration",
        tags=["system"],
        summary="Show current integration",
        description="Returns internal `integration_name` resolved from `X-Integration-Key`.",
    )
    async def current_integration(request: Request) -> dict[str, str]:
        return {"integration_name": request.state.integration_name}

    @protected_router.get(
        "/local/transactions/{transaction_id}",
        tags=["local"],
        summary="Get locally saved transaction",
        description="Returns the last locally saved transaction state from callbacks or API calls.",
    )
    async def local_transaction(request: Request, transaction_id: str) -> dict:
        item = storage(request).get_transaction(transaction_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
        return item

    @protected_router.get(
        "/local/webhooks",
        tags=["local"],
        summary="List local webhook events",
        description="Shows recently received MKassa webhook payloads saved in local SQLite.",
    )
    async def local_webhooks(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500, description="Maximum rows to return.")] = 50,
    ) -> list[dict]:
        return storage(request).list_webhook_events(limit=limit)

    @protected_router.get(
        "/local/access-events",
        tags=["local"],
        summary="List local API access events",
        description="Shows recent calls to protected endpoints grouped by integration key owner.",
    )
    async def local_access_events(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500, description="Maximum rows to return.")] = 50,
    ) -> list[dict]:
        return storage(request).list_api_access_events(limit=limit)

    webhook_router = APIRouter(prefix="/api/v1")

    @webhook_router.post(
        "/webhooks/mkassa",
        response_model=WebhookAck,
        tags=["webhooks"],
        summary="Receive MKassa webhook",
        description=(
            "Public callback endpoint for MKassa. Does not require `X-Integration-Key`. "
            "If `WEBHOOK_SHARED_SECRET` is set, add `?secret=<value>` to the callback URL."
        ),
        response_description="Webhook acceptance acknowledgement.",
    )
    async def mkassa_webhook(
        request: Request,
        payload: WebhookPayload,
        secret: str | None = None,
    ) -> WebhookAck:
        verify_webhook_secret(
            settings_from_request(request),
            candidate=secret,
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


def build_form_metadata(
    *,
    invoice_number: str | None = None,
    source: str | None = None,
    payer_code: str | None = None,
    payer_full_name: str | None = None,
    metadata_key_1: str | None = None,
    metadata_value_1: str | None = None,
) -> dict[str, str] | None:
    metadata: dict[str, str] = {}
    for key, value in {
        "invoice_number": invoice_number,
        "source": source,
        "payer_code": payer_code,
        "payer_full_name": payer_full_name,
    }.items():
        if value is not None and value.strip():
            metadata[key] = value.strip()

    if metadata_key_1 is not None and metadata_key_1.strip():
        metadata[metadata_key_1.strip()] = (metadata_value_1 or "").strip()

    return metadata or None


async def require_integration_key(
    request: Request,
    x_integration_key: str | None = Depends(integration_key_scheme),
) -> None:
    key_pool = settings_from_request(request).integration_key_pool
    if not key_pool:
        request.state.integration_name = "anonymous"
        return
    if x_integration_key:
        for integration_name, expected in key_pool.items():
            if hmac.compare_digest(x_integration_key, expected):
                request.state.integration_name = integration_name
                return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid integration key")


def verify_webhook_secret(settings: Settings, *, candidate: str | None) -> None:
    configured = settings.webhook_shared_secret
    if configured is None or not configured.get_secret_value():
        return
    expected = configured.get_secret_value()
    if candidate and hmac.compare_digest(candidate, expected):
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
