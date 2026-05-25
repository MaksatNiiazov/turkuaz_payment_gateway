# MBank MKassa Integration

Отдельный микросервис для интеграции с MKassa QR API. Его можно запускать самостоятельно или подключать к любому другому сервису через REST.

Что внутри:

- Typed async client для MKassa API.
- REST API для динамического QR, статического QR, статусов, списков транзакций, торговых точек и деталей.
- Webhook endpoint для callback-уведомлений от MKassa.
- SQLite-хранилище callback-событий и последнего состояния транзакций.
- Retry для временных ошибок `429/5xx`, явные таймауты, без автоматического следования redirect.
- Валидация сумм и `metadata`: максимум 5 ключей, значение до 150 символов.

## Быстрый запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Заполните `MKASSA_API_KEY` в `.env`, затем:

```bash
uvicorn mbank_integration.main:app --host 0.0.0.0 --port 8010 --reload
```

Swagger UI:

```text
http://localhost:8010/docs
```

Простейшая страница для ручного тестирования:

```text
http://localhost:8010/demo
```

В Swagger нажмите `Authorize`, вставьте выданный ключ в `X-Integration-Key`.
Для ручного тестирования используйте `/api/v1/qr/dynamic/form` и `/api/v1/qr/static/form`: там можно заполнять отдельные поля без JSON.
JSON endpoint'ы `/api/v1/qr/dynamic` и `/api/v1/qr/static` оставлены для 1С, сайта, POS и других системных интеграций.

Проверка:

```bash
curl http://localhost:8010/health
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Сервис будет доступен на `http://localhost:8010`.

## Основные endpoint'ы

Все endpoint'ы, кроме `/health` и `/api/v1/webhooks/mkassa`, можно защитить пулом ключей `INTEGRATION_KEYS`.

```env
INTEGRATION_KEYS=1c:secret-for-1c,site:secret-for-site,pos:secret-for-pos
```

Клиентские системы передают только значение ключа:

```text
X-Integration-Key: secret-for-1c
```

Сервис распознает владельца ключа как внутреннюю метку `integration_name=1c`. Это не логин и не передается клиентом отдельно. Для одного ключа используйте тот же формат, например `INTEGRATION_KEYS=1c:secret-for-1c`.

| Метод | URL | Назначение |
| --- | --- | --- |
| `POST` | `/api/v1/qr/dynamic` | Создать динамическую QR-транзакцию |
| `POST` | `/api/v1/qr/static` | Создать статическую QR-транзакцию |
| `POST` | `/api/v1/qr/dynamic/form` | Создать динамический QR через поля формы в Swagger |
| `POST` | `/api/v1/qr/static/form` | Создать статический QR через поля формы в Swagger |
| `GET` | `/api/v1/transactions/{transaction_id}` | Получить статус транзакции |
| `PUT` | `/api/v1/transactions/{transaction_id}/cancel` | Отменить неоплаченную динамическую транзакцию |
| `GET` | `/api/v1/transactions` | Получить/отфильтровать список транзакций |
| `GET` | `/api/v1/transaction-details` | Получить детальную информацию за период |
| `GET` | `/api/v1/branches` | Получить список торговых точек и кассиров |
| `POST` | `/api/v1/webhooks/mkassa` | Принять callback от MKassa |
| `GET` | `/api/v1/integration` | Проверить, какой `integration_name` распознан по ключу |
| `GET` | `/api/v1/local/transactions/{transaction_id}` | Посмотреть сохраненное локальное состояние |
| `GET` | `/api/v1/local/webhooks` | Посмотреть последние webhook-события |
| `GET` | `/api/v1/local/access-events` | Посмотреть, какие интеграции обращались к сервису |

## Примеры

Создание динамического QR:

```bash
curl -X POST http://localhost:8010/api/v1/qr/dynamic \
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

Если включен `WEBHOOK_SHARED_SECRET`, передайте URL с secret-параметром:

```text
https://your-domain.example/api/v1/webhooks/mkassa?secret=your-webhook-secret
```

По документу MKassa callback поддерживается только на домене с действующим SSL-сертификатом и портом `443`.

## Проверки

```bash
pytest
ruff check .
```

## Заметки по интеграции

Суммы передаются в тыйынах. Динамическая QR-транзакция по умолчанию ждет оплату 60 секунд. При запросе статуса MKassa может отвечать до 15 секунд, поэтому `REQUEST_TIMEOUT_READ` по умолчанию выставлен в 20 секунд.
