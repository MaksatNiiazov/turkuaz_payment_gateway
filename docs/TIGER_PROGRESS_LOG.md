# Tiger Integration Progress Log

Дата проверки: 2026-06-26

Это краткий журнал уже подтвержденных фактов по интеграции PaymentGateway с
Logo Tiger 3 Enterprise через LObjects.

## Что Нашли На Сервере

- На сервере открыт `Tiger Server`.
- На рабочем столе есть ярлык `TIGER 3 ENTERPRISE`.
- Основная папка установки:

```text
C:\LOGO\TIGER3ENT\
```

- Найден `LObjects.dll`:

```text
C:\LOGO\TIGER3ENT\LObjects.dll
```

- Найден `REGISTER.BAT`:

```text
C:\LOGO\TIGER3ENT\REGISTER.BAT
```

- Найдена папка:

```text
C:\LOGO\TIGER3ENT\LogoObjectService
```

В ней есть `LogoObjectService`, `LogoObjectService.exe.config`,
`LogoObjectServiceTestTool`, `LogoObjectDataTypes.cfg`, `log4net.dll`,
`SmartThreadPool.dll`.

## COM / LObjects Проверки

Проверили регистрацию COM:

```powershell
$progId = "UnityObjects.UnityApplication"
$type = [type]::GetTypeFromProgID($progId)
```

Результат:

```text
REGISTERED
Guid: 72db412a-6bf5-4920-a002-2aac679951df
```

Что это доказывает:

- `LObjects.dll` уже зарегистрирован.
- `REGISTER.BAT` запускать не нужно.
- COM ProgID `UnityObjects.UnityApplication` доступен из Windows.

## Создание UnityApplication

Проверили создание COM-объекта:

```powershell
$app = New-Object -ComObject UnityObjects.UnityApplication
```

Получили список методов. Важные найденные методы:

- `Connect`
- `Disconnect`
- `UserLogin`
- `UserLogout`
- `CompanyLogin`
- `CompanyLogout`
- `NewQuery`
- `NewDataObject`
- `GetLastError`
- `GetLastErrorString`
- `Version`
- `GetAppPath`
- `DebtClose`

Что это доказывает:

- LObjects не просто зарегистрирован, а реально создается и отвечает.
- У нас есть нужные методы для будущей интеграции.

## Версия Logo Objects

Проверили:

```powershell
$app.Version()
$app.GetAppPath()
```

Результат:

```text
Version: Logo Objects 030700
AppPath: C:\LOGO\TIGER3ENT\
Connected: False
LoggedIn: False
```

Что это доказывает:

- На сервере работает Logo Objects `030700`.
- Он смотрит на правильную папку Tiger.

## Успешный Login В Tiger

Рабочая схема оказалась такой:

```powershell
$app.Connect()
$app.UserLogin("<user>", "<password>")
$app.CompanyLogin(126)
```

Результат:

```text
Connect: True
UserLogin result: True
CompanyLogin result: True
LoggedIn: True
CompanyLoggedIn: True
CurrentFirm: 126
CurrentPeriod: 1
LastError: 0
```

Что это доказывает:

- LObjects может подключаться к Tiger.
- Пользователь может логиниться через LObjects.
- Фирма `126` и период `1` доступны.
- Лицензия/права достаточны минимум для входа и чтения.

## Успешный Read-only SQL Через LObjects

Проверили `NewQuery()` и `OpenDirect()`:

```sql
SELECT TOP 5 LOGICALREF, CODE, DEFINITION_
FROM LG_126_CLCARD
ORDER BY LOGICALREF
```

Результат:

```text
Query ok: True
Query error: 0
1  | я      |
2  | 120    | ALICI CARILER
34 | 320    | SATICI CARILER
42 | 128    | TAKIBE DUSEN CARILER
43 | 128.01 | TAKIBE DUSEN CARILER
```

Что это доказывает:

- Мы можем читать таблицы Tiger через официальный LObjects-слой.
- `NewQuery`, `OpenDirect`, `First`, `Next`, `FieldByName` работают.
- Прямой SQL-write в базу не нужен и не должен использоваться.

## Что Уже Сделано В Репозитории

Добавлен минимальный Windows-only C# worker:

```text
TigerIntegrationWorker/
```

Текущие endpoints:

- `GET /health`
- `GET /tiger/version`
- `POST /tiger/test-login`
- `GET /tiger/clients/sample`
- `POST /api/invoices/paid` только в `DryRun=true`

Worker пока ничего не создает в Tiger. Он нужен для безопасного smoke test и
будущего HTTP-триггера из PaymentGateway.

## Текущий Вывод

Интеграция технически возможна.

У нас уже подтвержден путь:

```text
PaymentGateway
  -> HTTP request
  -> Windows Tiger worker
  -> UnityObjects / LObjects.dll
  -> Logo Tiger 3 Enterprise
```

Следующий риск находится не в подключении, а в бухгалтерской части:

- какой `DataObjectType` создавать;
- какие поля обязательны;
- как правильно связать оплату с исходным счетом/накладной;
- куда сохранять внешний `payment_id`, чтобы не создавать дубликаты.

До подтверждения этих пунктов нельзя вызывать `Post()` для создания документов.
