# Tiger Integration Worker

Small standalone Windows-only C# worker for Logo Tiger / LObjects integration.

This worker must run on the Tiger Windows server where `LObjects.dll` is
registered. It can poll paid invoice events from PaymentGateway and write them
to grouped incoming bank vouchers through `doBankVoucher=24`.

This is a separate project from the Python/FastAPI PaymentGateway backend. Keep
it in this folder and deploy it to the Windows Tiger server separately.

## What It Does Now

- `GET /health` - no Tiger access.
- `GET /tiger/version` - creates `UnityObjects.UnityApplication` and reads
  `Version()` / `GetAppPath()`.
- `POST /tiger/test-login` - runs `Connect`, `UserLogin`, `CompanyLogin`, then
  logs out.
- `GET /tiger/clients/sample` - reads 5 client rows from `LG_126_CLCARD`.
- `POST /api/invoices/paid` - validates or posts one paid-invoice event.
- background poller - pulls pending events from PaymentGateway and reports the
  resulting Tiger logical reference and fiche number.

The confirmed write paths are:

```text
new bank/date group:
ImportFromXmlStr -> Post -> SQL verify line marker/count/sum

existing bank/date group:
Read(LOGICALREF) -> ExportToXML -> modify exported XML -> ImportFromXmlStr
-> Post -> SQL verify line marker/count/sum
```

The worker serializes all COM operations. A stable hash of `invoiceId` is stored
on each `BNFLINE.LINEEXP` through `TRANSACTION.DESCRIPTION`; it is checked
before each write and again after export to avoid duplicate lines after retries.
`BNFICHE.GENEXP1` stores the group marker for `bank account + document date`.

## Install .NET SDK

On the Tiger server, install .NET 8 SDK for Windows.

Check:

```powershell
dotnet --info
```

## Configure

Copy the example config:

```powershell
copy appsettings.example.json appsettings.json
```

Edit only `appsettings.json`:

```json
{
  "Urls": "http://127.0.0.1:5088",
  "Tiger": {
    "UserName": "LOGO_USER",
    "Password": "LOGO_PASSWORD",
    "FirmNo": 126,
    "PeriodNo": 1,
    "IntegrationKey": "use-a-long-random-secret",
    "DryRun": true,
    "AllowedWriteFirmNos": [],
    "TestDocumentDateOverride": null
  },
  "Gateway": {
    "Enabled": false,
    "BaseUrl": "https://payments.example.com",
    "IntegrationKey": "use-a-long-random-secret",
    "PollIntervalMinutes": 30,
    "BatchSize": 20
  }
}
```

Do not commit `appsettings.json` with real credentials.

Start with both protections enabled:

```json
"DryRun": true,
"AllowedWriteFirmNos": [],
"Gateway": { "Enabled": false }
```

For controlled writes to the test firm only, use `FirmNo=923`, `PeriodNo=1`,
set `AllowedWriteFirmNos` to `[923]`, and only then set `DryRun=false`.
`TestDocumentDateOverride` may be used only for firm `923`, whose test period
ends in 2025. It is rejected for every other firm.

## Run

From this folder on the Tiger server:

```powershell
dotnet run
```

## Test Locally On The Tiger Server

Health:

```powershell
curl http://127.0.0.1:5088/health
```

Version:

```powershell
curl -H "X-Integration-Key: use-a-long-random-secret" http://127.0.0.1:5088/tiger/version
```

Login:

```powershell
curl -X POST -H "X-Integration-Key: use-a-long-random-secret" http://127.0.0.1:5088/tiger/test-login
```

Read sample clients:

```powershell
curl -H "X-Integration-Key: use-a-long-random-secret" http://127.0.0.1:5088/tiger/clients/sample
```

## First C# Dry-Run In 923/1

Use this test configuration before enabling polling:

```json
"FirmNo": 923,
"PeriodNo": 1,
"DryRun": true,
"AllowedWriteFirmNos": [],
"TestDocumentDateOverride": "2024-05-31"
```

Keep `Gateway:Enabled=false`, start the worker, then run in another PowerShell:

```powershell
$headers = @{ "X-Integration-Key" = "use-a-long-random-secret" }
$body = @{
    invoiceId = "CSharp-DryRun-001"
    invoiceNumber = "TEST-001"
    paidTransactionId = "TEST-PAYMENT-001"
    paidProvider = "test"
    providerPaymentId = "TEST-PROVIDER-001"
    targetBankCode = "TEST"
    targetBankAccountCode = "10200 100.01.001"
    paidAt = "2026-07-01T07:00:00+06:00"
    amountTyiyn = 100
    amount = 1
    currency = "KGS"
    clientCode = "120.04.2.01.1451"
    clientName = "Test"
    paymentMethod = "qr"
    description = "C# dry-run"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:5088/api/invoices/paid" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

Expected: `success=true`, `dryRun=true`, `savedLineCount=1`, and no Tiger
logical reference. A dry-run never calls `Post()`.

## Inspect DataObjectType Without Writing

`Inspect-LObjectsDataObjectType.ps1` reads the `DataObjectType` enum directly
from the COM type library in `LObjects.dll`. It does not register the DLL,
connect to Tiger, access SQL, or call `NewDataObject`/`Post`.

```powershell
cd C:\path\to\TigerIntegrationWorker
powershell -ExecutionPolicy Bypass -File .\Inspect-LObjectsDataObjectType.ps1
```

The default DLL path is `C:\LOGO\TIGER3ENT\LObjects.dll`. Pass
`-LObjectsPath` only when Tiger is installed elsewhere.

Confirmed from the installed type library and official Polaris documentation:

```text
DataObjectType: doBankVoucher (24)
XML root: BANK_VOUCHERS
REST resource: bankVouchers
Line collection: TRANSACTIONS
Line creation while building a new object: AppendLine()
```

For an existing voucher, call `Read(LOGICALREF)` before
`ExportToXMLStr("BANK_VOUCHERS", ...)`. Do not call `Post()` during schema
inspection.

Controlled tests in `923/1` confirmed that one newly created `BANK_VOUCHER`
can contain multiple `TRANSACTIONS` lines. Direct append to an already posted
bank voucher is unsafe: `AppendLine()` added a row in memory and `Post()`
returned true, but SQL read-back still showed the original line count; a
hand-built minimal XML `DBOP="UPD"` also failed with `DBError=23000`.

Appending to an existing voucher is confirmed only through Tiger's own exported
XML shape: `Read(LOGICALREF)`, `ExportToXML("BANK_VOUCHERS", file)`, change the
exported XML to `DBOP="UPD"`, add the new `TRANSACTION`, update `TOTAL_DEBIT`,
then `ImportFromXmlStr("BANK_VOUCHERS", xml)` and `Post()`. In test firm
`923/1`, this increased voucher `1006` from one line to two lines. The worker
uses this path for daily `bank account + date` groups and verifies line
marker/count/sum after every post.

The minimal incoming voucher payload has been confirmed in test firm `923/1`:

```text
header: TYPE=3, TOTAL_DEBIT, NOTES1, CURRSEL_TOTALS=1
line: TYPE=1, BANKACC_CODE, ARP_CODE, TRCODE=3, MODULENR=7
line: CURR_TRANS=37, DEBIT, AMOUNT, TC_XRATE=1, TC_AMOUNT
line: BANK_PROC_TYPE=2, AFFECT_RISK=1, BN_CRDTYPE=3, COSTTYPE=1
```

The event must contain the real Tiger `CLCARD.CODE` in `clientCode` and the
real Tiger `BANKACC.CODE` in `targetBankAccountCode`. Only KGS is enabled.

Logo Objects is licensed per server and requires a runtime license. Keep one
serialized COM session: error `-13` means the runtime license is unavailable
and `-93` means the terminal limit was exceeded. After `Post()`, call
`Read(LOGICALREF)` again before checking `TRANSACTIONS`, because nested line
objects are freed during the post.

## Safety

- Keep `DryRun=true` until the manual endpoint succeeds on the deployed worker.
- Keep `AllowedWriteFirmNos=[]` until a specific test or production firm is
  explicitly approved.
- Do not write directly to the Tiger SQL database.
- Do not run concurrent Logo Objects sessions from this worker.
- Do not expose this API publicly. Keep it on the Tiger server or behind an
  internal firewall/VPN.
- Dry-run polling never acknowledges queue events as successful.
