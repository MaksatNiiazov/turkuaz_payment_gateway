# Tiger Read-only Discovery

Дата последней проверки: 2026-06-29

Этот документ хранит только безопасные диагностические команды для текущей
интеграции с Logo Tiger. Они ничего не создают, не обновляют и не удаляют.

## Правила Безопасности

1. В SSMS всегда открывать новую вкладку через `New Query`.
2. Перед `Execute` проверять, что во вкладке есть только `SELECT`.
3. Никогда не выполнять `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`,
   `DROP`, `ALTER`, `CREATE` или хранимые процедуры изменения данных.
4. Не записывать документы напрямую в SQL-базу Tiger.
5. Будущие документы создавать только через официальный LObjects API и сначала
   только в тестовой базе или после отдельного разрешения.

Во время проверки была замечена старая вкладка SSMS с `UPDATE TBLMUSTERI`.
Она не относится к интеграции и не должна выполняться вместе с нашими запросами.

## Подтверждение COM Регистрации

```powershell
$progId = "UnityObjects.UnityApplication"
$type = [type]::GetTypeFromProgID($progId)

if ($null -eq $type) {
  "NOT_REGISTERED"
} else {
  "REGISTERED"
  $type.GUID
}
```

Подтвержденный результат:

```text
REGISTERED
72db412a-6bf5-4920-a002-2aac679951df
```

## Версия И Путь Tiger

```powershell
$app = New-Object -ComObject UnityObjects.UnityApplication

try {
  "Version:"
  $app.Version()
  "AppPath:"
  $app.GetAppPath()
  "Connected:"
  $app.Connected
  "LoggedIn:"
  $app.LoggedIn
}
finally {
  try { $app.Disconnect() } catch {}
  [Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
```

Подтверждено:

```text
Version: Logo Objects 030700
AppPath: C:\LOGO\TIGER3ENT\
```

## Безопасная Проверка Login

Пароль намеренно не хранится в репозитории. Вместо `<password>` используется
секрет, известный оператору сервера.

```powershell
$app = New-Object -ComObject UnityObjects.UnityApplication

try {
  $app.Connect()
  $app.UserLogin("MAKSAT.NIIAZOV", "<password>")
  $app.CompanyLogin(126, 1)

  $app.Connected
  $app.LoggedIn
  $app.CompanyLoggedIn
  $app.CurrentFirm
  $app.CurrentPeriod
  $app.GetLastError()
  $app.GetLastErrorString()
}
finally {
  try { $app.CompanyLogout() } catch {}
  try { $app.UserLogout() } catch {}
  try { $app.Disconnect() } catch {}
  [Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
```

Подтверждено: три login-вызова возвращают `True`, `CurrentFirm=126`,
`CurrentPeriod=1`, `LastError=0`.

## Рабочий SELECT Через LObjects

```sql
SELECT TOP 5 LOGICALREF, CODE, DEFINITION_
FROM LG_126_CLCARD
ORDER BY LOGICALREF
```

Рабочая итерация результата:

```powershell
$hasRow = $q.First()
while ($hasRow) {
  $q.FieldByName("LOGICALREF")
  $q.FieldByName("CODE")
  $q.FieldByName("DEFINITION_")
  $hasRow = $q.Next()
}
```

Не использовать `EOF` и не вызывать `Next()` после того, как он вернул
`False`.

## Поиск Таблиц В SSMS

```sql
SELECT
    @@SERVERNAME AS ServerName,
    DB_NAME() AS DatabaseName;

SELECT TOP (2000)
    name AS TableName
FROM sys.tables
WHERE name LIKE 'LG[_]126[_]%'
   OR name LIKE '%INVOICE%'
ORDER BY name;
```

Подтверждены таблицы фирмы `126`, включая:

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

`TOP (2000)` остается read-only и не меняет базу.

## Последние Счета С Контрагентами

Следующий утвержденный read-only запрос:

```sql
SELECT TOP (100)
    I.LOGICALREF,
    I.FICHENO,
    I.DOCODE,
    I.DATE_,
    I.TRCODE,
    I.CLIENTREF,
    C.CODE AS CLIENT_CODE,
    C.DEFINITION_ AS CLIENT_NAME,
    I.NETTOTAL,
    I.CANCELLED
FROM dbo.LG_126_01_INVOICE AS I
LEFT JOIN dbo.LG_126_CLCARD AS C
    ON C.LOGICALREF = I.CLIENTREF
ORDER BY I.LOGICALREF DESC;
```

Цель запроса: определить, какое поле связывает событие PaymentGateway с
документом Tiger. Основные кандидаты: `FICHENO`, `DOCODE` или отдельное поле,
которое заполняется исходной системой. Решение принимается только после анализа
реальных строк.

### Результат Проверки

Запрос успешно вернул 100 строк. В выборке:

- `LOGICALREF`: `170090`-`170189`, без повторов в полученном наборе;
- `FICHENO`: последовательные значения до `00058918`, заполнены во всех
  полученных строках;
- `DOCODE`: часто пустой, повторяется и содержит маршрутные/пользовательские
  значения наподобие `DOSTOR-21`;
- `TRCODE`: встречаются значения `8` и `1`;
- `CLIENTREF` успешно соединяется с карточкой клиента;
- `NETTOTAL` заполнен;
- `CANCELLED=0` во всех 100 строках.

`DOCODE` исключен из кандидатов на уникальный идентификатор. Текущие кандидаты:

1. `LOGICALREF` как внутренний неизменяемый идентификатор Tiger.
2. `FICHENO` как видимый номер документа, дополненный фирмой, периодом и
   контрольной суммой.

Уникальность `FICHENO` во всей таблице и его доступность в 1С еще нужно
проверить.

## Колонки Банковских Таблиц

Следующий безопасный запрос получает только метаданные нужных таблиц:

```sql
SELECT
    T.name AS TABLE_NAME,
    C.column_id AS COLUMN_ID,
    C.name AS COLUMN_NAME,
    TY.name AS DATA_TYPE,
    C.max_length AS MAX_LENGTH,
    C.is_nullable AS IS_NULLABLE
FROM sys.tables AS T
JOIN sys.columns AS C
    ON C.object_id = T.object_id
JOIN sys.types AS TY
    ON TY.user_type_id = C.user_type_id
WHERE T.name IN (
    'LG_126_01_BNFICHE',
    'LG_126_01_BNFLINE',
    'LG_126_BANKACC',
    'LG_126_BNCARD',
    'LG_126_01_CLFLINE'
)
ORDER BY T.name, C.column_id;
```

По результату будет составлен точный read-only запрос существующих банковских
операций без предположений о названиях колонок.

### Проверяемые Связи

Метаданные успешно прочитаны. По названиям полей для следующей выборки
проверяем:

```text
BNFLINE.SOURCEFREF -> BNFICHE.LOGICALREF
BNFLINE.CLIENTREF  -> CLCARD.LOGICALREF
BNFLINE.BANKREF    -> BNCARD.LOGICALREF
BNFLINE.BNACCREF   -> BANKACC.LOGICALREF
```

В `BNFLINE` присутствуют необходимые для интеграции поля суммы, валюты,
клиента, банка, банковского счета и внешних ссылок. В `BANKACC` присутствуют
код и наименование счета; реальные номера счетов и IBAN не требуется выводить
для текущего анализа.

## Последние Банковские Операции

```sql
SELECT TOP (100)
    L.LOGICALREF AS BANK_LINE_LOGICALREF,
    H.LOGICALREF AS BANK_FICHE_LOGICALREF,
    H.FICHENO AS BANK_FICHENO,
    L.DATE_,
    H.TRCODE AS FICHE_TRCODE,
    L.TRCODE AS LINE_TRCODE,
    L.TRANSTYPE,
    L.SIGN,
    L.AMOUNT,
    L.TRCURR,
    L.TRNET,
    L.CLIENTREF,
    C.CODE AS CLIENT_CODE,
    C.DEFINITION_ AS CLIENT_NAME,
    B.CODE AS BANK_CODE,
    B.DEFINITION_ AS BANK_NAME,
    A.CODE AS BANK_ACCOUNT_CODE,
    A.DEFINITION_ AS BANK_ACCOUNT_NAME,
    L.TRANNO,
    L.DOCODE,
    L.LINEEXP,
    L.BANKREFNR,
    L.CUSTOMDOCNR,
    L.PAYMENTREF,
    L.CLFLINEREF,
    L.CLFICHEREF,
    H.CANCELLED AS FICHE_CANCELLED,
    L.CANCELLED AS LINE_CANCELLED
FROM dbo.LG_126_01_BNFLINE AS L
LEFT JOIN dbo.LG_126_01_BNFICHE AS H
    ON H.LOGICALREF = L.SOURCEFREF
LEFT JOIN dbo.LG_126_CLCARD AS C
    ON C.LOGICALREF = L.CLIENTREF
LEFT JOIN dbo.LG_126_BNCARD AS B
    ON B.LOGICALREF = L.BANKREF
LEFT JOIN dbo.LG_126_BANKACC AS A
    ON A.LOGICALREF = L.BNACCREF
ORDER BY L.LOGICALREF DESC;
```

Запрос нужен для определения сочетания `TRCODE`, `TRANSTYPE` и `SIGN`, которым
в этой базе представлена входящая оплата клиента, и для получения кодов
банковских счетов Mbank/O!Bank. Он ничего не изменяет.

### Результат Банковской Выборки

Получено 100 строк:

```text
63 x TRCODE=3, TRANSTYPE=1, SIGN=0: входящие поступления
29 x TRCODE=4, TRANSTYPE=1, SIGN=1: исходящие платежи
 8 x TRCODE=2, TRANSTYPE=1: парные переводы SIGN=0/1
```

Во всех строках `FICHE_CANCELLED=0` и `LINE_CANCELLED=0`. Входящая банковская
операция в текущей базе представлена документом/строкой `TRCODE=3` и
`SIGN=0`.

В показанной выборке поля `PAYMENTREF`, `CLFLINEREF` и `CLFICHEREF` равны нулю.
Прямой связи банковской строки с `INVOICE` здесь не найдено. Следующая задача —
проверить `PAYTRANS` и клиентские проводки, через которые Tiger закрывает
задолженность по счету.

Не использовать имя банка для автоматического маппинга: карточка с названием
`О! БАНК ТТ` связана со счетом, имя которого содержит `HALYK BANK`. Для каждого
провайдера нужен согласованный `BANK_ACCOUNT_CODE`.

## Колонки PAYTRANS

Метаданные `LG_126_01_PAYTRANS` прочитаны read-only через `sys.columns`.
Важные для интеграции поля:

```text
LOGICALREF, CARDREF, DATE_, MODULENR, SIGN, FICHEREF, FICHELINEREF,
TRCODE, TOTAL, PAID, CROSSREF, PAIDINCASH, CANCELLED, PROCDATE,
BANKACCREF, TRNET, NETTOTAL, BNFCHREF, BNFLNREF, DOCODE, LINEEXP,
MATCHDATE
```

Это подтверждает, что `PAYTRANS` может быть связующим слоем между счетом,
закрытием задолженности и банковским документом:

```text
PAYTRANS.FICHEREF   -> исходный документ, например INVOICE.LOGICALREF
PAYTRANS.CARDREF    -> контрагент
PAYTRANS.BANKACCREF -> банковский счет
PAYTRANS.BNFCHREF   -> BNFICHE.LOGICALREF, если создана банковская операция
PAYTRANS.BNFLNREF   -> BNFLINE.LOGICALREF, если создана банковская строка
```

Следующая проверка должна быть по конкретному счету, чтобы увидеть, создаются ли
для него строки `PAYTRANS`, заполнены ли `PAID`/`CROSSREF`, и появляются ли
ссылки на `BNFICHE`/`BNFLINE`.

Проверка по `INVOICE.LOGICALREF=170189` показала одну строку `PAYTRANS`:

```text
PAYTRANS_REF=455809
MODULENR=4
SIGN=0
TRCODE=8
TOTAL=11986.96
PAID=0
CROSSREF=0
BANKACCREF=0
BNFCHREF=0
BNFLNREF=0
```

Это неоплаченный счет: строка задолженности есть, но закрытие и связь с банком
еще отсутствуют.

Следующая проверка по `PAYTRANS` с фильтром `MODULENR = 4` и признаками оплаты
`PAID > 0`, `CROSSREF > 0`, `BNFCHREF > 0`, `BNFLNREF > 0` вернула пустой
результат. Для дальнейшего анализа нужно смотреть все `MODULENR` и/или
клиентские проводки.

Агрегация всей `PAYTRANS` подтвердила, что поля `PAID`, `CROSSREF`,
`BANKACCREF`, `BNFCHREF`, `BNFLNREF` не заполнены во всех найденных группах.
Значит, эта таблица в текущей базе не дает прямой связи оплаты с банковским
документом. Следующий read-only этап — `CLFICHE`/`CLFLINE`.

## Колонки CLFICHE И CLFLINE

Метаданные `LG_126_01_CLFICHE` и `LG_126_01_CLFLINE` успешно прочитаны
read-only. Для поиска закрытия счета особенно важны:

```text
CLFICHE: LOGICALREF, FICHENO, DATE_, DOCODE, TRCODE, INVOREF,
         CANCELLED, CLCARDREF, BANKACCREF, BNACCREF

CLFLINE: LOGICALREF, CLIENTREF, SOURCEFREF, DATE_, MODULENR, TRCODE,
         SIGN, AMOUNT, EXTENREF, PAYMENTREF, CANCELLED,
         BANKACCREF, BNACCREF
```

Следующий шаг — сначала агрегировать `CLFLINE` по `MODULENR/SIGN/TRCODE` и
посчитать заполненность `SOURCEFREF`, `PAYMENTREF`, `BANKACCREF`, `BNACCREF`.
После этого можно безопасно выбрать только реальные банковские группы и
проверить ссылки на `BNFLINE`, `BNFICHE` и `PAYTRANS`.

Агрегация выполнена. Банковские группы имеют следующие признаки:

```text
MODULENR=7 SIGN=1 TRCODE=20 ROW_COUNT=4436
MODULENR=7 SIGN=0 TRCODE=21 ROW_COUNT=3693
```

Для всех этих строк заполнены `SOURCEFREF` и `BANKACCREF`. Во всех строках
`PAYMENTREF=0`, `BNACCREF=0`, `EXTENREF=0`. Следовательно, банк можно
определить непосредственно из клиентской проводки, а распределение на счет
через `PAYMENTREF` в этой базе не ведется. Точный смысл `SOURCEFREF` еще нужно
подтвердить join-проверкой с `BNFLINE`.

Join-проверка последних 50 строк подтвердила:

```text
CLFLINE.SOURCEFREF = BNFLINE.LOGICALREF
BNFLINE.SOURCEFREF = BNFICHE.LOGICALREF
CLFLINE.BANKACCREF = BANKACC.LOGICALREF
CLFLINE.CLIENTREF  = CLCARD.LOGICALREF
```

Для входящего банковского платежа фактические признаки в фирме 126:

```text
BNFICHE.TRCODE=3
BNFLINE.TRCODE=3, TRANSTYPE=1, SIGN=0
CLFLINE.MODULENR=7, TRCODE=20, SIGN=1
```

Суммы на `BNFLINE` и `CLFLINE` совпадают. Это доказанная схема проводки,
которую можно использовать для проверки будущей записи через LObjects.
Прямой связи с `INVOICE` все еще нет: `CLFLINE.PAYMENTREF=0`, а поля закрытия
в `PAYTRANS` также не заполняются.

Важно: `LOGICALREF` уникален только внутри своей таблицы. Join из `BNFLINE` в
`CLFLINE` должен использовать одновременно:

```sql
CLFLINE.MODULENR = 7
AND CLFLINE.SOURCEFREF = BNFLINE.LOGICALREF
```

Без условия по модулю совпавшие числа могут вернуть несвязанные invoice-строки
`MODULENR=4`. Полная выгрузка `BNFICHE=756` также показала, что одна шапка
содержит строки разных банковских счетов, поэтому `BNACCREF` нужно читать на
уровне `BNFLINE`.

Исправленный join для `BNFICHE=756` проверен на всех 37 строках:

```text
BNFLINE rows:                    37
CLFLINE rows found:              37
BNFLINE/CLFLINE amount matches:  37
SUM(BNFLINE.AMOUNT):             4471080.67
BNFICHE.DEBITTOT:                4471080.67
```

Пропусков и расхождений нет. В одной шапке используются `BNACCREF` 15, 5 и
2. Имя карточки банка не всегда совпадает с названием банковского счета;
автоматический provider mapping должен использовать `BANKACC.CODE`.

Проверка всех проводок одного контрагента (`CLIENTREF=20885`) подтвердила:

```text
invoice: CLFLINE.MODULENR=4, TRCODE=38, SIGN=0
         CLFLINE.SOURCEFREF=INVOICE.LOGICALREF
bank:    CLFLINE.MODULENR=7, TRCODE=20, SIGN=1
         CLFLINE.SOURCEFREF=BNFLINE.LOGICALREF
```

У обеих групп `PAYMENTREF=0`. Банковские суммы не соответствуют одному
конкретному счету, поэтому по имеющимся данным Tiger хранит общий баланс
контрагента без явного invoice-level allocation. Не следует выводить статус
конкретного счета только из этих таблиц.

## Известные Неудачные Запросы LObjects

Через `NewQuery().OpenDirect()` не открылись:

```sql
SELECT TOP 5 LOGICALREF FROM LG_126_01_INVOICE
```

```sql
SELECT TOP 100 NAME FROM SYS.TABLES WHERE NAME LIKE '%INVOICE%'
```

Оба дали:

```text
DISP_E_NOTACOLLECTION (0x80020011)
LastError: -10
LastErrorString: Не удалось создать SQL запрос.
```

При этом SSMS подтвердил существование `LG_126_01_INVOICE`. Значит, ошибка
относится к ограничениям `NewQuery/OpenDirect`, а не к отсутствию таблицы.

## Следующие Read-only Проверки

Type library inspection confirmed that the bank voucher object is:

```text
DataObjectType.doBankVoucher = 24
```

This value comes from `LObjects.dll` itself, not from a database table. The
inspection used `REGKIND_NONE` and did not connect to Tiger or SQL.

Покадровая проверка видео Logo Objects уточнила назначение `DebtClose`:

```text
DebtClose(pTrFRecRef, pTrSRecRef, DebtAmount, DebtRate, PayRate)
```

`pTrFRecRef` и `pTrSRecRef` — это `PAYTRANS.LOGICALREF` двух закрываемых
движений. Метод не принимает напрямую `INVOICE.LOGICALREF` или ссылку
банковского документа. Поэтому он не заменяет отсутствующее invoice-level
сопоставление и не должен вызываться на найденных ссылках из `BNFICHE` или
`BNFLINE`.

Также подтвержден безопасный путь получения схемы объекта: `NewDataObject`,
`Read(LOGICALREF)`, затем `ExportToXML`/`ExportToXmlStr`. Официальный список
Logo REST Resources подтвердил XML root банковского документа:

```text
DataObjectType.doBankVoucher = 24
XML root = BANK_VOUCHERS
REST resource = bankVouchers
```

Документация `Lines` предписывает использовать `AppendLine()`, проверить его
Boolean-результат и затем обращаться к новой строке по
`Lines.Item(Lines.Count - 1)`. Это окончательно исключает `AppendLine2()` из
будущей реализации worker.

После получения последних счетов нужно без записи:

1. Определить поле внешнего номера счета.
2. Найти клиентские проводки банковского модуля в `CLFLINE` и проверить
   `SOURCEFREF`/`PAYMENTREF` на реальных строках.
3. Определить банковский счет из `BANKACC`/`BNCARD`.
4. Сопоставить Mbank и O!Bank с кодами банковских счетов Tiger.
5. Экспортировать существующий `doBankVoucher` через root `BANK_VOUCHERS` и
   зафиксировать обязательные поля шапки и `TRANSACTIONS`.

Для этого найден эталон в тестовой фирме `923/1`: `BNFICHE.LOGICALREF=2`,
`FICHENO=00000002`, `TRCODE=3`, одна строка, сумма шапки и строки `1000`.
Первый XML-экспорт выполняется только для этого тестового документа; production
`126/1` пока не используется.
