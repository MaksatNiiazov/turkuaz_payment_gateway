# Turkuaz Payment Gateway

Отдельный payment gateway микросервис. Его можно запускать самостоятельно или подключать к 1С, сайту, POS, фронту и другим сервисам через REST. MKassa и О!Деньги подключены как банковские провайдеры за единым API.

Что внутри:

- Typed async clients для MKassa API и О!Деньги API.
- REST API для динамического QR, статического QR, статусов, списков транзакций, торговых точек и деталей.
- Webhook endpoints для callback-уведомлений от MKassa и О!Деньги.
- SQL-хранилище callback-событий, аудита и последнего состояния транзакций.
- SQLite как текущее хранилище для локального, демо и первого production-этапа.
- Provider/gateway-слой: банковские детали остаются за `PaymentProvider`, внешние системы используют стабильный API.
- Retry для временных ошибок `429/5xx`, явные таймауты, без автоматического следования redirect.
- Валидация сумм и `metadata`: максимум 5 ключей, значение до 150 символов.

## Быстрый запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Заполните нужные provider-ключи в `.env`, затем:

```bash
uvicorn payment_gateway.main:app --host 0.0.0.0 --port 8502 --reload
```

Swagger UI:

```text
http://localhost:8502/docs
```

Базовые страницы просмотра операций:

```text
http://localhost:8502/ui/transactions
http://localhost:8502/ui/webhooks
http://localhost:8502/ui/access-events
```

Отдельный React admin интерфейс:

```text
http://localhost:7502
```

React admin сам ходит в backend через `/api`. Если `PAYMENT_ADMIN_API_KEY` заполнен,
admin endpoints принимают `X-Admin-Key`; также admin endpoints принимают
`Authorization: Bearer <identity_access_token>` и проверяют токен через
`IDENTITY_API_URL`.

React admin открывается через локальную страницу `/login`, как Converter: форма
отправляет email/password в Turkuaz Identity через `/identity-api/auth/login`,
сохраняет `identity_access_token` и дальше отправляет Bearer-токен в admin API.
Внешние интеграционные ключи `INTEGRATION_KEYS` React не использует.
Для IIS/static frontend deployments проксируйте `/identity-api/*` в
`http://127.0.0.1:8500/api/v1/*`, так же как в `frontend/public/web.config`.

В Swagger нажмите `Authorize`, вставьте выданный ключ в `X-Integration-Key`.
Для ручного тестирования используйте раздел `QR Demo` в React admin или `/api/v1/qr/dynamic/form` и `/api/v1/qr/static/form` в Swagger.
JSON endpoint'ы `/api/v1/qr/dynamic` и `/api/v1/qr/static` оставлены для 1С, сайта, POS и других системных интеграций.

Проверка:

```bash
curl http://localhost:8502/api/v1/health
curl http://localhost:8502/api/v1/ready
curl http://localhost:7502/identity-api/ready
```

`/health` оставлен как совместимый alias. Для Turkuaz-сервисов используйте стандартные
`/api/v1/health` и `/api/v1/ready`.

## Docker / SQLite запуск

```bash
cp .env.example .env
docker compose up --build
```

Compose поднимает приложение с SQLite-файлом в Docker volume. Сервис будет доступен на `http://localhost:8502`.
React admin будет доступен на `http://localhost:7502`.
По умолчанию Docker-прокси использует `PAYMENT_ADMIN_API_KEY=admin-dev-key`.
Если нужен другой admin-ключ, его можно передать так:

```bash
PAYMENT_ADMIN_API_KEY=secret-for-admin docker compose up --build
```

Для локального запуска без Docker используйте SQLite:

```env
DATABASE_URL=sqlite:///./data/payment_gateway.db
```

В строгом режиме можно запускать миграции явно:

```bash
AUTO_CREATE_SCHEMA=false DATABASE_URL=sqlite:///./data/payment_gateway.db \
  .venv/bin/python -m alembic upgrade head
```

Подробнее по архитектуре: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Основные endpoint'ы

Интеграционные endpoint'ы для 1С, Tiger worker, сайта и POS защищаются пулом
ключей `INTEGRATION_KEYS`.

```env
INTEGRATION_KEYS=1c:secret-for-1c,tiger:secret-for-tiger,site:secret-for-site,pos:secret-for-pos
```

Клиентские системы передают только значение ключа:

```text
X-Integration-Key: secret-for-1c
```

Сервис распознает владельца ключа как внутреннюю метку `integration_name=1c`.
Это не логин и не передается клиентом отдельно. Для одного ключа используйте тот
же формат, например `INTEGRATION_KEYS=1c:secret-for-1c`.

Provider выбирается внутри backend. По умолчанию используется `DEFAULT_PAYMENT_PROVIDER`.
Для разделения по владельцу ключа задайте:

```env
DEFAULT_PAYMENT_PROVIDER=mkassa
PAYMENT_PROVIDER_BY_INTEGRATION=1c_obank:odengi,site:mkassa,pos:odengi
```

Внешние клиенты все равно передают только `X-Integration-Key`, без отдельного `provider`.

Очереди `/api/v1/local/1c/...` доступны только ключу с именем `1c`, а
`/api/v1/local/tiger/...` - только ключу с именем `tiger`. Обычные POS/site
ключи не могут читать или подтверждать эти очереди.

Админские endpoints `/api/v1/local/...` и `/api/v1/admin/...` принимают либо
`Authorization: Bearer <identity_access_token>`, либо серверный `X-Admin-Key`
из `PAYMENT_ADMIN_API_KEY`. Этот ключ не нужно передавать интеграторам.

Webhook'и банков не требуют `X-Integration-Key` и принимаются по URL без
дополнительного secret-параметра. Backend сохраняет callback в журнал, но
обновляет оплату только если транзакция с таким `id` уже была создана нашим
сервисом.

| Метод | URL | Назначение |
| --- | --- | --- |
| `POST` | `/api/v1/qr/dynamic` | Создать динамическую QR-транзакцию |
| `POST` | `/api/v1/qr/static` | Создать статическую QR-транзакцию |
| `POST` | `/api/v1/qr/dynamic/form` | Создать динамический QR через поля формы в Swagger |
| `POST` | `/api/v1/qr/static/form` | Создать статический QR через поля формы в Swagger |
| `GET` | `/api/v1/qr/render` | Отрисовать PNG QR из `payment_token`, `static_qr_link` или тестовой строки |
| `GET` | `/api/v1/transactions/{transaction_id}` | Получить статус транзакции |
| `PUT` | `/api/v1/transactions/{transaction_id}/cancel` | Отменить неоплаченную динамическую транзакцию |
| `GET` | `/api/v1/transactions` | Получить/отфильтровать список транзакций |
| `GET` | `/api/v1/transaction-details` | Получить детальную информацию за период |
| `GET` | `/api/v1/branches` | Получить список торговых точек и кассиров |
| `POST` | `/api/v1/webhooks/mkassa` | Принять callback от MKassa |
| `POST` | `/api/v1/webhooks/odengi` | Принять callback/result_url от О!Деньги |
| `GET` | `/api/v1/integration` | Проверить, какой `integration_name` распознан по ключу |
| `GET` | `/api/v1/local/tiger/invoice-events/pending` | Для Tiger worker: забрать оплаченные счета на экспорт |
| `POST` | `/api/v1/local/tiger/invoice-events/{event_id}/result` | Для Tiger worker: сохранить результат экспорта в Tiger |
| `GET` | `/api/v1/local/1c/payment-events/pending` | Для 1С: забрать успешные оплаты |
| `POST` | `/api/v1/local/1c/payment-events/{event_id}/result` | Для 1С: подтвердить импорт оплаты или сообщить ошибку |
| `GET` | `/api/v1/local/transactions/{transaction_id}` | Посмотреть сохраненное локальное состояние |
| `GET` | `/api/v1/local/transactions/{transaction_id}/tiger-event-preview` | Посмотреть JSON события оплаченного счета для Tiger |
| `GET` | `/api/v1/local/transactions/{transaction_id}/1c-event-preview` | Посмотреть JSON успешной оплаты для 1С |
| `GET` | `/api/v1/local/tiger/invoice-events` | Админский список статусов экспорта счетов в Tiger |
| `POST` | `/api/v1/local/tiger/invoice-events/{event_id}/reset` | Админский сброс события в `pending` для повторной выгрузки |
| `GET` | `/api/v1/local/1c/payment-events` | Админский список статусов доставки оплат в 1С |
| `POST` | `/api/v1/local/1c/payment-events/{event_id}/reset` | Админский сброс доставки в `pending` для повторного импорта |
| `GET` | `/api/v1/local/webhooks` | Посмотреть последние webhook-события |
| `GET` | `/api/v1/local/access-events` | Посмотреть, какие интеграции обращались к сервису |

`branch` и `cashier` в платежных endpoint'ах являются реквизитами MKassa. Они не
связаны с филиалами Turkuaz, не используются для доступа пользователей к сервисам и
не требуют отдельного permission. Для О!Деньги эти поля не нужны; provider создает счет
через `createInvoice` и возвращает готовые `qr`, `qr_url`, `link_app` и `site_pay`.

### Экспорт оплаченных счетов в Tiger

Tiger server находится во внутренней сети, поэтому интеграция работает как pull:
Windows worker на сервере Tiger раз в заданный интервал запрашивает backend и
забирает только оплаченные счета.

Событие создается на уровне счета, а не отдельной банковской попытки. Если для
одного invoice были QR в нескольких банках, в очередь попадет один оплаченный
invoice, но внутри события будет указано, какой банк реально оплатил счет:
`paidProvider`, `paidTransactionId`, `providerPaymentId`, `targetBankCode`,
`targetBankAccountCode`.

`invoiceId` является ключом идемпотентности для Tiger. Backend не ставит событие
в `pending`, если для Tiger не хватает обязательных полей `paidAt`,
`targetBankAccountCode`, `clientCode` или сумма/валюта не поддержаны: такое
событие остается в статусе `error` в админском списке до исправления данных и
ручного reset. После успешной выгрузки worker отправляет результат в
`/api/v1/local/tiger/invoice-events/{event_id}/result`. Если нужно выгрузить
повторно, админ может сбросить событие endpoint'ом
`/api/v1/local/tiger/invoice-events/{event_id}/reset`.

### Передача успешных оплат в 1С

Каждая сохраненная транзакция со статусом `paid` и стабильным `invoiceId`
автоматически попадает в отдельную очередь 1С. Доставка в Tiger и доставка в
1С подтверждаются независимо: успешный экспорт в одну систему не скрывает
событие от другой.

1С забирает очередь методом `GET /api/v1/local/1c/payment-events/pending`,
обрабатывает `event_payload`, затем подтверждает результат через
`POST /api/v1/local/1c/payment-events/{event_id}/result`. Если 1С возвращает
ошибку, событие уходит в `error` и не выдается повторно до админского reset.
Одна и та же процедура 1С может вызываться регламентным заданием и кнопкой
«Загрузить оплаты», но в 1С должна быть реальная блокировка от параллельного
запуска.

Ключ идемпотентности для 1С — `paymentId`. Перед созданием документа 1С должна
проверить, не импортировала ли она этот `paymentId` ранее. `invoiceId` используется
для поиска исходной реализации. `paymentCode` (`mbank`, `obank`, `qr_3`, `qr_4`)
выбирает локально настроенный банковский счет 1С; GUID счета через API не
передается. Полный контракт и примеры запросов:
[docs/1C_PAYMENT_SYNC.md](docs/1C_PAYMENT_SYNC.md).

## Примеры

Создание динамического QR:

```bash
curl -X POST http://localhost:8502/api/v1/qr/dynamic \
  -H "Content-Type: application/json" \
  -H "X-Integration-Key: secret-for-1c" \
  -d '{
    "amount": 100,
    "branch": 12345,
    "cashier": 1234,
    "is_long_living": true,
    "metadata": {
      "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
      "invoice_number": "TIGER-FACTURE-1001",
      "source": "tiger"
    }
  }'
```

Для 1С `metadata.invoice_id` должен быть стабильным ID ссылки накладной из 1С.
`metadata.invoice_number` можно передавать дополнительно для отображения и поиска
человеком, но бизнес-связку платежей нужно строить по `invoice_id`.

Webhook URL, который нужно передать ответственным лицам MKassa:

```text
https://your-domain.example/api/v1/webhooks/mkassa
```

По документу MKassa callback поддерживается только на домене с действующим SSL-сертификатом и портом `443`.

Webhook/result_url для О!Деньги:

```text
https://your-domain.example/api/v1/webhooks/odengi
```

Для sandbox О!Деньги используйте тестовое приложение `Мой О! + Банк (T)`.
`ODENGI_PASSWORD` в локальном `.env` берите в кавычки, если пароль содержит `#`.

## Проверки

```bash
pytest
ruff check .
```

## Заметки по интеграции

Памятка по интеграции с Logo Tiger и официальной документации Polaris:
[docs/TIGER_POLARIS_INTEGRATION_NOTES_RU.md](docs/TIGER_POLARIS_INTEGRATION_NOTES_RU.md).

Суммы передаются в тыйынах. Динамическая QR-транзакция MKassa по умолчанию ждет оплату 60 секунд. O!Dengi dynamic QR создается как одноразовый счет: `long_term=0`, `date_life` выставляется на 24 часа вперед в локальном времени UTC+6. При запросе статуса MKassa может отвечать до 15 секунд, поэтому `REQUEST_TIMEOUT_READ` по умолчанию выставлен в 20 секунд.
