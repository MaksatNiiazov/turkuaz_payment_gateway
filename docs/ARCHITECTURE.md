# Production Architecture

This service is a payment gateway adapter. External systems should integrate with
our stable API, not directly with a bank-specific API.

## Layers

```text
1C / site / POS / future frontend
        |
        v
FastAPI public API
        |
        v
PaymentGateway
        |
        +-- MKassaProvider -> AsyncMKassaClient -> MKassa API
        +-- ODengiProvider -> AsyncODengiClient -> O!Dengi API
        +-- FutureBankProvider -> FutureBankClient -> another bank API
        |
        v
PaymentStore -> SQLite for the first production phase, demos, local runs, and tests
```

## Database Choice

Current database: SQLite.

The first production phase uses SQLite for every Turkuaz service. The application
still talks to the database through SQLAlchemy, so a later storage switch remains
a `DATABASE_URL` change plus migrations.

Default:

```env
DATABASE_URL=sqlite:///./data/payment_gateway.db
```

For local, demo, and the first production phase, `AUTO_CREATE_SCHEMA=true` can create tables on
startup. For stricter deploys, run Alembic first and start the app
with `AUTO_CREATE_SCHEMA=false`.

```bash
DATABASE_URL=sqlite:///./data/payment_gateway.db \
  .venv/bin/python -m alembic upgrade head
```

## Provider Boundary

Bank-specific logic must stay behind `PaymentProvider`.

`PaymentProvider` is an abstract base class and is the only shared provider
contract used by the gateway/service layers. Provider clients such as
`AsyncMKassaClient` and `AsyncODengiClient` stay concrete because they are
low-level HTTP adapters for different bank APIs.

Current providers:

```text
src/payment_gateway/api.py      -> HTTP-only FastAPI layer
src/payment_gateway/service.py  -> business use cases
src/payment_gateway/gateway.py          -> provider registry
src/payment_gateway/providers/mkassa.py -> low-level MKassa HTTP client and adapter
src/payment_gateway/providers/odengi.py -> low-level O!Dengi HTTP client and adapter
src/payment_gateway/store.py            -> SQL persistence
```

The intended dependency direction is:

```text
api -> service -> gateway/provider -> bank client
api -> service -> store
```

This keeps the design close to SOLID:

- Single responsibility: routes, use cases, provider clients, and persistence are separate.
- Open/closed: a new bank is added as a new provider without rewriting existing MKassa code.
- Dependency inversion: business flows depend on the provider abstract base class, not one concrete bank API.

When adding another bank:

1. Add a bank client that knows only that bank's HTTP API.
2. Add a provider inheriting from `PaymentProvider`.
3. Register it in `PaymentGateway`.
4. Keep public API payloads stable unless a new business capability is required.

Provider routing is intentionally internal:

```env
DEFAULT_PAYMENT_PROVIDER=mkassa
PAYMENT_PROVIDER_BY_INTEGRATION=1c_obank:odengi,site:mkassa,pos:odengi
```

External clients still send only `X-Integration-Key`. Provider-specific callback
URLs stay separate because banks call those endpoints directly:

```text
/api/v1/webhooks/mkassa
/api/v1/webhooks/odengi
```

## Public API Rule

External clients should use our API and `X-Integration-Key`.

Do not expose:

- MKassa API key;
- O!Dengi SID/password;
- bank-internal auth;
- raw bank endpoint URLs.

## Audit

The service persists:

- transactions;
- webhook events with duplicate detection;
- API access events by `integration_name`.

These tables are intentionally provider-aware, so future banks can reuse the
same operational and frontend screens.

For 1C invoice payments, clients should send the stable 1C document ID in
`metadata.invoice_id`. The store extracts it into `transactions.external_invoice_id`
so multiple provider transactions can be grouped by the same invoice without
depending on a human-readable invoice number or bank-specific IDs.

## Frontend Readiness

Frontend should use the same API as 1C/site/POS:

- create QR;
- render QR PNG;
- check transaction status;
- read local audit/debug data for support screens.

Do not build frontend logic around MKassa or O!Dengi field names directly. Treat
both as providers under the gateway.
