# Tiger / Logo Polaris: practical Russian memo

## What These Docs Are About

The Polaris docs at <https://polaris.logo.cloud/docs/tiger-uyarlama-araci>
cover Tiger customization and integration tooling:

- `Logo Objects`, `LObjects`, `UnityObjects` - the Windows COM/API layer used to
  read and write Tiger objects from code.
- `Logo Tiger3 REST Servisi` - a self-hosted Windows Web API service that works
  on top of Logo Objects.
- `Logo REST Servis Ayarlari` - REST service configuration: Tiger user,
  certificates, firewall/proxy, logging and runtime parameters.
- `Logo Object Designer / LOD` - tooling for inspecting/building object
  definitions around Logo Objects.

The docs are mostly Turkish and many pages are rendered through the Polaris web
app. Search-indexed snippets still confirm the important direction: Tiger REST
Service is a Windows self-hosted Web API, and Logo Objects/UnityObjects remain
the lower-level runtime behind it.

## Recommended Architecture

PaymentGateway should not directly load `LObjects.dll`.

Recommended shape:

```text
PaymentGateway
  -> stores paid invoice export events
  -> Tiger Integration worker polls over HTTPS
  -> Logo Tiger3 REST Service OR LObjects/UnityObjects
  -> Logo Tiger
```

Why:

- `LObjects.dll` is Windows/COM-oriented and depends on installed Tiger runtime.
- It needs correct bitness, registration, Logo user, firm number and period.
- PaymentGateway should stay a small HTTP payment adapter and not become a
  Windows COM host.
- A separate Tiger-side worker can poll, retry, log and protect writes without
  exposing the Tiger server to inbound internet traffic.

## Option A: Use Logo Tiger3 REST Service

Prefer this if it is installed and enabled.

What to check:

1. Is `Logo Tiger3 REST Servisi` installed on the Tiger server?
2. What base URL/port does it use?
3. Is HTTPS/certificate required?
4. Which Tiger user is configured in REST service settings?
5. Does that user have rights to read customers/documents and create the target
   payment/accounting document?
6. Which REST endpoint/object represents the document we need to create?
7. How does REST auth work in this installation?
8. Is there a test company/period?

If this path works, our Windows integration service can call REST instead of COM.
This is cleaner operationally.

## Option B: Use LObjects / UnityObjects

Use this if REST Service is unavailable or too limited.

Tiger developer confirmed the same direction:

- `LObjects.dll` is an API layer for safe data exchange between external apps
  and Logo ERP systems such as Tiger/Unity.
- It is used for transferring cards and vouchers/documents (`kart` / `fis`).
- It requires an active LObjects license.
- It requires local COM registration on the Windows machine where integration
  code runs.
- In C# the library is usually referenced as `UnityObjects`.
- Connection is done through `UnityObjects.UnityApplication.Connect(...)`.

Minimum smoke test on the Tiger Windows server:

```text
UnityApplication.Connect(username, password, firmNo, periodNo)
```

Do this before writing any real document.

Checklist:

1. Find Tiger install folder.
2. Find `LObjects.dll`.
3. Register it through `REGISTER.BAT` or `regsvr32` as administrator.
4. Confirm 32-bit vs 64-bit and match .NET project platform.
5. Create a .NET Framework 4.8 console test.
6. Add COM reference: `UnityObjects Library`.
7. Connect with real Tiger user, firm and period.
8. Run a read-only query for one known customer/document.
9. Only then create a dry-run worker.

Example connection skeleton from Tiger developer:

```csharp
using UnityObjects;

UnityApplication logoApp = new UnityApplication();
int result = logoApp.Connect(
    "KullaniciAdi",
    "Sifre",
    FirmaNo,
    DonemNo
);

if (result == 0)
{
    // Connected successfully. Read/write Tiger objects here.
}
else
{
    string error = logoApp.GetLastErrorString();
}
```

Important: this still does not tell us which Tiger object/document must be
created for a paid QR invoice. It only confirms the transport/runtime path.

## Option C: Ready-Made Integration Tool

The video `Logo Kolay Entegrasyon Araci (Sql,Lobject,Rest)` suggests another
possible path: a ready-made Logo integration tool that can work through SQL,
LObjects and REST.

Treat this as a commercial/third-party shortcut, not as a confirmed project
dependency yet.

What to ask before relying on it:

1. Which Logo/Tiger versions does it support?
2. Does it work with our exact Tiger installation?
3. Does it support writing the document type required by our accountant?
4. Does it provide an HTTP API we can call from PaymentGateway?
5. Can it guarantee idempotency by external payment ID?
6. Can it run in dry-run/test mode?
7. Does it log failed writes and allow retries?
8. Who supports it in production?

If it has an HTTP API and supports the required payment document, it may replace
our custom Windows service. If it is only a desktop/manual tool, it is useful for
experiments but not enough for automatic payment posting.

## What PaymentGateway Should Send To Tiger Side

PaymentGateway already stores the key data:

- `external_invoice_id` - stable 1C/Tiger document ID from `metadata.invoice_id`.
- `invoice_number` - human-readable number from `metadata.invoice_number`.
- `provider` - the bank/provider that actually paid the invoice.
- transaction ID / provider payment ID - evidence of the winning paid provider.

Recommended paid-invoice event:

```json
{
  "invoiceId": "550e8400-e29b-41d4-a716-446655440000",
  "invoiceNumber": "TIGER-FACTURE-1001",
  "paidTransactionId": "TIGER-ORDER-123",
  "paidProvider": "odengi",
  "providerPaymentId": "172030403548",
  "targetBankCode": "OBANK",
  "targetBankAccountCode": "OBANK_KGS",
  "paidAt": "2026-06-24T10:30:00+06:00",
  "amountTyiyn": 1500000,
  "amount": 15000.0,
  "currency": "KGS",
  "clientCode": "CARI.001",
  "paymentMethod": "qr",
  "description": "QR payment for TIGER-FACTURE-1001"
}
```

Idempotency:

```text
invoiceId
```

The Tiger-side service must never create two Tiger documents for the same
`invoiceId`. `paidProvider` / `targetBankAccountCode` decide which Tiger bank
account receives the payment.

## Main Thing We Must Learn Before Continuing

These are blockers. Without them, we can build transport code, but not safely
create real Tiger documents.

1. Is Logo Tiger3 REST Service available and allowed for this integration?
   - If yes, get base URL, auth method, test credentials and endpoint docs.
   - If no, use LObjects/UnityObjects.

2. Is there already a ready-made Logo integration tool available?
   - The useful video shows a tool approach over SQL, LObjects and REST.
   - If the company already bought/uses one, get its API docs and support
     contact before building our own Windows service.

3. What exact Tiger document should be created when QR payment is paid?
   - Cash receipt?
   - Bank receipt?
   - Current account collection?
   - Payment against sales invoice/waybill/order?
   - Something else used by their accountant?

4. How do we find the target document in Tiger?
   - By internal logical reference?
   - By document number?
   - By 1C external ID saved in a custom field?
   - By customer + number + date?

5. What stable ID should 1C send as `invoice_id`?
   - It must not be just a visible number if numbers can repeat/change.
   - Ideally it is the Tiger/1C internal reference or an immutable external UUID.

6. Which fields are mandatory for the target Tiger document?
   - Customer/current account code.
   - Bank/cash account code.
   - Branch/department/workplace.
   - Currency.
   - Date fields.
   - Special codes/auth codes.
   - Project/trading group fields, if used.

7. How should payment providers map to Tiger accounts?
   - MBank -> which cash/bank/current account?
   - O!Bank/O!Dengi -> which cash/bank/current account?
   - Commission handling, if any.

8. Is there a test company and test period?
   - If no, first phase must be read-only + dry-run only.

9. Who owns error correction?
   - If Tiger write fails after bank payment succeeds, should it retry
     automatically, wait for manual fix, or notify someone?

## Questions To Send To Logo/Tiger Specialist

```text
Нам нужно отражать оплаты QR из PaymentGateway в Logo Tiger.

Подскажите, пожалуйста:

1. У нас установлен и доступен Logo Tiger3 REST Service?
2. Если да: какой base URL, способ авторизации и где описание endpoint'ов?
3. Используется ли у нас готовый Logo integration tool, который работает через
   SQL/LObjects/REST? Если да, есть ли у него HTTP API и документация?
4. Если REST/tool нет: есть ли активная лицензия Logo Objects / LObjects?
5. Где установлен LObjects.dll и какой он разрядности: x86 или x64?
6. Какая фирма и период должны использоваться для интеграции?
7. Какой пользователь Tiger должен использоваться и какие права ему нужны?
8. Каким документом в Tiger должна отражаться успешная QR-оплата?
9. По какому полю искать исходный счет/накладную/документ?
10. Какие обязательные поля нужны для создания этого документа?
11. В какие счета/кассы/банки мапятся MBank и O!Bank/O!Dengi?
12. Есть ли тестовая база/фирма/период для первых записей?
13. Как должны обрабатываться комиссии банка, если они есть?
14. Можете дать минимальный C# пример именно для нужного нам документа:
    поиск исходного счета + создание оплаты + привязка к счету?
15. Какой `DataObjectType` / объект UnityObjects используется для этого
    документа?
16. Какие поля в этом объекте обязательны в нашей конфигурации Tiger?
```

## What We Can Build Now

Safe work before answers:

- Keep PaymentGateway creating one grouped payment by `invoice_id`.
- Keep auto-cancel of other provider QR after one provider is paid.
- Add optional outgoing queue table for Tiger delivery events.
- Add dry-run endpoint that shows what would be sent to Tiger.
- Build a Windows-side API skeleton with an incoming table and no Tiger writes.

Unsafe work before answers:

- Creating Tiger accounting documents.
- Writing directly to Tiger SQL.
- Guessing document type or account codes.
- Treating visible invoice number as the only business key.
