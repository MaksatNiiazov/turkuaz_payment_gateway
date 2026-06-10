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

React admin сам ходит в backend через `/api`. Отдельный admin-ключ для этих запросов задается на стороне frontend-сервиса в `PAYMENT_ADMIN_API_KEY`; в браузере его вводить не нужно. Внешние интеграционные ключи `INTEGRATION_KEYS` React не использует.
Если в браузере есть общий `identity_access_token`, верхнее меню React admin берет текущего пользователя из Turkuaz Identity через `/identity-api/auth/me`.
Для IIS/static frontend deployments проксируйте `/identity-api/*` в `http://127.0.0.1:8500/api/v1/*`, так же как в `frontend/public/web.config`.

При запуске без Docker держите `PAYMENT_ADMIN_API_KEY` в корневом `.env` проекта и
перезапускайте оба процесса после изменения. Backend читает этот `.env` напрямую,
а Vite frontend-proxy тоже подхватывает его из корня проекта и отправляет в backend
как `X-Admin-Key`.

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
Для admin-интерфейса можно передать ключ так:

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

Интеграционные endpoint'ы для 1С, сайта, POS и ERP защищаются пулом ключей `INTEGRATION_KEYS`.

```env
INTEGRATION_KEYS=1c:secret-for-1c,site:secret-for-site,pos:secret-for-pos
```

Клиентские системы передают только значение ключа:

```text
X-Integration-Key: secret-for-1c
```

Сервис распознает владельца ключа как внутреннюю метку `integration_name=1c`. Это не логин и не передается клиентом отдельно. Для одного ключа используйте тот же формат, например `INTEGRATION_KEYS=1c:secret-for-1c`.

Provider выбирается внутри backend. По умолчанию используется `DEFAULT_PAYMENT_PROVIDER`.
Для разделения по владельцу ключа задайте:

```env
DEFAULT_PAYMENT_PROVIDER=mkassa
PAYMENT_PROVIDER_BY_INTEGRATION=1c_obank:odengi,site:mkassa,pos:odengi
```

Внешние клиенты все равно передают только `X-Integration-Key`, без отдельного `provider`.

Админские endpoints `/api/v1/local/...` защищаются отдельно через `PAYMENT_ADMIN_API_KEY`. React-сервис добавляет его к backend-запросам как `X-Admin-Key`; этот ключ не нужно передавать интеграторам.

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
| `GET` | `/api/v1/local/transactions/{transaction_id}` | Посмотреть сохраненное локальное состояние |
| `GET` | `/api/v1/local/webhooks` | Посмотреть последние webhook-события |
| `GET` | `/api/v1/local/access-events` | Посмотреть, какие интеграции обращались к сервису |

`branch` и `cashier` в платежных endpoint'ах являются реквизитами MKassa. Они не
связаны с филиалами Turkuaz, не используются для доступа пользователей к сервисам и
не требуют отдельного permission. Для О!Деньги эти поля не нужны; provider создает счет
через `createInvoice` и возвращает готовые `qr`, `qr_url`, `link_app` и `site_pay`.

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
      "invoice_number": "TIGER-FACTURE-1001",
      "source": "tiger"
    }
  }'
```

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

Суммы передаются в тыйынах. Динамическая QR-транзакция MKassa по умолчанию ждет оплату 60 секунд. О!Деньги принимает сумму в копейках/тыйынах и при `is_long_living=true` получает `long_term=1`, то есть reusable/static QR по их API. При запросе статуса MKassa может отвечать до 15 секунд, поэтому `REQUEST_TIMEOUT_READ` по умолчанию выставлен в 20 секунд.
