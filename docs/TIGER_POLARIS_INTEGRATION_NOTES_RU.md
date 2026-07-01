# Logo Tiger / PaymentGateway: памятка по Polaris

Проверено по актуальному официальному справочнику Logo Tiger Uyarlama Aracı
на Polaris и сопоставлено с установленным `LObjects.dll` версии
`Logo Objects 030700`.

## Выбранная архитектура

- PaymentGateway остается публичным источником статуса счета и победившего банка.
- Windows-worker на сервере Tiger каждые 30 минут делает исходящий HTTPS
  запрос, получает только оплаченные счета и записывает поступления в Tiger.
- Tiger-серверу не нужен входящий доступ из интернета.
- Запись выполняется только через Logo Objects `IData`. Прямые SQL
  `INSERT`, `UPDATE` и `DELETE` запрещены.
- COM-вызовы следует выполнять последовательно в одном worker-сеансе.

Официальный Logo Objects REST существует, но переходить на него сейчас нет
оснований: установленный и проверенный интерфейс — локальный COM
`UnityObjects`, а наличие и настройка REST runtime на сервере не подтверждены.

## Подключение и лицензия

```text
Connect()
UserLogin(user, password)
CompanyLogin(firm, period)
```

После каждого шага проверяется Boolean-результат, а также `Connected`,
`LoggedIn`, `CompanyLoggedIn`, `CurrentFirm` и `CurrentPeriod/ActivePeriod`.
При ошибке сохраняются одновременно `GetLastError()` и
`GetLastErrorString()`.

```text
-1  базовое подключение не установлено
-2  нет подключения к БД / права пользователя БД
-3  неверное имя пользователя
-5  неверный пароль
-7  недоступна фирма или активный период
-8  вход в фирму не выполнен: права, период, лимит или версия
-13 нет Logo Objects runtime-лицензии
-93 превышен терминальный/пользовательский лимит Logo Objects
```

Лицензия Logo Objects продается на сервер, а не на конкретного пользователя.
Для запуска требуется runtime-лицензия. Учетную запись worker следует выделить
отдельно и дать ей только необходимые права фирмы и банка.

## Объект банковского поступления

```text
DataObjectType: doBankVoucher
numeric value: 24
XML root: BANK_VOUCHERS
REST resource name: bankVouchers
header table: LG_<firm>_<period>_BNFICHE
line table: LG_<firm>_<period>_BNFLINE
line collection: TRANSACTIONS
```

Входящее поступление в Tiger называется `Gelen Havale/EFT`. На production
данных фирмы 126 подтверждены признаки:

```text
BNFICHE.TRCODE = 3
BNFLINE.TRCODE = 3
BNFLINE.TRANSTYPE = 1
BNFLINE.SIGN = 0
CLFLINE.MODULENR = 7
CLFLINE.TRCODE = 20
CLFLINE.SIGN = 1
```

## Работа с IData и полями

`New()` очищает buffer и готовит новый объект. До `Post()` база данных не
изменяется.

`DataField.FieldName` — имя поля в XML/LObjects. `DBFieldName` — имя SQL
колонки. Нужно использовать `FieldByName`; для ссылочных полей документация
прямо запрещает полагаться на `DBFieldName`.

`DataField.Value` имеет COM-тип `Variant`. В C# нужно передавать значения
правильного CLR-типа: `DateTime` для дат, числовой тип для кодов/сумм и
`string` для кодовых полей. Индексы начинаются с нуля, но документация не
рекомендует обращаться к полям по индексам.

```csharp
var lines = data.DataFields.FieldByName("TRANSACTIONS").Lines;

if (!lines.AppendLine())
    throw new InvalidOperationException("Tiger did not append a line");

var line = lines[lines.Count - 1];
line.FieldByName("...").Value = ...;
```

`AppendLine2()` не используется в официальной документации и не должен
использоваться worker. Перед `Post()` проверяется `Lines.Count`.

## Post и повторное чтение

`Post()` записывает весь buffer и возвращает `Boolean`. Часть вычислений Tiger
выполняет внутри `Post()`.

После успешного `Post()` вложенные объекты освобождаются из памяти. Поэтому
нужно получить `LOGICALREF`, выполнить `Read(LOGICALREF)` и только затем
проверять `TRANSACTIONS.Count` или экспортировать XML.

Запись считается успешной только если:

1. `Post()` вернул `True`.
2. Получен ненулевой `LOGICALREF`.
3. Повторный `Read(LOGICALREF)` вернул `True`.
4. `TRANSACTIONS.Count` равен ожидаемому числу строк.
5. Сумма, клиент и банковский счет совпадают с событием PaymentGateway.

## Обработка ошибок

При `Post() == False`:

1. Если `ErrorCode != 0`, сохранить `ErrorCode`, `ErrorDesc` и `DBErrorDesc`.
2. Перебрать `ValidateErrors` от `0` до `Count - 1` и сохранить `ID` и
   `Error`. У установленной COM-версии также доступен `ErrorDetail`.
3. Не отмечать событие выгруженным только потому, что вызов не бросил исключение.

```text
Database 7   строки/детали документа не записаны
Database 8   добавление записи не выполнено
Database 15  недопустимое значение
Database 20  у пользователя нет прав

Validation 508   код контрагента не найден
Validation 510   банковский счет не найден
Validation 7102  код банковского счета не указан
Validation 7103  номер банковского документа не указан
Validation 201   дата вне финансового года
Validation 202   дата не является рабочим днем
Validation 720   тип документа не указан
Validation 721   тип документа не соответствует модулю
```

Номера и локализованные тексты могут отличаться между версиями. Установленная
версия уже возвращала ID `202` с другим русским текстом. Поэтому логируются и
код, и фактическое сообщение; бизнес-логика не строится только на номере.

## XML export

Для существующего документа:

```text
NewDataObject(doBankVoucher)
Read(LOGICALREF)
ExportToXMLStr("BANK_VOUCHERS", out xml)
```

Для нового несохраненного buffer XML можно получить до `Post()`. После
`Post()` объект нужно заново прочитать через `Read`, иначе строки в XML могут
отсутствовать.

XML import worker сейчас не нужен. Поля будут задаваться через типизированный
`IData`, а XML используется как эталон структуры и для диагностики.

## DebtClose

`DebtClose` не является частью создания банковского документа. Это отдельная
write-операция закрытия двух движений `PAYTRANS`:

```text
DebtClose(pTrFRecRef, pTrSRecRef, DebtAmount, DebtRate, PayRate)
```

Одна ссылка должна быть долгом, другая оплатой; обе должны относиться к одному
контрагенту. При частичном закрытии Tiger может создать дополнительные
движения. Вызывать `DebtClose` можно только если бизнес требует распределения
на конкретный счет и найдены правильные `PAYTRANS.LOGICALREF`.

На первом этапе PaymentGateway остается источником статуса счета, а Tiger
получает поступление на баланс контрагента.

## Идемпотентность

- В PaymentGateway одно событие имеет неизменяемый внешний ID.
- Worker не берет событие повторно после подтвержденного успеха.
- Внешний ID нужно записать в проверенное текстовое поле Tiger. Кандидаты:
  `NOTES1` шапки и `DESCRIPTION` строки.
- Если `Post()` мог завершиться до обрыва связи, worker сначала ищет/читает
  документ по marker, а не выполняет повторный `Post()` вслепую.
- Ручной reset экспорта остается административной операцией PaymentGateway.

## Что еще нужно подтвердить XML

Официальная документация не публикует полный минимальный payload
`BANK_VOUCHERS`. Осталось получить:

- точные поля шапки и `TRANSACTIONS`;
- обязательные поля и фактические типы;
- достаточные коды/ссылки банка и контрагента;
- безопасное поле внешнего ID;
- валютные поля для сомового поступления.

Эталонный XML `923/1`, `LOGICALREF=2`, получен. Он подтвердил для сомового
входящего поступления:

```text
header: DATE, TYPE=3, TOTAL_DEBIT, NOTES1, CURRSEL_TOTALS=1
line: TYPE=1, BANKACC_CODE, ARP_CODE, DATE, TRCODE=3, MODULENR=7
line: CURR_TRANS=37, DEBIT, AMOUNT, TC_XRATE=1, TC_AMOUNT
line: BANK_PROC_TYPE=2, DUE_DATE, AFFECT_RISK=1, BN_CRDTYPE=3
line: DIVISION=0, COSTTYPE=1
```

Внутренние ссылки, GUID, metadata создания, `SOURCEFREF`, `DATA_REFERENCE` и
`PAYMENT_LIST` не переносятся из XML: их должен создать `Post()`.

## Следующие шаги

1. Собрать минимальный payload по эталонному XML и сделать один `Post()` в
   `923/1` с `AppendLine()`.
2. Повторно прочитать документ и проверить `BNFLINE`, `PAYMENT_LIST` и
   `CLFLINE` read-only.
3. Реализовать payload в `TigerIntegrationWorker`, сохраняя `DryRun=true` до
   полного end-to-end теста.

До отдельного решения пользователя production-фирма `126/1` не используется
даже для XML discovery.

Минимальный payload уже успешно проверен без записи через
`ImportFromXmlStr`: импорт и обратный экспорт вернули `True`, а коллекция
`TRANSACTIONS` содержит одну строку. Для PowerShell выбран XML import, чтобы
избежать ошибок преобразования COM `Variant`; C# worker сможет использовать
тот же проверенный XML-контракт.

Затем тот же payload успешно записан в `923/1`: создан документ
`LOGICALREF=1003`, `FICHENO=00000006`, после повторного `Read` найдены одна
строка `TRANSACTIONS` и одна автоматически созданная строка `PAYMENT_LIST`.
Это подтверждает полный write path для входящего банковского поступления.

SQL read-back дополнительно подтвердил физические записи `BNFICHE=1003`,
`BNFLINE=1003`, `CLFLINE=2265` и `PAYTRANS=2412`. Сумма, валюта и направления
проводок согласованы; документ не отменен.

## Официальный источник

- [Logo Tiger Uyarlama Aracı](https://polaris.logo.cloud/docs/tiger-uyarlama-araci/detail/all)
- Разделы Logo Objects Library: `UnityApplication`, `Data`, `DataField`,
  `Lines`, `Post`, `ValidateError`, `ValidateErrors`, `ExportToXML`,
  `ExportToXMLStr`, `DebtClose`, database и XML errors.
