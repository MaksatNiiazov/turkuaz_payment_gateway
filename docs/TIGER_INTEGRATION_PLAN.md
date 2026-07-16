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
- SSMS confirmed that `LG_126_01_INVOICE`, `LG_126_01_BNFICHE`,
  `LG_126_01_BNFLINE`, `LG_126_BANKACC`, `LG_126_BNCARD` and
  `LG_126_CLCARD` exist in the database.
- LObjects `NewQuery` reads `LG_126_CLCARD`, but attempts to query
  `LG_126_01_INVOICE` and `SYS.TABLES` returned error `-10`. Schema discovery
  therefore uses read-only SSMS queries.
- A 100-row invoice sample confirmed that `DOCODE` is optional and repeated,
  so it cannot be the integration id. `LOGICALREF` is the preferred internal
  Tiger identifier; `FICHENO` remains a possible visible identifier when paired
  with firm, period and amount.
- Bank metadata provided the candidate join path
  `BNFLINE -> BNFICHE/BNCARD/BANKACC/CLCARD`. `BNFLINE` contains the amount,
  currency, client, bank account and external-reference fields needed to model
  an incoming payment.
- Existing rows confirmed that an incoming bank receipt uses `TRCODE=3`,
  `TRANSTYPE=1`, `SIGN=0` in this database. The sampled rows had no direct
  invoice link (`PAYMENTREF`, `CLFLINEREF`, `CLFICHEREF` were zero), so debt
  closing must be investigated separately.
- `LG_126_01_PAYTRANS` contains debt-closing and bank-link fields including
  `FICHEREF`, `FICHELINEREF`, `TOTAL`, `PAID`, `CROSSREF`, `BANKACCREF`,
  `BNFCHREF` and `BNFLNREF`. This is the next table to inspect for invoice
  payment status and links to bank documents.
- In this database, aggregation showed that `PAID`, `CROSSREF`, `BANKACCREF`,
  `BNFCHREF` and `BNFLNREF` are not populated in the sampled `PAYTRANS`
  groups. `PAYTRANS` is useful for invoice debt rows, but actual payment links
  likely need `CLFICHE`/`CLFLINE` plus bank tables.
- Read-only schema inspection confirmed that `CLFLINE` contains
  `MODULENR`, `SOURCEFREF`, `PAYMENTREF`, `BANKACCREF` and `BNACCREF`;
  `CLFICHE` contains `INVOREF`, `CLCARDREF`, `BANKACCREF` and `BNACCREF`.
  These are the current candidates for linking a bank receipt to the client
  ledger and payment schedule, but the exact joins still require row-level
  confirmation.
- Aggregation identified bank-origin client ledger rows as `MODULENR=7`:
  `SIGN=1/TRCODE=20` and `SIGN=0/TRCODE=21`. Every such row has
  `SOURCEFREF` and `BANKACCREF`, while `PAYMENTREF`, `BNACCREF` and
  `EXTENREF` are zero. This supports bank-account identification but suggests
  that invoice allocation is not recorded through `CLFLINE.PAYMENTREF` in
  this database.
- Row-level joins confirmed the actual bank receipt path:
  `BNFICHE(TRCODE=3) -> BNFLINE(TRCODE=3, TRANSTYPE=1, SIGN=0) ->`
  `CLFLINE(MODULENR=7, TRCODE=20, SIGN=1)`. The joins are
  `BNFLINE.SOURCEFREF = BNFICHE.LOGICALREF` and
  `CLFLINE.SOURCEFREF = BNFLINE.LOGICALREF`; amounts, clients and bank
  accounts match. This is sufficient to validate a future LObjects-created
  bank receipt, but it does not yet prove invoice-level allocation.
- `LOGICALREF` values are table-local. The bank-to-ledger join must include
  both `CLFLINE.MODULENR=7` and
  `CLFLINE.SOURCEFREF=BNFLINE.LOGICALREF`; omitting the module can produce
  false matches to invoice ledger rows. A `BNFICHE` may also contain lines
  for multiple bank accounts, so provider mapping belongs on each
  `BNFLINE.BNACCREF`.
- The corrected join was validated over all 37 lines of `BNFICHE=756`: every
  bank line had one module-7 client ledger row, every amount matched, and the
  line sum `4,471,080.67` equaled `BNFICHE.DEBITTOT`. The document contained
  three bank accounts, confirming that bank selection is a line-level field.
- A client-ledger timeline confirmed that invoice rows use
  `MODULENR=4/TRCODE=38/SIGN=0` with
  `CLFLINE.SOURCEFREF = INVOICE.LOGICALREF`, while bank receipts remain
  separate `MODULENR=7` rows. `PAYMENTREF` is zero on both sides and receipt
  amounts are not tied to one invoice. PaymentGateway must therefore remain
  the source of truth for invoice payment status; Tiger receives the resulting
  bank receipt unless an additional Tiger allocation mechanism is explicitly
  identified.
- LObjects exposes `DebtClose`, `DebtCloseFIFO` and `RollBackDebtClose`.
  Video inspection confirmed that `DebtClose` receives two
  `PAYTRANS.LOGICALREF` values plus amount and exchange rates. It must not be
  called with invoice or bank-voucher references and remains a write operation.
- Provider mapping must use an approved `BANK_ACCOUNT_CODE`, not a substring of
  the bank/account display name.

No document write was tested.

The exact LObjects object type is now confirmed from the installed type
library: `DataObjectType.doBankVoucher = 24`. This is the object that must be
used for the future `BNFICHE/BNFLINE` write; `doBank=22` and
`doBankAccount=23` are master-data objects.

An in-memory smoke test against test firm `923`, period `1`, confirmed that
`NewDataObject(24)` and `IData.New()` work. No `Post()` was called. The object
exposes `DataFields`, validation/error collections, XML import/export and
`Post()`, while `IDataFields` exposes indexed and named field access.

A controlled write test then succeeded in test firm `923`, period `1`:
`doBankVoucher=24`, header `TYPE=3`, one incoming line with
`TYPE=1/TRCODE=3/MODULENR=7/SIGN=0`, amount `1`, client code
`120.04.2.02.4456` and bank account code `10202 102.01.001`. LObjects returned
`LOGICALREF=1002`, `FICHENO=00000005`, with zero validation errors and warnings.
No direct SQL write was used.

However, a subsequent `IData.Read(1002)` returned zero transaction lines. The
test therefore proves header creation only, not a complete bank receipt.
`AppendLine2()` must be replaced with `AppendLine()` plus `Lines.Item(0)`, and
the persisted line count must be verified before enabling worker writes.

İvmebilişim independently confirmed the intermediate-service architecture:
external data is sent according to field rules, and a Logo Objects layer
periodically creates the appropriate Tiger voucher. Our deployment uses an
outbound pull from the internal Windows worker to PaymentGateway, avoiding any
public inbound access to the Tiger server.

The supplied C# source samples do not contain a bank-voucher implementation,
but they confirm the supported collection pattern: `AppendLine()` followed by
`Lines[Lines.Count - 1]`, named field assignment, `Post()` and
`ValidateErrors`. They also confirm XML string import/export. The next safest
discovery step is therefore a read-only XML export of an existing valid bank
voucher from the installed Tiger version. Official Polaris documentation
confirms its XML root as `BANK_VOUCHERS` (REST resource `bankVouchers`). Use
`Read(LOGICALREF)` followed by `ExportToXMLStr("BANK_VOUCHERS", ...)`.

Polaris confirms that `AppendLine()` is the generic supported line-creation
method for building object collections, but controlled `doBankVoucher=24` tests
showed it must not be used as our production append strategy for already posted
bank vouchers: the line was added in memory and `Post()` returned true, while
SQL read-back still showed the original line count. Full XML `DBOP="UPD"` with
existing lines plus a new line also failed with `DBError=23000`.

The confirmed append strategy for an already posted bank voucher is to let
Tiger produce the update shape first: `Read(LOGICALREF)`,
`ExportToXML("BANK_VOUCHERS", file)`, change the exported Tiger XML to
`DBOP="UPD"`, add the new `TRANSACTION`, update `TOTAL_DEBIT`, then
`ImportFromXmlStr("BANK_VOUCHERS", xml)` and `Post()`. In `923/1`, the
`full-export-upd` debug strategy passed a repeated append test with one base
voucher plus three appended payments: one voucher, four lines, total amount
`4`, and every expected `BNFLINE.LINEEXP` marker exactly once. This makes daily
grouping by bank technically feasible, provided each append is serialized and
verified after posting.

Logo Objects licensing is server-based but requires an installed runtime
license. Error `-13` means the entitlement is missing and `-93` means the
terminal limit was exceeded. Serialize COM work in one dedicated Tiger
session. After `Post()`, call `Read(LOGICALREF)` again and verify persisted
`TRANSACTIONS` before acknowledging the PaymentGateway event. Persist both
numeric and textual errors because identifiers/messages can differ by version
and locale.

Detailed progress log: [TIGER_PROGRESS_LOG.md](TIGER_PROGRESS_LOG.md).
Read-only command log: [TIGER_READONLY_DISCOVERY.md](TIGER_READONLY_DISCOVERY.md).

## Proposed Contract To Tiger Integration

PaymentGateway can create multiple bank transactions for one 1C/Tiger invoice
because the same invoice may be payable through several banks. Tiger must not
receive every bank transaction. Tiger should receive one invoice-level event only
after the invoice is actually paid by one provider.

The winning paid provider still matters because the payment must land on the
correct Tiger bank account. Therefore the event contains both:

- invoice-level fields: `invoiceId`, `invoiceNumber`, `amount`, `paidAt`;
- winning-payment fields: `paidTransactionId`, `paidProvider`,
  `providerPaymentId`, `targetBankCode`, `targetBankAccountCode`.

Each printable QR configuration stores its own Tiger `BANKACC.CODE`. When 1C
creates the invoice QR bundle, that account code is copied into the payment
metadata. The paid QR therefore determines the bank account without guessing
from the provider name:

```text
print QR code -> tiger_bank_account_code -> targetBankAccountCode
-> BANK_VOUCHER.TRANSACTION.BANKACC_CODE
```

`clientCode` is supplied by 1C as the invoice customer's `CLCARD.CODE`.

Recommended pull endpoint on PaymentGateway:

```http
GET /api/v1/local/tiger/invoice-events/pending?limit=20
X-Integration-Key: <tiger-worker-secret>
```

`<tiger-worker-secret>` must be the value from an `INTEGRATION_KEYS` entry named
`tiger`, for example `INTEGRATION_KEYS=1c:...,tiger:...`. 1C/POS/site keys are
not allowed to poll or acknowledge the Tiger queue.

The pull atomically changes selected rows from `pending` to `processing`.
Another worker cannot receive the same active lease. If no result is reported
within `EXPORT_QUEUE_LEASE_SECONDS`, PaymentGateway makes the event available
again.

Recommended event payload:

```json
{
  "invoiceId": "550e8400-e29b-41d4-a716-446655440000",
  "invoiceNumber": "TIGER-FACTURE-1001",
  "paidTransactionId": "7c661926-34e0-43bb-b5e6-590e88a03b9a",
  "paidProvider": "odengi",
  "providerPaymentId": "172030403548",
  "targetBankCode": "OBANK",
  "targetBankAccountCode": "OBANK_KGS",
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
invoiceId is the business idempotency key for Tiger export
```

PaymentGateway should create at most one successful Tiger export per `invoiceId`.
The paid transaction details are kept as evidence of which bank actually paid
the invoice.

PaymentGateway keeps incomplete Tiger events out of `pending`. Missing
`paidAt`, `targetBankAccountCode`, `clientCode`, non-positive amount, or
non-KGS currency places the event in `error` for admin review. After fixing the
source data, reset the event to `pending` from the admin endpoint.

Recommended result endpoint on PaymentGateway:

```http
POST /api/v1/local/tiger/invoice-events/{event_id}/result
X-Integration-Key: <tiger-worker-secret>
```

The result should contain `success` / `error`, and on success the Tiger
document identifiers such as `tigerLogicalRef` and `tigerFicheNo`.

## What The Tiger Windows Service Should Do

The Windows worker should:

1. Claim paid-invoice events from PaymentGateway.
2. Validate the server-to-server key.
3. Process only invoice events whose invoice is marked paid.
4. Connect to Logo via `UnityObjects`.
5. Use `targetBankCode` / `targetBankAccountCode` to choose the correct Tiger
   bank account.
6. Find the client and invoice/order by configured fields.
7. Create the agreed payment document.
8. Report `Success` or `Error` back to PaymentGateway.
9. Leave an event unacknowledged when the polling/transport cycle fails so the
   processing lease can retry it; report deterministic validation/write errors
   as `error` for admin review and reset.

Recommended incoming table:

```text
TigerInvoiceExports
- Id
- InvoiceId
- InvoiceNumber
- PaidTransactionId
- PaidProvider
- ProviderPaymentId
- TargetBankCode
- TargetBankAccountCode
- ClientCode
- Amount
- Currency
- Status: Pending / Processing / Success / Error / Skipped
- LogoDocumentNumber
- LogoLogicalRef
- ErrorMessage
- RetryCount
- CreatedAt
- ProcessedAt
```

## Remaining Clarifications Before Production Writes

The minimal XML payload and full write/read-back path are confirmed in `923/1`.
One voucher per paid invoice is implemented, and a stable hash of `invoiceId`
is stored in `NOTES1` for idempotency. Production still requires:

1. Map every PaymentGateway provider to its real production `BANKACC.CODE`.
2. Ensure every invoice event contains the corresponding `CLCARD.CODE` as
   `clientCode`.
3. Confirm with accounting that `NOTES1` is approved for the immutable marker.
4. Decide whether the customer-balance receipt is sufficient or whether
   `DebtClose` allocation to the specific invoice is required.
5. Create a dedicated Logo user with least-privilege production rights.
6. Run the deployed C# worker against `923/1` before allowing firm `126`.

Until those mappings and accounting rules are confirmed, production firm `126`
remains read-only and must not appear in `AllowedWriteFirmNos`.

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

### Phase 2 - Build Tiger Polling Worker

Build a Windows worker on the Tiger server:

- Claim PaymentGateway paid-invoice export events as `processing`.
- Process only events whose invoice is marked paid.
- Use `paidProvider` and `targetBankAccountCode` to choose the correct Tiger bank.
- Report success/error back to PaymentGateway.
- Keep `DryRun=true` until document mapping is approved.

### Phase 3 - Wire PaymentGateway

After the Windows worker is available, add optional PaymentGateway settings:

```env
TIGER_EXPORT_POLLING_ENABLED=true
TIGER_WORKER_INTEGRATION_KEY=<server-to-server-secret>
```

PaymentGateway should expose only invoice-level events for invoices that are
actually paid. It must not expose every bank transaction for the same invoice.

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
  "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
  "invoice_number": "TIGER-FACTURE-1001",
  "client_code": "CARI.001"
}
```

Use `invoice_id` as the business key. Use `invoice_number` only for display and
manual search. `client_code` must be Logo Tiger `CLCARD.CODE`, not the ordinary
1C customer code.

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
