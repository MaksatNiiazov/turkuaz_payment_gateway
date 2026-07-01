# Tiger Integration Progress Log

Дата последней проверки: 2026-06-29

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
$app.CompanyLogin(126, 1)
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

## Проверка Базы Через SSMS

На отдельном сервере базы данных подтвержден доступ через Microsoft SQL Server
Management Studio. Все выполненные нами команды были только `SELECT`.

В выбранной базе найдены таблицы фирмы `126` и периода `01`, в том числе:

```text
LG_126_01_INVOICE
LG_126_01_BNFICHE
LG_126_01_BNFLINE
LG_126_01_CLFICHE
LG_126_01_CLFLINE
LG_126_01_KSLINES
LG_126_01_PAYTRANS
LG_126_BANKACC
LG_126_BNCARD
LG_126_CLCARD
```

Также в базе присутствуют таблицы других фирм: `004`, `022`, `023`, `024`,
`122`-`126`, `222`-`226`, `322`-`326`, `422` и другие. Поэтому во всех
запросах интеграции номер фирмы и периода должен задаваться явно; нельзя
выбирать первую таблицу с названием `INVOICE`.

Наличие `LG_126_01_INVOICE` доказывает, что предыдущая ошибка LObjects
`LastError=-10` не означала отсутствие таблицы.

Полный каталог безопасных диагностических запросов сохранен в
[TIGER_READONLY_DISCOVERY.md](TIGER_READONLY_DISCOVERY.md).

## Ограничения, Которые Уже Обнаружены

### NewQuery

Успешно работает простой запрос к `LG_126_CLCARD`. При этом запросы к
`LG_126_01_INVOICE` и `SYS.TABLES` завершились так:

```text
DISP_E_NOTACOLLECTION (0x80020011)
LastError: -10
LastErrorString: Не удалось создать SQL запрос.
```

Следовательно, `NewQuery/OpenDirect` нельзя считать полноценной заменой SSMS:
парсер или права LObjects ограничивают часть SQL. Исследование схемы выполняем
через read-only `SELECT` в SSMS, а будущую запись документа планируем только
через `NewDataObject`/`Post` после утверждения бухгалтерского маппинга.

### Итерация IQuery

У `IQuery` нет надежного свойства `EOF`. Рабочий шаблон:

```powershell
$hasRow = $q.First()
while ($hasRow) {
  $q.FieldByName("LOGICALREF")
  $hasRow = $q.Next()
}
```

Использование неверного цикла после конца набора приводило к
`E_UNEXPECTED (0x8000FFFF)`. После такой ошибки нужно закрыть query и создать
новые COM-объекты.

### GetTableName

Первый аргумент `GetTableName` не является `DataObjectType`. Проверка дала:

```text
GetTableName(3, 126, 1)  -> LG_126_01_SRVTOT
GetTableName(30, 126, 1) -> LG_126_01_EMUHTOT
GetTableName(5, 126, 1)  -> LG_126_01_STINVENS
```

По этим числам нельзя определять тип создаваемого документа.

### COM Методы Для Следующего Этапа

Подтвержденные сигнатуры:

```text
void GetDBConnInfo (string, string, int, string, string, string, int, string)
IData NewDataObject (DataObjectType)
```

`GetDBConnInfo` пока не вызываем: PowerShell скрывает имена выходных параметров,
среди которых могут быть учетные данные SQL. `DataObjectType` платежного
документа еще не подтвержден.

## Реальные Строки INVOICE

Read-only выборка последних 100 строк из `LG_126_01_INVOICE` с join на
`LG_126_CLCARD` успешно выполнена 2026-06-29.

Подтверждено:

- `FICHENO` заполнен последовательными номерами вида `00058918`, `00058917`;
- `DOCODE` может быть пустым, повторяться и содержать значения вида
  `DOSTOR-21`, поэтому это поле нельзя использовать как уникальный ключ;
- `CLIENTREF` корректно связывается с `LG_126_CLCARD.LOGICALREF`;
- join возвращает `CLIENT_CODE` и `CLIENT_NAME`;
- среди выборки встречаются `TRCODE=8` и `TRCODE=1`; бизнес-смысл кодов еще
  должен быть подтвержден бухгалтером или документацией Tiger;
- `NETTOTAL` содержит итоговую сумму документа;
- во всех полученных 100 строках `CANCELLED=0`;
- `LOGICALREF` выглядит как внутренний уникальный идентификатор строки;
- один клиент может иметь много счетов за один день, поэтому `CLIENTREF`, дата
  и сумма по отдельности не обеспечивают идемпотентность.

Примеры:

```text
LOGICALREF=170189 FICHENO=00058918 DOCODE=-DOSTOR-4
DATE_=2026-06-29 TRCODE=8 CLIENTREF=400
CLIENT_CODE=120-1-1-1-3-0004 CLIENT_NAME=DOSTOR
NETTOTAL=11986.96 CANCELLED=0

LOGICALREF=170142 FICHENO=00000617 DOCODE=2026-567 haziran
DATE_=2026-06-26 TRCODE=1 CLIENTREF=1389
CLIENT_CODE=320-01-0-0-0-196
NETTOTAL=5604154.812 CANCELLED=0
```

Предварительный вывод для контракта: если 1С знает Tiger `LOGICALREF`, это
лучший технический ключ. Если не знает, 1С должна передавать как минимум
`FICHENO`, фирму `126`, период `1` и ожидаемую сумму. Окончательное решение
принимаем после проверки уникальности и источника номера.

## Структура Банковских Таблиц

Через `sys.tables/sys.columns` прочитана структура банковских таблиц фирмы
`126`. По названиям полей сформирована следующая проверяемая схема связей:

```text
BNFICHE.LOGICALREF       <- BNFLINE.SOURCEFREF
BNFLINE.CLIENTREF        -> CLCARD.LOGICALREF
BNFLINE.BANKREF          -> BNCARD.LOGICALREF
BNFLINE.BNACCREF         -> BANKACC.LOGICALREF
```

Шапка банковского документа `LG_126_01_BNFICHE` содержит:

```text
LOGICALREF, DATE_, FICHENO, TRCODE, CANCELLED,
DEBITTOT, CREDITTOT, GENEXP1..GENEXP6, BNACCOUNTREF, GUID
```

Строка `LG_126_01_BNFLINE` содержит:

```text
LOGICALREF, SOURCEFREF, DATE_, TRCODE, TRANSTYPE, SIGN,
CLIENTREF, BANKREF, BNACCREF, AMOUNT, TRCURR, TRRATE, TRNET,
TRANNO, DOCODE, LINEEXP, PAYMENTREF, BANKREFNR, CUSTOMDOCNR,
CLFLINEREF, CLFICHEREF, GUID, CANCELLED
```

Карточка банковского счета `LG_126_BANKACC` содержит `CODE`, `DEFINITION_`,
`BANKREF`, `CURRENCY`, `ACTIVE`, `ACCOUNTNO`, `IBAN`, `GUID`. Карточка банка
`LG_126_BNCARD` содержит `CODE`, `DEFINITION_`, `ACTIVE`, `BRANCH`, `BRANCHNO`,
`VOEN`, `GUID`.

Структура позволяет привязать банковский документ к конкретному банку, счету и
клиенту. Сами join-связи, а также значения `TRCODE`, `TRANSTYPE` и `SIGN` для
входящей оплаты еще нужно подтвердить по реальным проведенным строкам.

## Реальные Банковские Операции

Read-only выборка последних 100 строк `LG_126_01_BNFLINE` с join на документ,
клиента, банк и банковский счет успешно выполнена.

Распределение комбинаций в выборке:

```text
63 x FICHE_TRCODE=3 LINE_TRCODE=3 TRANSTYPE=1 SIGN=0
29 x FICHE_TRCODE=4 LINE_TRCODE=4 TRANSTYPE=1 SIGN=1
 4 x FICHE_TRCODE=2 LINE_TRCODE=2 TRANSTYPE=1 SIGN=0
 4 x FICHE_TRCODE=2 LINE_TRCODE=2 TRANSTYPE=1 SIGN=1
```

По тексту `LINEEXP` подтверждается локальная семантика текущей базы:

- `TRCODE=3`, `SIGN=0` — поступление денег;
- `TRCODE=4`, `SIGN=1` — исходящий платеж/списание;
- `TRCODE=2` с парой `SIGN=0/1` — перевод/конвертация между счетами.

Для всех показанных строк `TRANSTYPE=1`. Все 100 строк не отменены.

Среди 63 входящих строк использованы счета:

```text
23 x 10203 / 10203 103.01.001 / TURKUAZ BAY TUSHUM SOM
22 x 10202 / 10202 102.01.001 / TURKUAZ HALYK BANK SOM
14 x 10200 / 10200 100.01.001 / TURKUAZ DEMIRBANK SOM
 2 x 20200 / 20200 201.01.001 / MARSAN O! BANK SOM
 2 x 10202 / 10202 102.03.001 / TURKUAZ HALYK BANK RUBL
```

Пример реального входящего платежа:

```text
BANK_LINE_REF=9205 BANK_FICHE_REF=717 BANK_FICHENO=00000229
DATE_=2026-06-18 TRCODE=3 TRANSTYPE=1 SIGN=0 AMOUNT=5252.40
BANK_CODE=20200 BANK_NAME=О! БАНК МАРСАН
BANK_ACCOUNT_CODE=20200 201.01.001
BANK_ACCOUNT_NAME=MARSAN O! BANK SOM
LINEEXP=ПОСТУПЛЕНИЕ ДЕНЕГ ОТ УНИВЕРМАГ
```

Во всех 100 строках `PAYMENTREF=0`, `CLFLINEREF=0`, `CLFICHEREF=0`. Поэтому
банковская строка сама по себе в этой выборке не содержит прямой ссылки на
оплаченный счет. Связь оплаты с задолженностью нужно искать в `PAYTRANS`,
клиентских проводках или механизме закрытия долга.

Название банковской карточки и название счета могут не совпадать буквально:
например, `BANK_NAME=О! БАНК ТТ`, а имя счета содержит `HALYK BANK`. Маппинг
Mbank/O!Bank нельзя строить по подстроке имени; нужно утвердить конкретные
`BANK_ACCOUNT_CODE`.

## Структура PAYTRANS

Через read-only запрос к `sys.columns` прочитана структура
`LG_126_01_PAYTRANS`. Для интеграции особенно важны поля:

```text
LOGICALREF, CARDREF, DATE_, MODULENR, SIGN, FICHEREF, FICHELINEREF,
TRCODE, TOTAL, PAID, CROSSREF, PAIDINCASH, CANCELLED, PROCDATE,
BANKACCREF, TRNET, NETTOTAL, BNFCHREF, BNFLNREF, DOCODE, LINEEXP,
MATCHDATE
```

Наличие `BNFCHREF` и `BNFLNREF` важно: если Tiger при закрытии долга связывает
строку оплаты с банковским документом, эти поля должны указать на
`BNFICHE.LOGICALREF` и `BNFLINE.LOGICALREF`. Наличие `FICHEREF`,
`FICHELINEREF`, `TOTAL`, `PAID`, `CROSSREF` позволяет проверить, как именно
закрывается задолженность по конкретному счету.

Первая проверка по счету `INVOICE.LOGICALREF=170189` подтвердила:

```text
INVOICE FICHENO=00058918 DOCODE=-DOSTOR-4 TOTAL=11986.96
PAYTRANS_REF=455809 MODULENR=4 SIGN=0 FICHEREF=170189 TRCODE=8
TOTAL=11986.96 PAID=0 CROSSREF=0 BANKACCREF=0 BNFCHREF=0 BNFLNREF=0
```

Вывод: при создании счета Tiger создает строку задолженности в `PAYTRANS`, но
для неоплаченного счета в ней нет банковских ссылок. Следующая проверка должна
найти строки `PAYTRANS` с `PAID > 0` или `CROSSREF > 0`, чтобы увидеть
закрытие долга на реальном оплаченном документе.

Проверка оплаченных строк с фильтром `P.MODULENR = 4` и условием
`PAID > 0 OR CROSSREF > 0 OR BNFCHREF > 0 OR BNFLNREF > 0` вернула пустой
результат. Значит, закрытие может храниться в другом `MODULENR`, в другой
таблице или без заполнения этих полей на строке исходного счета.

Агрегация всей `LG_126_01_PAYTRANS` по `MODULENR`, `SIGN`, `TRCODE`
показала, что во всех проверенных группах счетчики `PAID_ROWS`,
`CROSSREF_ROWS`, `BANKACC_ROWS`, `BNFCH_ROWS`, `BNFLN_ROWS` равны нулю.
Крупнейшие группы:

```text
MODULENR=4 SIGN=0 TRCODE=8  ROW_COUNT=158081
MODULENR=4 SIGN=1 TRCODE=3  ROW_COUNT=10765
MODULENR=5 SIGN=1 TRCODE=5  ROW_COUNT=48649
MODULENR=10 SIGN=1 TRCODE=1 ROW_COUNT=56307
MODULENR=7 SIGN=1 TRCODE=3  ROW_COUNT=4436
MODULENR=7 SIGN=0 TRCODE=4  ROW_COUNT=3693
```

Вывод: в этой базе `PAYTRANS` хранит строки задолженности/платежного графика,
но не хранит фактические связи закрытия с банком. Для фактической оплаты нужно
исследовать клиентские проводки `CLFICHE`/`CLFLINE` и их связь с
`BNFICHE`/`BNFLINE`.

## Структура CLFICHE И CLFLINE

Метаданные обеих таблиц прочитаны через `sys.columns` строго read-only.
Ключевые поля `LG_126_01_CLFICHE`:

```text
LOGICALREF, FICHENO, DATE_, DOCODE, TRCODE, DEBIT, CREDIT,
INVOREF, CANCELLED, CLCARDREF, BANKACCREF, BNACCREF, GUID
```

Ключевые поля `LG_126_01_CLFLINE`:

```text
LOGICALREF, CLIENTREF, SOURCEFREF, DATE_, MODULENR, TRCODE, DOCODE,
LINEEXP, SIGN, AMOUNT, EXTENREF, PAYMENTREF, CANCELLED,
BANKACCREF, BNACCREF, GUID
```

`CLFLINE` теперь является главным кандидатом для поиска фактической связи:
`MODULENR` указывает модуль исходного документа, `SOURCEFREF` — ссылку на
исходную операцию, а `PAYMENTREF` может связывать проводку со строкой
платежного графика. `BANKACCREF`/`BNACCREF` позволяют проверить банк. Эти
связи пока являются гипотезами и должны быть подтверждены выборкой реальных
строк, а не только названиями колонок.

Агрегация `CLFLINE` по `MODULENR/SIGN/TRCODE` показала:

```text
MODULENR=7 SIGN=1 TRCODE=20 ROW_COUNT=4436 BANKACC_ROWS=4436
MODULENR=7 SIGN=0 TRCODE=21 ROW_COUNT=3693 BANKACC_ROWS=3693
```

Во всех строках обеих банковских групп заполнены `SOURCEFREF` и
`BANKACCREF`; `PAYMENTREF`, `BNACCREF` и `EXTENREF` равны нулю. Это надежно
выделяет банковские клиентские проводки, но пока не дает прямой ссылки на
конкретный счет. Следующая проверка должна подтвердить, что
`CLFLINE.SOURCEFREF = BNFLINE.LOGICALREF`, и сопоставить направление
операции с `BNFLINE`/`BNFICHE`.

Проверка последних 50 банковских клиентских проводок подтвердила join:

```text
CLFLINE.SOURCEFREF = BNFLINE.LOGICALREF
BNFLINE.SOURCEFREF = BNFICHE.LOGICALREF
CLFLINE.BANKACCREF = BANKACC.LOGICALREF
CLFLINE.CLIENTREF  = CLCARD.LOGICALREF
```

Сумма `CLFLINE.AMOUNT` совпадает с `BNFLINE.AMOUNT`, а контрагент и
банковский счет разрешаются корректно. Для входящего платежа подтверждена
цепочка:

```text
BNFICHE  TRCODE=3
BNFLINE  TRCODE=3 TRANSTYPE=1 SIGN=0
CLFLINE  MODULENR=7 TRCODE=20 SIGN=1
```

Пример: `CLFLINE_REF=307008` связан с `BNFLINE_REF=9186` и
`BNFICHE_REF=756`; сумма `68167.07`, счет `10202 102.01.001 TURKUAZ HALYK
BANK SOM`. Это подтверждает весь путь от банковского документа до клиентской
проводки. При этом `PAYMENTREF=0`, поэтому распределение платежа на конкретный
счет этой цепочкой не подтверждается.

Полная выгрузка документа `BNFICHE.LOGICALREF=756` показала, что это пакетная
шапка с несколькими строками и разными `BNACCREF`. Поэтому для проверки одной
оплаты всегда нужно фильтровать конкретную строку `BNFLINE`; банковский счет
нельзя брать только из шапки.

Также обнаружена важная особенность join: значения `LOGICALREF` разных таблиц
не образуют общее пространство идентификаторов. Запрос
`CLFLINE.SOURCEFREF IN (BNFLINE.LOGICALREF...)` без
`CLFLINE.MODULENR = 7` может присоединить несвязанные строки счетов
`MODULENR=4` с совпавшим числом. Все join к `CLFLINE` должны включать модуль.

Индексы подтвердили уникальность `BNFICHE(TRCODE, FICHENO)` и
`BNFICHE(DATE_, TRCODE, FICHENO)`. Индекс по `GUID` не уникален. В последних
входящих строках `DOCODE` и `CUSTOMDOCNR` пусты; `DOCODE` имеет длину 32,
`CUSTOMDOCNR` 16, `SPECODE2` 40 символов. Это кандидаты для внешнего id, но
их назначение и возможность записи через LObjects нужно подтвердить.

Исправленный полный join документа `756` вернул 37 банковских строк. Для
каждой строки найдена ровно одна `CLFLINE` с `MODULENR=7`, пропусков нет,
`BNFLINE.AMOUNT = CLFLINE.AMOUNT` во всех 37 случаях. Сумма строк равна
`4471080.67` и точно совпадает с `BNFICHE.DEBITTOT`.

Документ содержит три банковских счета:

```text
BNACCREF=15  10200 100.01.001  TURKUAZ DEMIRBANK SOM
BNACCREF=5   10203 103.01.001  TURKUAZ BAY TUSHUM SOM
BNACCREF=2   10202 102.01.001  TURKUAZ HALYK BANK SOM
```

Для `BNACCREF=2` карточка банка имеет имя `О! БАНК ТТ`, хотя банковский счет
называется `TURKUAZ HALYK BANK SOM`. Это окончательно подтверждает, что
провайдера нужно маппить по согласованному `BANKACC.CODE`, а не по имени
банка или счета.

Проверка журнала одного контрагента (`CLIENTREF=20885`) показала рядом
проводки счетов `MODULENR=4/TRCODE=38/SIGN=0` и банковские поступления
`MODULENR=7/TRCODE=20/SIGN=1`. Для счетов
`CLFLINE.SOURCEFREF = INVOICE.LOGICALREF`; суммы проводки и счета совпадают.
Но у всех строк `PAYMENTREF=0`, а банковские поступления являются отдельными
суммами и не имеют идентификатора закрываемого счета. Например, поступление
`68167.07` не равно одному из показанных счетов. Значит, в исследованной базе
Tiger ведет общий клиентский баланс, а не явный статус оплаты каждого счета.

Практический вывод для интеграции: PaymentGateway остается источником истины
по статусу конкретного invoice. В Tiger нужно создать одно входящее банковское
поступление на нужного контрагента и банковский счет, сохранив внешний id для
идемпотентности. Автоматическое распределение на конкретный Tiger invoice
нельзя считать поддержанным, пока бухгалтер или разработчик Tiger не укажет
отдельный механизм сопоставления.

В ранее полученном списке COM-методов есть потенциальный отдельный механизм
распределения задолженности:

```text
DebtClose(int, int, double, double, double)
DebtCloseFIFO(int, Date, Date)
RollBackDebtClose(int)
```

Эти методы ничего не доказывают о текущих данных и являются записывающими.
Их нельзя вызывать в read-only исследовании. Перед использованием разработчик
Tiger должен подтвердить смысл каждого аргумента и ожидаемый сценарий.

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

- какие поля объекта `doBankVoucher` обязательны;
- как правильно связать оплату с исходным счетом/накладной;
- куда сохранять внешний `payment_id`, чтобы не создавать дубликаты.

## DataObjectType Банковского Документа

Enum `DataObjectType` прочитан напрямую из type library
`C:\LOGO\TIGER3ENT\LObjects.dll` через `LoadTypeLibEx` с
`REGKIND_NONE`. Tiger и SQL при этом не открывались.

Подтверждено:

```text
doBank         = 22
doBankAccount  = 23
doBankVoucher  = 24
```

Для создания `BNFICHE/BNFLINE` нужен
`NewDataObject(DataObjectType.doBankVoucher)`, то есть числовое значение
`24`. Значения `22` и `23` относятся к карточке банка и банковского счета, а
не к банковскому документу.

Проверка в тестовой фирме `923`, период `1`, подтвердила, что
`NewDataObject(24)` и `IData.New()` успешно создают новый банковский объект в
памяти. `Post()` не вызывался, поэтому запись в базе не создавалась.

У `IData` подтверждены необходимые методы и свойства:

```text
DataFields
ValidateErrors
ErrorCode / ErrorDesc / DBErrorDesc
New()
Post()
ExportToXmlStr(...)
ImportFromXmlStr(...)
```

У `IDataFields` подтверждены:

```text
Count
Item(index)
FieldByName(name)
DBFieldByName(name)
GetFieldIndex(name)
SetFieldValue(name, value)
```

Следующий read-only шаг — перечислить свойства `IDataField` и затем имена всех
полей `doBankVoucher`. До подтверждения списка полей `Post()` не вызывается.

`doBankVoucher` в текущей версии LObjects содержит 85 полей. Для каждого
`IDataField` доступны:

```text
fieldName
DBFieldName
FieldType
FieldSize
FieldOffset
Value
Lines
```

Первое поле подтверждено как:

```text
fieldName=INTERNAL_REFERENCE
DBFieldName=LOGICALREF
FieldType=3
FieldSize=4
Value=0
```

Это позволяет получить точные LObjects-имена для шапки и вложенной коллекции
строк непосредственно из установленной версии, без предположений по SQL-именам.

Полный список 85 полей шапки получен. Для будущей минимальной записи особенно
важны:

```text
DATE          -> BNFICHE.DATE_
NUMBER        -> BNFICHE.FICHENO
TYPE          -> BNFICHE.TRCODE
TOTAL_DEBIT   -> BNFICHE.DEBITTOT
NOTES1        -> BNFICHE.GENEXP1
BNACCREF      -> BNFICHE.BNACCOUNTREF
BNACCCODE     -> virtual code field
GUID          -> BNFICHE.GUID
TRANSACTIONS  -> nested bank line collection
```

У `TRANSACTIONS.Lines` подтверждены `AppendLine()`, `AppendLine2()`,
`Item(index)` и `Count`. `AppendLine2()` возвращает `IDataFields` новой строки
и позволяет перечислить точные LObjects-поля `BNFLINE` до вызова `Post()`.

## Первая Тестовая Попытка Post

В тестовой фирме `923`, период `1`, выполнена первая попытка создать
`doBankVoucher=24` на сумму `1` с marker
`PGTEST-20260630-070650`. Результат:

```text
Post result: False
ErrorCode: 0
ErrorDesc: empty
ErrorDetail: empty
DBError: empty
```

Исключения и SQL-ошибки нет. Это указывает на проверку бизнес-полей через
`ValidateErrors`/`ValidateWarns`; следующий шаг — вывести эти коллекции после
неуспешного `Post()`. Успешная запись пока не подтверждена.

Повторная попытка с marker `PGTEST-20260630-071911` успешно раскрыла причину:

```text
Post result: False
ValidateErrors count: 1
ID: 202
Error: Дата не входит в финансовый год !
ValidateWarns count: 0
```

Запись не создана. Структура объекта прошла до проверки финансового периода;
для следующего теста нужно прочитать `BEGDATE/ENDDATE` периода `923/1` и
использовать дату внутри этого диапазона.

`L_CAPIPERIOD` подтвердил диапазон тестовой фирмы `923`, период `1`:

```text
BEGDATE=2023-01-01
ENDDATE=2025-12-31
```

Следующая попытка должна использовать дату `2025-06-30`, не меняя остальной
минимальный payload, чтобы отдельно проверить устранение validation error 202.

## Успешная Тестовая Запись

Повторная попытка в фирме `923`, период `1`, с датой `2025-06-30` успешно
создала банковский документ через LObjects:

```text
Marker: PGTEST-20260630-072504
DataObjectType: doBankVoucher (24)
Post result: True
ValidateErrors: 0
ValidateWarns: 0
Tiger logical reference: 1002
Tiger fiche number: 00000005
```

Тестовый payload:

```text
header TYPE=3, MODULENR=7, SIGN=0, TOTAL_DEBIT=1
line TYPE=1, TRCODE=3, MODULENR=7, SIGN=0, AMOUNT=1
ARP_CODE=120.04.2.02.4456
BANKACC_CODE=10202 102.01.001
```

Это подтверждает возможность создания входящего банковского поступления через
`LObjects.dll` без прямой записи в SQL. Следующий шаг — read-only проверка
`BNFICHE=1002`, связанной `BNFLINE` и автоматически созданной `CLFLINE`.

Последующая проверка через `IData.Read(1002)` уточнила результат:

```text
Read result: True
TableName: LG_923_01_BNFICHE
FICHENO: 00000005
TYPE: 3
Line count: 0
```

Таким образом, `Post()` создал только шапку `BNFICHE`, но строка, полученная
через `TRANSACTIONS.AppendLine2()`, не была сохранена. Полный банковский
документ пока не подтвержден. Следующая попытка должна использовать
`AppendLine()` и затем `Lines.Item(0)`, проверять каждый результат
`SetFieldValue` и не вызывать `Post()`, если `Lines.Count != 1`.

Дополнительная проверка показала, что прямое присваивание `Value` полям строки
`DOC_NUMBER` и `SPECODE2` вызывает `Specified cast is not valid`. Обе попытки
остановились до `Post()`. Эти поля нельзя использовать в минимальном payload.
Та же ошибка получена для `BNFICHE.NOTES2`. Marker следует сохранять в
проверенных доступных полях `BNFICHE.NOTES1` и `BNFLINE.DESCRIPTION`.

## Подтверждение Архитектуры От İvmebilişim

Разработчик Tiger подтвердил рекомендуемую схему: между 1C и Tiger должен быть
отдельный web service/intermediate layer, который получает данные по
согласованным полям и периодически создает нужный тип ваучера через Logo
Objects. Это совпадает с нашим разделением на публичный PaymentGateway и
Windows-only `TigerIntegrationWorker`.

Из-за сетевой схемы выбран pull-вариант: worker на Tiger-сервере выполняет
исходящий HTTPS-запрос к PaymentGateway, получает только оплаченные invoices,
создает `doBankVoucher=24` и отправляет результат обратно. Входящий доступ к
Tiger-серверу из интернета не требуется.

У разработчика нужно уточнить не общую архитектуру, а конкретно:

- минимальные обязательные поля `doBankVoucher` для входящего поступления;
- рекомендуемое поле для внешнего id и идемпотентности;
- требуется ли бизнесу распределять поступление на invoice через `DebtClose`;
- что именно он называет `LogoObject.exe` относительно установленного
  `LObjects.dll`/`UnityObjects`.

## C# Samples От İvmebilişim

Получен архив `LogoObjectsCSharpSamples` (2022). Исполняемые файлы из архива
не запускались; изучены только исходники. Готового примера `doBankVoucher` в
архиве нет, но подтвержден базовый шаблон Logo Objects:

```text
data.New()
lines = data.DataFields.FieldByName("TRANSACTIONS").Lines
lines.AppendLine()
line = lines[lines.Count - 1]
line.FieldByName(...).Value = ...
data.Post()
data.ValidateErrors
```

`AppendLine2()` в присланных примерах не используется. Это поддерживает наше
решение перейти на `AppendLine()` и брать последнюю строку коллекции.

В исходниках также подтверждены `ExportToXmlStr` и `ImportFromXmlStr`.
Следовательно, точную структуру банковского документа можно получить без
записи: прочитать существующий корректный `doBankVoucher` и экспортировать его
в XML-строку. Это предпочтительнее дальнейшего угадывания обязательных полей.

Присланный YouTube-плейлист содержит отдельные материалы `Tahsilat
(DebtClose)` и `ExportToXML - ImportFromXMLFile`. Субтитров у этих видео нет;
видео скачаны и проверены покадрово.

Видео `Tahsilat (DebtClose)` подтвердило точную COM-сигнатуру:

```text
bool DebtClose(
    int pTrFRecRef,
    int pTrSRecRef,
    double DebtAmount = 0,
    double DebtRate = 0,
    double PayRate = 0)
```

Первые два аргумента — `LOGICALREF` двух закрываемых движений из `PAYTRANS`,
а не ссылки на `INVOICE`, `BNFICHE` или `BNFLINE`. Остальные аргументы —
закрываемая сумма, курс закрываемого документа и курс закрывающего документа.
В ролике `DebtClose` вызывается после создания AR/AP voucher; это не пример
создания банковской квитанции. Для нашей базы, где у исследованных строк
`PAYTRANS.CROSSREF=0` и явного распределения платежа на invoice нет, вызывать
`DebtClose` без отдельно найденных PAYTRANS-ссылок нельзя.

Видео `ExportToXML - ImportFromXMLFile` показывает рабочий шаблон:

```csharp
UnityObjects.Data inv =
    Global.unityApp.NewDataObject(UnityObjects.DataObjectType.doSalesInvoice);
inv.Read(7);
inv.ExportToXML("SALES_INVOICES", "C:\\TIGERENT3\\XMLL\\SATIS FATURASI.XML");
```

Перед экспортом существующий объект читается по `LOGICALREF`; первый аргумент
экспорта — имя XML-шаблона/root, второй — путь файла. В том же видео показаны
`ImportFromXMLFile` и атрибуты XML `DBOP="INS"`/`DBOP="UPD"`.

Видео `Tiger 3 XML Aktarımları` оказалось вводным: создание WinForms-проекта,
добавление COM reference `UnityObjects`, общий экземпляр `UnityApplication`,
`Login` и чтение `GetLastError`/`GetLastErrorString`. Банковских объектов и
XML-шаблона банковского ваучера в нем нет.

До получения полноценной строки `BNFLINE` в тестовой фирме нельзя включать
`Post()` в worker или выполнять его в production-фирме `126`.

## Актуальная Документация Polaris

Проверена официальная документация Logo Tiger Uyarlama Aracı на Polaris:

- [общий справочник](https://polaris.logo.cloud/docs/tiger-uyarlama-araci/detail/all);
- разделы `UnityApplication`, `Data`, `Lines`, `ExportToXML`,
  `ExportToXMLStr`, `DebtClose` и список Data Objects.

Документация подтверждает следующие правила:

1. `UnityApplication` является корневым COM-объектом. Рабочая
   последовательность — `Connect`, `UserLogin`, `CompanyLogin`; состояние
   проверяется через `Connected`, `LoggedIn`, `CompanyLoggedIn`.
2. Записи следует создавать только через `IData`. Прямые SQL
   `INSERT/UPDATE/DELETE` могут нарушить связи и целостность данных.
3. После `New()` изменения существуют только в памяти до `Post()`.
   `Post()` записывает весь buffer. При `False` нужно проверять сначала
   `ErrorCode/ErrorDesc/DBErrorDesc`, затем `ValidateErrors`.
4. Для ссылочных полей нельзя использовать `DBFieldName`; следует работать
   через LObjects `FieldByName`.
5. Строки вложенных секций (`TRANSACTIONS` и т. п.) добавляются только через
   `Lines.AppendLine()`. Метод возвращает `Boolean`; после успеха новая строка
   берется как `Lines[Lines.Count - 1]`/`Lines.Item(Count - 1)`.
6. Подтвержденный XML root для `doBankVoucher=24` — `BANK_VOUCHERS`.
7. `ExportToXMLStr(rootKey, xmlString)` возвращает XML через выходной строковый
   параметр. Для уже сохраненного документа сначала нужен `Read(LOGICALREF)`.
   Для нового несохраненного buffer экспорт выполняется до `Post()`; если
   экспорт нужен после `Post()`, документ следует заново прочитать через
   `Read`.
8. Входящее банковское поступление в интерфейсе называется `Gelen Havale/EFT`.
   Документация версий подтверждает его поддержку в XML import/export.
9. `DebtClose` принимает две ссылки `PAYTRANS.LOGICALREF`: одна должна быть
   долгом, другая кредитом, обе — одного контрагента. Полное или частичное
   закрытие является отдельной write-операцией. При остатке Tiger создает
   дополнительные движения.

Следующий шаг теперь полностью определен и не требует угадывания root: в
production-фирме `126` выполнить только `Read(756)` для существующего
корректного `doBankVoucher`, затем `ExportToXMLStr("BANK_VOUCHERS", ...)`.
Это не вызывает `Post`, `Delete` или SQL-запись и должно показать точные поля
шапки и всех 37 строк документа.

Полная русская памятка по Polaris, лицензии, подключению, ошибкам, `Post`, XML
и правилам worker: [TIGER_POLARIS_INTEGRATION_NOTES_RU.md](TIGER_POLARIS_INTEGRATION_NOTES_RU.md).

## Эталонный Документ В Тестовой Фирме

В фирме `923`, период `1`, read-only SQL-проверкой найден существующий
корректный входящий банковский документ со строкой:

```text
BNFICHE.LOGICALREF = 2
FICHENO = 00000002
DATE_ = 2024-05-31
TRCODE = 3
DEBITTOT = 1000
CANCELLED = 0
BNFLINE count = 1
BNFLINE amount sum = 1000
```

Он будет использован вместо production-документа для первого read-only
`ExportToXMLStr("BANK_VOUCHERS", ...)`. До завершения анализа XML все
следующие действия выполняются только в `923/1`; фирма `126/1` не используется.

Read-only экспорт выполнен успешно:

```text
Firm: 923
Period: 1
LogicalRef: 2
Line count: 1
ExportToXMLStr("BANK_VOUCHERS"): True
```

Эталонный XML сохранен в
[`samples/TIGER_BANK_VOUCHER_923_1_REF_2.xml`](samples/TIGER_BANK_VOUCHER_923_1_REF_2.xml).

Помимо уже использованных полей, корректная строка содержит:

```text
CURR_TRANS=37
DEBIT=1000
TC_XRATE=1
TC_AMOUNT=1000
BANK_PROC_TYPE=2
AFFECT_RISK=1
BN_CRDTYPE=3
DIVISION=0
COSTTYPE=1
```

`INTERNAL_REFERENCE`, `SOURCEFREF`, `DATA_REFERENCE`, `GUID`, created fields и
вложенный `PAYMENT_LIST` относятся к сохраненному документу и должны
генерироваться Tiger. Их нельзя копировать в новую запись.

Следующая контролируемая запись в `923/1` должна использовать проверенный XML
import с перечисленными финансовыми полями. После `Post()` объект необходимо
заново прочитать и проверить строку и автоматически созданный `PAYMENT_LIST`.

## Успешная Сборка Через XML Без Записи

Минимальный входящий банковский документ успешно загружен в `doBankVoucher=24`
через `ImportFromXmlStr("BANK_VOUCHERS", xml)` без вызова `Post()`:

```text
Firm/period: 923/1
Import result: True
Line count: 1
Export result: True
ErrorCode: 0
```

PowerShell не смог надежно присваивать числовые COM `Variant` через
`IDataField.Value`, но XML import корректно создал заголовок и строку в памяти.
Подтверждены минимальные поля, банк `10200 100.01.001`, контрагент
`120.04.2.01.1451`, сумма `1` и marker в `NOTES1`. База данных при этой
проверке не изменялась.

Следующий шаг: один контролируемый `Post()` этого payload только в `923/1`,
повторный `Read(LOGICALREF)` и проверка сохраненной строки.

## Успешная Полная Запись В 923/1

Контролируемый `Post()` проверенного XML payload успешно выполнен:

```text
Marker: PG-POST-20260701-071948
Post result: True
BNFICHE.LOGICALREF: 1003
FICHENO: 00000006
Saved TRANSACTIONS count: 1
Generated PAYMENT_LIST count: 1
```

Повторный `Read(1003)` и XML export подтвердили, что Tiger создал полноценный
документ, одну банковскую строку и платежную строку с `SIGN=1`, `TRCODE=3`,
`TOTAL=1`, `TRCURR=37`. Внутренние ссылки, номер, GUID и `PAYMENT_LIST` Tiger
сгенерировал автоматически.

На основе подтвержденного пути в отдельный C# worker добавлены XML import,
`Post`, повторный `Read`, проверка строки, идемпотентный marker, явный список
разрешенных для записи фирм и фоновый pull из PaymentGateway. По умолчанию
`DryRun=true`, `AllowedWriteFirmNos=[]`, polling выключен.

Read-only SQL-проверка созданного документа подтвердила все связанные записи:

```text
BNFICHE.LOGICALREF=1003, FICHENO=00000006, TRCODE=3, DEBITTOT=1
BNFLINE.LOGICALREF=1003, TRCODE=3, TRANSTYPE=1, SIGN=0, AMOUNT=1
BANKACC.LOGICALREF=15, CODE=10200 100.01.001
CLCARD.LOGICALREF=8128, CODE=120.04.2.01.1451
CLFLINE.LOGICALREF=2265, MODULENR=7, TRCODE=20, SIGN=1, AMOUNT=1
PAYTRANS.LOGICALREF=2412, MODULENR=7, TRCODE=3, SIGN=1, TOTAL=1
```

Все записи активны (`CANCELLED=0`), валюта `37`, курс `1`, сумма во всех
слоях равна `1`. Поля `PAYTRANS.BNFCHREF` и `BNFLNREF` равны нулю, что
совпадает с ранее изученным поведением этой базы; связь банковской проводки
доказана через `CLFLINE.MODULENR=7` и `CLFLINE.SOURCEFREF=BNFLINE.LOGICALREF`.

## Привязка QR К Банковским Счетам Tiger

В настройки каждого печатного QR добавлено поле `tiger_bank_account_code`.
Для включенного QR оно обязательно и содержит точный `LG_<firm>_BANKACC.CODE`.
Код переносится в metadata транзакции, затем в invoice-level событие как
`targetBankAccountCode`, после чего worker записывает его в
`TRANSACTIONS.BANKACC_CODE`.

Таким образом, при нескольких QR одного счета именно фактически оплаченный QR
определяет банковский счет Tiger. Провайдер не используется для угадывания
счета. В запрос `POST /api/v1/invoice/qr-codes` 1C также передает обязательный
`client_code` (`CLCARD.CODE`). Технические `source` и `print_qr_slot` удалены
из provider metadata, чтобы сохранить установленный лимит в 5 ключей.
