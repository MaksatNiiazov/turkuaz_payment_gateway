# Tiger / Logo Integration: Beginner .NET Steps

This is the practical checklist for testing Logo Tiger / Unity integration when
you do not know C# or .NET yet.

The first goal is not to build the full integration. The first goal is only to
prove that the Tiger server can be reached through `LObjects.dll`.

Current verified server facts:

- Tiger folder: `C:\LOGO\TIGER3ENT\`
- COM ProgID: `UnityObjects.UnityApplication`
- Logo Objects version: `Logo Objects 030700`
- Working login flow: `Connect()` -> `UserLogin(user, password)` ->
  `CompanyLogin(126)`
- Verified firm/period after login: firm `126`, period `1`

## What We Are Building

Final architecture:

```text
PaymentGateway
  -> sends "payment paid" event
  -> small Windows API/worker
  -> LObjects.dll / UnityObjects COM
  -> Logo Tiger
```

Do not connect PaymentGateway directly to `LObjects.dll`. `LObjects.dll` is a
Windows COM library, so it belongs on the Windows server where Tiger is
installed.

## Phase 1: Smoke Test Only

Before writing any real integration, we must run this tiny test:

```text
UnityApplication.Connect()
UnityApplication.UserLogin(username, password)
UnityApplication.CompanyLogin(firmNo)
```

If this fails, stop. The problem is in Logo licensing, DLL registration,
bitness, credentials, firm number, period number, or server setup.

## Production-Only Safety Rules

If there is no test Tiger database and only the production server is available,
work in this order:

1. Do not create documents from code yet.
2. Do not call `Post()` on any Logo `Data` object yet.
3. Do not write directly to the Tiger SQL database.
4. Do not run unknown sample code from the internet against production.
5. Use a separate low-permission Logo user for smoke tests.
6. First test only COM registration and login.
7. Then test read-only queries only.
8. Create accounting documents only after the accountant or Logo implementer
   confirms the exact document type and fields.
9. Add an application-level `DRY_RUN=true` mode before any write code exists.
10. Keep an idempotency table so repeated payment events cannot create duplicate
    Tiger documents.

The first production-safe milestone is:

```text
Connect succeeds, then Disconnect succeeds, and no Tiger document is created.
```

The second production-safe milestone is:

```text
Read one known client/invoice/document from Tiger without changing it.
```

Only after both milestones are safe should we discuss writing a real payment
document.

## What To Install On The Tiger Server

Install:

1. Visual Studio 2022 Community.
2. Workload: `.NET desktop development`.
3. .NET Framework 4.8 Developer Pack, if Visual Studio does not include it.

Use Visual Studio for the first test because it can add COM references through
the UI. This is easier than command-line .NET for a first Logo COM test.

## What To Check Before Coding

On the Tiger server, find out:

1. Where Tiger is installed.
2. Where `LObjects.dll` is located.
3. Whether `REGISTER.BAT` exists in the Tiger/Logo folder.
4. Whether `REGISTER.BAT` was run as Administrator.
5. Whether `LObjects.dll` is 32-bit or 64-bit.
6. Logo username/password for integration.
7. Logo firm number, Turkish: `Firma No`.
8. Logo period number, Turkish: `Dönem No`.

## Register LObjects.dll

Preferred way:

```text
Right click REGISTER.BAT -> Run as administrator
```

If there is no `REGISTER.BAT`, use one of these.

For 64-bit DLL:

```bat
cd C:\Windows\System32
regsvr32 "C:\LOGO\TIGER3ENT\LObjects.dll"
```

For 32-bit DLL on 64-bit Windows:

```bat
cd C:\Windows\SysWOW64
regsvr32 "C:\LOGO\TIGER3ENT\LObjects.dll"
```

The exact path may be different. Use the real Tiger install path.

## Create The First Visual Studio Project

1. Open Visual Studio.
2. Create a new project.
3. Choose `Console App (.NET Framework)`.
4. Name it `LogoTigerSmokeTest`.
5. Framework: `.NET Framework 4.8`.
6. Open project properties.
7. Set platform target:
   - `x86` if `LObjects.dll` is 32-bit.
   - `x64` if `LObjects.dll` is 64-bit.

If unsure, try `x86` first. Many old COM integrations are 32-bit.

## Add UnityObjects Reference

In Visual Studio:

```text
Project -> Add Reference -> COM -> UnityObjects Library
```

If `UnityObjects Library` is not visible:

1. `LObjects.dll` is probably not registered.
2. Or it was registered with the wrong bitness.
3. Or you need to browse directly to `LObjects.dll`.

Alternative:

```text
Project -> Add Reference -> Browse -> select LObjects.dll
```

## Program.cs For First Test

Replace `Program.cs` with this:

```csharp
using System;
using UnityObjects;

namespace LogoTigerSmokeTest
{
    internal class Program
    {
        private static void Main(string[] args)
        {
            var username = "LOGO_USER";
            var password = "LOGO_PASSWORD";
            var firmNo = 126;

            UnityApplication logoApp = null;

            try
            {
                logoApp = new UnityApplication();

                Console.WriteLine("Connecting to Logo Tiger...");
                var connected = logoApp.Connect();
                var userLoggedIn = logoApp.UserLogin(username, password);
                var companyLoggedIn = logoApp.CompanyLogin(firmNo);

                if (connected && userLoggedIn && companyLoggedIn)
                {
                    Console.WriteLine("SUCCESS: Connected to Logo Tiger.");
                    Console.WriteLine("LoggedIn: " + logoApp.LoggedIn);
                    Console.WriteLine("CompanyLoggedIn: " + logoApp.CompanyLoggedIn);
                    Console.WriteLine("CurrentFirm: " + logoApp.CurrentFirm);
                    Console.WriteLine("CurrentPeriod: " + logoApp.CurrentPeriod);
                }
                else
                {
                    Console.WriteLine("FAILED:");
                    Console.WriteLine("Connect: " + connected);
                    Console.WriteLine("UserLogin: " + userLoggedIn);
                    Console.WriteLine("CompanyLogin: " + companyLoggedIn);
                    Console.WriteLine(logoApp.GetLastErrorString());
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("EXCEPTION:");
                Console.WriteLine(ex);
            }
            finally
            {
                try
                {
                    logoApp?.CompanyLogout();
                    logoApp?.UserLogout();
                    logoApp?.Disconnect();
                }
                catch
                {
                    // Ignore disconnect errors in the smoke test.
                }
            }

            Console.WriteLine("Press Enter to exit.");
            Console.ReadLine();
        }
    }
}
```

Change only these values first:

```csharp
var username = "LOGO_USER";
var password = "LOGO_PASSWORD";
var firmNo = 126;
```

## Success Result

Good result:

```text
SUCCESS: Connected to Logo Tiger.
LoggedIn: True
CompanyLoggedIn: True
CurrentFirm: 126
CurrentPeriod: 1
```

If this happens, we can continue to document reads and payment document creation.

## Common Failures

### UnityObjects Library is missing

Likely causes:

- `LObjects.dll` is not registered.
- `REGISTER.BAT` was not run as Administrator.
- Wrong 32-bit / 64-bit registration.

### Login flow fails

Likely causes:

- Wrong Logo username/password.
- Wrong `firmNo`.
- Wrong active `periodNo`.
- User has no permission.
- Logo Objects license is missing or inactive.
- Logo/Tiger services are not running.

### PowerShell COM Works But Login Fails

This only proves the COM object exists. It does not prove the Logo user, license,
firm, or period are valid.

## Turkish Terms To Ask About

Useful terms in Turkish docs:

```text
Cari Hesap       = client/customer account
Tahsilat         = payment collection / received payment
Kasa Fişi        = cash voucher
Banka Fişi       = bank voucher
Satış Faturası   = sales invoice
İrsaliye         = delivery note / waybill
Firma No         = firm number
Dönem No         = period number
```

## Questions For Logo/Tiger Implementer

Send this to the Logo/Tiger implementer:

```text
Здравствуйте.

Для интеграции оплат из нашего PaymentGateway в Logo Tiger через LObjects нам
нужно уточнить:

1. Есть ли активная лицензия Logo Objects / LObjects runtime?
2. Где находится LObjects.dll на сервере?
3. Зарегистрирована ли библиотека через REGISTER.BAT или regsvr32?
4. Какая разрядность LObjects.dll: 32-bit или 64-bit?
5. Какой Firma No использовать?
6. Какой Dönem No использовать?
7. Какой пользователь Logo должен использоваться для интеграции?
8. Какие права нужны этому пользователю?
9. Каким типом документа в Tiger нужно отражать оплату QR по накладной?
10. Это должен быть Tahsilat, Kasa Fişi, Banka Fişi или другой документ?
11. Какие обязательные поля нужны для этого документа?
12. Какие коды кассы, банка, валюты и подразделения использовать?
13. Как связать оплату с накладной или счетом в Tiger?
14. Можно ли дать тестовую базу и пример вручную созданного правильного документа?
```

## After Smoke Test Succeeds

Only after `Connect()`, `UserLogin()`, and `CompanyLogin()` succeed:

1. Ask implementer which Tiger document type to create.
2. If there is no test database, do not create anything from code yet.
3. Ask the accountant or implementer to manually show one correct production
   document that represents a QR payment.
4. Reproduce only read-only lookup of that document through `LObjects`.
5. Build the Windows API/worker with `DRY_RUN=true` by default.
6. Add outbound delivery from PaymentGateway only after the Windows service can
   safely receive and store events without writing to Tiger.

## Minimum Safe Windows Service Design

The Windows service should have two separate steps:

```text
Polling worker
  -> call PaymentGateway for paid invoice events
  -> process one invoice-level event at a time
  -> report success/error back to PaymentGateway

Tiger write step
  -> if DRY_RUN=true, only validate and log what would be created
  -> if DRY_RUN=false, create Tiger document through LObjects
```

Required safety fields in the local integration table:

```text
InvoiceId
InvoiceNumber
PaidTransactionId
PaidProvider
ProviderPaymentId
TargetBankCode
TargetBankAccountCode
Amount
Status: Pending / DryRunValidated / Processing / Success / Error
DryRun
LogoDocumentNumber
LogoLogicalRef
ErrorMessage
RetryCount
CreatedAt
ProcessedAt
```

Never create a Tiger document if `InvoiceId` already has a successful Tiger
export.

Recommended first production run:

```text
DRY_RUN=true
```

In dry-run mode the worker should:

1. Connect to Logo.
2. Find the client/invoice/document.
3. Validate required mappings.
4. Log the exact Tiger document type and fields it would create.
5. Disconnect.
6. Not call `Post()`.

## PaymentGateway Invoice Event Shape

PaymentGateway should later expose this to the Windows worker. It is one event
per paid invoice, not one event per bank transaction.

```json
{
  "invoiceId": "550e8400-e29b-41d4-a716-446655440000",
  "invoiceNumber": "TIGER-FACTURE-1001",
  "paidTransactionId": "7c661926-34e0-43bb-b5e6-590e88a03b9a",
  "paidProvider": "odengi",
  "providerPaymentId": "172030403548",
  "targetBankCode": "OBANK",
  "targetBankAccountCode": "OBANK_KGS",
  "paidAt": "2026-06-23T10:30:00+06:00",
  "amountTyiyn": 1500000,
  "amount": 15000.0,
  "currency": "KGS",
  "paymentMethod": "qr",
  "description": "Оплата по накладной TIGER-FACTURE-1001"
}
```

The idempotency key is:

```text
invoiceId
```

`paidProvider` and `targetBankAccountCode` tell the worker which bank account in
Tiger should receive the payment.
