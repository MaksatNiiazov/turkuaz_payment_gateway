from __future__ import annotations

import io
import hmac
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

import qrcode
from fastapi import APIRouter, Body, Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader

from payment_gateway.config import Settings, get_settings
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
    WebhookAck,
    WebhookPayload,
)
from payment_gateway.providers.mkassa import (
    AsyncMKassaClient,
    MKassaAPIError,
    MKassaProvider,
    MKassaTransportError,
)
from payment_gateway.service import PaymentService
from payment_gateway.store import PaymentStore


integration_key_scheme = APIKeyHeader(
    name="X-Integration-Key",
    scheme_name="X-Integration-Key",
    auto_error=False,
    description=(
        "Integration key issued by Turkuaz for 1C/site/POS/ERP integrations. "
        "Paste only the key value, not 'integration_name:key'."
    ),
)

admin_key_scheme = APIKeyHeader(
    name="X-Admin-Key",
    scheme_name="X-Admin-Key",
    auto_error=False,
    description="Private key used only by the admin web service.",
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

ADMIN_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Payment Gateway Admin</title>
  <style>
    body { background: white; color: black; font-family: sans-serif; margin: 16px; }
    nav a { margin-right: 12px; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; }
    th, td { border: 1px solid #bbb; padding: 6px; text-align: left; vertical-align: top; }
    th { background: #f2f2f2; }
    input, select, button { margin: 4px 4px 4px 0; }
    pre { white-space: pre-wrap; max-width: 420px; margin: 0; }
    .muted { color: #555; }
  </style>
</head>
<body>
  <h1>Payment Gateway Admin</h1>
  <nav>
    <a href="/ui/transactions">Транзакции</a>
    <a href="/ui/webhooks">Webhook события</a>
    <a href="/ui/access-events">Доступы</a>
    <a href="/docs">Swagger</a>
  </nav>

  <section>
    <h2>Admin ключ</h2>
    <label>X-Admin-Key<br>
      <input id="adminKey" type="password" size="60" autocomplete="off">
    </label>
    <button type="button" onclick="saveKey()">Сохранить</button>
    <button type="button" onclick="clearKey()">Очистить</button>
    <span id="keyState" class="muted"></span>
  </section>

  <hr>

  <section id="controls"></section>
  <section id="content"></section>

  <script>
    const keyInput = document.getElementById("adminKey");
    const keyState = document.getElementById("keyState");
    const controls = document.getElementById("controls");
    const content = document.getElementById("content");

    keyInput.value = localStorage.getItem("adminKey") || "";
    updateKeyState();

    function saveKey() {
      localStorage.setItem("adminKey", keyInput.value);
      updateKeyState();
      loadPage();
    }

    function clearKey() {
      localStorage.removeItem("adminKey");
      keyInput.value = "";
      updateKeyState();
    }

    function updateKeyState() {
      keyState.textContent = keyInput.value ? "ключ задан" : "ключ не задан";
    }

    function headers() {
      return { "X-Admin-Key": keyInput.value };
    }

    function text(value) {
      if (value === null || value === undefined) return "";
      if (typeof value === "object") return JSON.stringify(value, null, 2);
      return String(value);
    }

    function cell(value) {
      const td = document.createElement("td");
      if (typeof value === "object" && value !== null) {
        const pre = document.createElement("pre");
        pre.textContent = text(value);
        td.appendChild(pre);
      } else {
        td.textContent = text(value);
      }
      return td;
    }

    function renderTable(columns, rows) {
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const column of columns) {
        const th = document.createElement("th");
        th.textContent = column.label;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      for (const row of rows) {
        const tr = document.createElement("tr");
        for (const column of columns) {
          tr.appendChild(cell(row[column.key]));
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      return table;
    }

    async function fetchJson(path) {
      const response = await fetch(path, { headers: headers() });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || `HTTP ${response.status}`);
      }
      return data;
    }

    function renderControls(kind) {
      const previousLimit = document.getElementById("limit")?.value || "50";
      const previousStatus = document.getElementById("status")?.value || "";
      const previousProvider = document.getElementById("provider")?.value || "";
      controls.innerHTML = "";
      const title = document.createElement("h2");
      title.textContent = kind;
      controls.appendChild(title);

      const limit = document.createElement("input");
      limit.id = "limit";
      limit.type = "number";
      limit.min = "1";
      limit.max = "500";
      limit.value = previousLimit;
      controls.append("Лимит: ", limit, " ");

      if (kind === "Транзакции") {
        const status = document.createElement("input");
        status.id = "status";
        status.placeholder = "status";
        status.value = previousStatus;
        const provider = document.createElement("input");
        provider.id = "provider";
        provider.placeholder = "provider";
        provider.value = previousProvider;
        controls.append(" Статус: ", status, " Provider: ", provider, " ");
      }

      const refresh = document.createElement("button");
      refresh.type = "button";
      refresh.textContent = "Обновить";
      refresh.onclick = loadPage;
      controls.appendChild(refresh);
    }

    async function loadTransactions() {
      renderControls("Транзакции");
      const params = new URLSearchParams({ limit: document.getElementById("limit").value || "50" });
      const status = document.getElementById("status").value.trim();
      const provider = document.getElementById("provider").value.trim();
      if (status) params.set("status", status);
      if (provider) params.set("provider", provider);
      const rows = await fetchJson(`/api/v1/local/transactions?${params.toString()}`);
      const columns = [
        { key: "id", label: "ID" },
        { key: "provider", label: "Provider" },
        { key: "status", label: "Status" },
        { key: "transaction_type", label: "Type" },
        { key: "amount", label: "Amount" },
        { key: "branch", label: "Branch" },
        { key: "cashier", label: "Cashier" },
        { key: "metadata", label: "Metadata" },
        { key: "updated_at", label: "Updated" },
      ];
      content.replaceChildren(renderTable(columns, rows));
    }

    async function loadWebhooks() {
      renderControls("Webhook события");
      const params = new URLSearchParams({ limit: document.getElementById("limit").value || "50" });
      const rows = await fetchJson(`/api/v1/local/webhooks?${params.toString()}`);
      const columns = [
        { key: "id", label: "ID" },
        { key: "provider", label: "Provider" },
        { key: "transaction_id", label: "Transaction" },
        { key: "status", label: "Status" },
        { key: "received_at", label: "Received" },
        { key: "payload", label: "Payload" },
      ];
      content.replaceChildren(renderTable(columns, rows));
    }

    async function loadAccessEvents() {
      renderControls("Доступы");
      const params = new URLSearchParams({ limit: document.getElementById("limit").value || "50" });
      const rows = await fetchJson(`/api/v1/local/access-events?${params.toString()}`);
      const columns = [
        { key: "id", label: "ID" },
        { key: "integration_name", label: "Integration" },
        { key: "method", label: "Method" },
        { key: "path", label: "Path" },
        { key: "status_code", label: "Code" },
        { key: "remote_addr", label: "Remote" },
        { key: "created_at", label: "Created" },
      ];
      content.replaceChildren(renderTable(columns, rows));
    }

    function loadPage() {
      content.textContent = "Загрузка...";
      const path = window.location.pathname;
      const loader = path.includes("/webhooks")
        ? loadWebhooks
        : path.includes("/access-events")
          ? loadAccessEvents
          : loadTransactions;
      loader().catch(err => {
        content.textContent = `Ошибка: ${err.message}`;
      });
    }

    loadPage();
  </script>
</body>
</html>
"""


def create_app(
    *,
    settings: Settings | None = None,
    client: AsyncMKassaClient | None = None,
    store: PaymentStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or get_settings()
        resolved_store = store or PaymentStore(resolved_settings.database_url)
        if resolved_settings.auto_create_schema:
            resolved_store.initialize()
        resolved_client = client or AsyncMKassaClient.from_settings(resolved_settings)
        resolved_gateway = PaymentGateway(
            [MKassaProvider(resolved_client)],
            default_provider=resolved_settings.default_payment_provider,
        )
        resolved_payment_service = PaymentService(
            gateway=resolved_gateway,
            store=resolved_store,
        )

        app.state.settings = resolved_settings
        app.state.store = resolved_store
        app.state.mkassa_client = resolved_client
        app.state.payment_gateway = resolved_gateway
        app.state.payment_service = resolved_payment_service
        try:
            yield
        finally:
            if client is None:
                await resolved_client.aclose()
            if store is None:
                resolved_store.close()

    app = FastAPI(
        title="Turkuaz Payment Gateway",
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
        "/ui",
        response_class=HTMLResponse,
        tags=["system"],
        summary="Admin UI",
        description="Basic browser pages for local transactions, webhooks, and API access events.",
    )
    async def admin_ui_root() -> HTMLResponse:
        return HTMLResponse(ADMIN_HTML)

    @app.get(
        "/ui/transactions",
        response_class=HTMLResponse,
        tags=["system"],
        summary="Transactions UI",
        description="Basic browser page for locally saved transactions.",
    )
    async def transactions_ui() -> HTMLResponse:
        return HTMLResponse(ADMIN_HTML)

    @app.get(
        "/ui/webhooks",
        response_class=HTMLResponse,
        tags=["system"],
        summary="Webhooks UI",
        description="Basic browser page for locally saved webhook events.",
    )
    async def webhooks_ui() -> HTMLResponse:
        return HTMLResponse(ADMIN_HTML)

    @app.get(
        "/ui/access-events",
        response_class=HTMLResponse,
        tags=["system"],
        summary="Access events UI",
        description="Basic browser page for integration access audit events.",
    )
    async def access_events_ui() -> HTMLResponse:
        return HTMLResponse(ADMIN_HTML)

    @app.get(
        "/health",
        tags=["system"],
        summary="Health check",
        description="Does not require `X-Integration-Key`.",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/health",
        tags=["system"],
        summary="Service health check",
        description="Standard Turkuaz service health endpoint.",
    )
    async def service_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/ready",
        tags=["system"],
        summary="Service readiness check",
        description="Standard Turkuaz service readiness endpoint.",
    )
    async def service_ready() -> dict[str, str]:
        return {"status": "ready"}

    protected_router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_integration_key)],
    )
    admin_router = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_admin_key)],
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
        return await payments(request).create_dynamic_qr(payload)

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
        return await payments(request).create_static_qr(payload)

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
        return await payments(request).create_dynamic_qr(payload)

    @protected_router.get(
        "/qr/render",
        tags=["qr"],
        summary="Render QR PNG",
        description="Renders a PNG QR image from `payment_token` or `static_qr_link`.",
        response_class=StreamingResponse,
    )
    async def render_qr(
        data: Annotated[
            str,
            Query(
                min_length=1,
                max_length=4096,
                description="QR payload, usually MKassa payment_token or static_qr_link.",
            ),
        ],
    ) -> StreamingResponse:
        return render_qr_png(data)

    @protected_router.get(
        "/qr/render/transaction/{transaction_id}",
        tags=["qr"],
        summary="Render QR PNG by transaction ID",
        description=(
            "Renders a PNG QR image from a locally saved dynamic `payment_token` "
            "or static `static_qr_link`. Useful for 1C print forms because the "
            "client only needs to keep the transaction ID."
        ),
        response_class=StreamingResponse,
    )
    async def render_qr_by_transaction(
        request: Request,
        transaction_id: str,
    ) -> StreamingResponse:
        item = storage(request).get_transaction(transaction_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

        qr_payload = item.get("payment_token") or item.get("static_qr_link")
        if not qr_payload:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction does not have a saved QR payload",
            )
        return render_qr_png(qr_payload)

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
        return await payments(request).create_static_qr(payload)

    @protected_router.get(
        "/transactions/{transaction_id}",
        response_model=Transaction,
        tags=["transactions"],
        summary="Get transaction status",
        description="Reads the current transaction state from MKassa by transaction ID.",
        response_description="Current MKassa transaction state.",
    )
    async def get_transaction(request: Request, transaction_id: str) -> Transaction:
        return await payments(request).get_transaction(transaction_id)

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
        return await payments(request).cancel_transaction(transaction_id)

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
        return await payments(request).list_transactions(
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
        return await payments(request).transaction_details(
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
        return await payments(request).branches(page=page)

    @protected_router.get(
        "/integration",
        tags=["system"],
        summary="Show current integration",
        description="Returns internal `integration_name` resolved from `X-Integration-Key`.",
    )
    async def current_integration(request: Request) -> dict[str, str]:
        return {"integration_name": request.state.integration_name}

    @admin_router.get(
        "/local/transactions",
        tags=["local"],
        summary="List locally saved transactions",
        description="Returns locally saved transaction states from callbacks or API calls.",
    )
    async def local_transactions(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500, description="Maximum rows to return.")] = 50,
        provider: Annotated[str | None, Query(description="Optional provider filter.")] = None,
        status_filter: Annotated[
            str | None,
            Query(alias="status", description="Optional transaction status filter."),
        ] = None,
    ) -> list[dict]:
        return storage(request).list_transactions(
            limit=limit,
            provider=provider,
            status=status_filter,
        )

    @admin_router.get(
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

    @admin_router.put(
        "/local/transactions/{transaction_id}/refresh",
        response_model=Transaction,
        tags=["local"],
        summary="Refresh local transaction status",
        description="Reads the current transaction state from MKassa and saves it locally.",
    )
    async def refresh_local_transaction(request: Request, transaction_id: str) -> Transaction:
        if storage(request).get_transaction(transaction_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
        return await payments(request).get_transaction(transaction_id)

    @admin_router.put(
        "/local/transactions/{transaction_id}/cancel",
        response_model=CancelResponse,
        tags=["local"],
        summary="Cancel local transaction from admin UI",
        description=(
            "Admin-only wrapper around MKassa dynamic QR cancellation. "
            "Use for unpaid dynamic QR transactions shown in the admin UI."
        ),
    )
    async def cancel_local_transaction(request: Request, transaction_id: str) -> CancelResponse:
        item = storage(request).get_transaction(transaction_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

        fresh = await payments(request).get_transaction(transaction_id)
        if fresh.transaction_type != "qr" or fresh.status not in {
            "inited",
            "waiting",
            "qr_scanned",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Transaction cannot be canceled from status {fresh.status or 'unknown'}",
            )
        return await payments(request).cancel_transaction(transaction_id)

    @admin_router.post(
        "/admin/qr/dynamic",
        response_model=DynamicQRResponse,
        tags=["local"],
        summary="Create dynamic QR from admin web UI",
        description="Admin-only QR demo endpoint used by the React admin interface.",
    )
    async def create_admin_dynamic_qr(
        request: Request,
        payload: DynamicQRCreate,
    ) -> DynamicQRResponse:
        return await payments(request).create_dynamic_qr(payload)

    @admin_router.get(
        "/admin/qr/render",
        tags=["local"],
        summary="Render QR image from admin web UI",
        description="Admin-only PNG QR renderer used by the React admin interface.",
    )
    async def render_admin_qr(
        data: Annotated[str, Query(min_length=1, max_length=4096, description="QR payload.")],
    ) -> StreamingResponse:
        return render_qr_png(data)

    @admin_router.get(
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

    @admin_router.get(
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
            "MKassa expects a public HTTPS URL on port 443 and a `200 OK` response."
        ),
        response_description="Webhook acceptance acknowledgement.",
    )
    async def mkassa_webhook(
        request: Request,
        payload: WebhookPayload,
    ) -> WebhookAck:
        result = payments(request).save_webhook(payload)
        return WebhookAck(transaction_id=result.transaction_id, duplicate=result.duplicate)

    app.include_router(protected_router)
    app.include_router(admin_router)
    app.include_router(webhook_router)
    app.add_exception_handler(MKassaAPIError, mkassa_api_error_handler)
    app.add_exception_handler(MKassaTransportError, mkassa_transport_error_handler)
    return app


def settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def payments(request: Request) -> PaymentService:
    return request.app.state.payment_service


def storage(request: Request) -> PaymentStore:
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


def render_qr_png(data: str) -> StreamingResponse:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


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


async def require_admin_key(
    request: Request,
    x_admin_key: str | None = Depends(admin_key_scheme),
) -> None:
    configured = settings_from_request(request).payment_admin_api_key
    if configured is None or not configured.get_secret_value().strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin key is not configured",
        )
    expected = configured.get_secret_value().strip()
    if x_admin_key and hmac.compare_digest(x_admin_key, expected):
        request.state.integration_name = "admin"
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


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
