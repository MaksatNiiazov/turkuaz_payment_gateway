# Tiger Integration Plan

This note describes how PaymentGateway should integrate with Logo Tiger / Unity
based on the received LObjects.dll guide.

## Main Decision

Do not call `LObjects.dll` directly from PaymentGateway.

`LObjects.dll` is a Windows COM library and needs Logo Objects runtime licensing,
DLL registration, correct x86/x64 bitness, a Logo user, firm number, and period
number. PaymentGateway is a standalone FastAPI service and should stay an HTTP
payment adapter.

The right production shape is:

```text
1C / site / POS
        |
        v
PaymentGateway
        |
        v
Tiger Integration API on Windows
        |
        v
Queue / incoming payments table
        |
        v
Windows worker
        |
        v
LObjects.dll / UnityObjects COM API
        |
        v
Logo Tiger
```

## Current PaymentGateway Side

PaymentGateway already has the important business key for invoice-linked
payments:

- 1C sends the stable invoice/document ID in `metadata.invoice_id`.
- PaymentGateway stores it as `transactions.external_invoice_id`.
- Human-readable invoice or facture number stays in `metadata.invoice_number`.
- Provider-specific bank IDs stay separate from the 1C/Tiger invoice ID.

This means Tiger integration should reconcile by:

```text
invoice_id        -> stable 1C document / invoice ID
invoice_number    -> visible facture / document number
payment id        -> unique paid transaction ID for idempotency
provider          -> mkassa / odengi
```

## Verified Tiger Server Facts

Read-only checks on the Tiger server confirmed:

- `LObjects.dll` exists at `C:\LOGO\TIGER3ENT\LObjects.dll`.
- `REGISTER.BAT` exists at `C:\LOGO\TIGER3ENT\REGISTER.BAT`.
- `UnityObjects.UnityApplication` is registered in COM.
- `UnityApplication.Version()` returns `Logo Objects 030700`.
- `UnityApplication.GetAppPath()` returns `C:\LOGO\TIGER3ENT\`.
- Working login flow is:

```text
Connect() -> UserLogin(username, password) -> CompanyLogin(126)
```

- After login, `CurrentFirm=126`, `CurrentPeriod=1`.
- `NewQuery()` and `OpenDirect()` can read Tiger tables through LObjects.

No document write was tested.

Detailed progress log: [TIGER_PROGRESS_LOG.md](TIGER_PROGRESS_LOG.md).

## Proposed Contract To Tiger Integration API

When a payment is confirmed as paid, PaymentGateway should send one payment event
to the Windows Tiger Integration API.

Recommended endpoint on the Windows side:

```http
POST /api/payments
X-Integration-Key: <server-to-server-secret>
Content-Type: application/json
```

Recommended payload:

```json
{
  "externalPaymentId": "odengi:172030403548",
  "gatewayTransactionId": "550e8400-e29b-41d4-a716-446655440000",
  "provider": "odengi",
  "providerPaymentId": "172030403548",
  "invoiceId": "550e8400-e29b-41d4-a716-446655440000",
  "invoiceNumber": "TIGER-FACTURE-1001",
  "paidAt": "2026-06-19T10:30:00+06:00",
  "amountTyiyn": 1500000,
  "amount": 15000.0,
  "currency": "KGS",
  "clientCode": "CARI.001",
  "clientName": "ОсОО Тест",
  "paymentMethod": "qr",
  "description": "Оплата по накладной TIGER-FACTURE-1001"
}
```

Idempotency rule:

```text
externalPaymentId = provider + ":" + providerPaymentId
```

If `providerPaymentId` is missing, use:

```text
externalPaymentId = provider + ":" + gatewayTransactionId
```

The Tiger side must reject or return the existing result for repeated
`externalPaymentId` values. It must not create the same Tiger payment document
twice.

## What The Tiger Windows Service Should Do

The Windows service should:

1. Accept the HTTP request.
2. Validate the server-to-server key.
3. Save the incoming payment to its own table first.
4. Return quickly to PaymentGateway.
5. Let a worker process queued payments.
6. Connect to Logo via `UnityObjects`.
7. Find the client and invoice/order by configured fields.
8. Create the agreed payment document.
9. Save the Logo document number.
10. Mark the incoming payment as `Success` or `Error`.
11. Retry temporary errors with a retry limit.

Recommended incoming table:

```text
IncomingPayments
- Id
- ExternalPaymentId
- GatewayTransactionId
- Provider
- ProviderPaymentId
- InvoiceId
- InvoiceNumber
- ClientCode
- Amount
- Currency
- Status: New / Processing / Success / Error
- LogoDocumentNumber
- ErrorMessage
- RetryCount
- CreatedAt
- ProcessedAt
```

## What Must Be Clarified Before Coding Tiger Documents

The received guide is enough for architecture, but not enough to create real
Tiger accounting documents. These questions must be answered by the Logo/Tiger
implementer or accountant:

1. Is Logo Objects / LObjects runtime licensed and active?
2. Where exactly is `LObjects.dll` installed?
3. Is it registered through `REGISTER.BAT` or `regsvr32`?
4. Is the DLL 32-bit or 64-bit?
5. Which firm number and period number should be used?
6. Which Logo user should the integration use?
7. Which rights should that user have?
8. What exact Tiger document type should represent a paid QR invoice?
9. Which fields are mandatory for that document type?
10. Which cash, bank, warehouse, department, and currency codes should be used?
11. How should external payment methods map to Tiger accounts?
12. Is there a test Tiger database for first writes?

Until these are known, PaymentGateway can prepare and deliver payment events, but
the Tiger service cannot safely create accounting documents.

## Implementation Phases

### Phase 1 - Confirm Tiger Access

On the Windows/Tiger server:

1. Confirm `LObjects.dll` is registered.
2. Create a small C# Console App on .NET Framework 4.7.2 or 4.8.
3. Add `UnityObjects Library`.
4. Run `Connect()`, `UserLogin(username, password)`, `CompanyLogin(firmNo)`.
5. Confirm a simple read or test object creation in a test database.

If `Connect()` does not work, stop here and fix Logo licensing, registration,
bitness, credentials, firm, or period.

### Phase 2 - Build Tiger Integration API

Build an ASP.NET API on the Windows server:

- `POST /api/payments` to accept paid payment events.
- `GET /api/payments/{externalPaymentId}` for diagnostics.
- Local table for queued incoming payments.
- Worker service that writes to Logo through LObjects.

### Phase 3 - Wire PaymentGateway

After the Windows endpoint is available, add optional PaymentGateway settings:

```env
TIGER_INTEGRATION_URL=https://tiger-integration.local/api/payments
TIGER_INTEGRATION_KEY=<server-to-server-secret>
TIGER_INTEGRATION_ENABLED=true
```

PaymentGateway should send to Tiger only when a local transaction status becomes
`paid`.

Before outbound delivery is implemented, verify the local event shape with:

```http
GET /api/v1/local/transactions/{transaction_id}/tiger-event-preview
X-Admin-Key: <admin-secret>
```

This endpoint only builds the JSON event from a saved paid transaction. It does
not call Tiger or any external integration service.

Recommended behavior:

- do not block bank webhook success if Tiger is temporarily unavailable;
- store delivery attempts;
- retry delivery;
- expose local delivery status under `/api/v1/local/*`;
- keep the public client auth mechanism as `X-Integration-Key`.

## 1C Payload Reminder

1C should continue creating QR through PaymentGateway and include the stable
invoice ID:

```json
{
  "amount": 1500000,
  "metadata": {
    "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
    "invoice_number": "TIGER-FACTURE-1001",
    "source": "1c",
    "client_code": "CARI.001"
  }
}
```

Use `invoice_id` as the business key. Use `invoice_number` only for display and
manual search.

## Practical Next Step

The next real step is the Windows/Tiger worker smoke test:

```text
cd TigerIntegrationWorker
dotnet run
GET /tiger/version
POST /tiger/test-login
GET /tiger/clients/sample
```

Once that works on the Tiger server and the document type is confirmed, the
PaymentGateway side can add a small outbound delivery module for paid
transactions.
