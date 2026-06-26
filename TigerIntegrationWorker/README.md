# Tiger Integration Worker

Small standalone Windows-only C# API for testing Logo Tiger / LObjects
integration.

This worker must run on the Tiger Windows server where `LObjects.dll` is
registered. It does not create Tiger documents yet. The current endpoints are
safe smoke tests and read-only checks.

This is a separate project from the Python/FastAPI PaymentGateway backend. Keep
it in this folder and deploy it to the Windows Tiger server separately.

## What It Does Now

- `GET /health` - no Tiger access.
- `GET /tiger/version` - creates `UnityObjects.UnityApplication` and reads
  `Version()` / `GetAppPath()`.
- `POST /tiger/test-login` - runs `Connect`, `UserLogin`, `CompanyLogin`, then
  logs out.
- `GET /tiger/clients/sample` - reads 5 client rows from `LG_126_CLCARD`.
- `POST /api/payments` - accepts the future payment payload but only validates
  it while `DryRun=true`.

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
    "DryRun": true
  }
}
```

Do not commit `appsettings.json` with real credentials.

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

## Safety

- Keep `DryRun=true`.
- Do not add document creation until the exact Logo `DataObjectType` and fields
  are confirmed.
- Do not write directly to the Tiger SQL database.
- Do not expose this API publicly. Keep it on the Tiger server or behind an
  internal firewall/VPN.
