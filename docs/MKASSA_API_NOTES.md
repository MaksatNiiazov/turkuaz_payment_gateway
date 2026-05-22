# MKassa QR API Notes

Основано на PDF `MKassa- Api QR 13.02.26 — копия.pdf`.

## Общие правила

- Base URL: `https://api.mkassa.kg`.
- Авторизация: `Authorization: api-key <cashier_api_key или api_key>`.
- Суммы передаются в тыйынах.
- `metadata`: максимум 5 ключей, значение до 150 символов.
- Callback принимается только на домене с действующим SSL-сертификатом по порту `443`.
- Динамическая QR-транзакция по умолчанию ожидает оплату 60 секунд.
- Для статуса транзакции нужно быть готовым ждать ответ до 15 секунд.

## Endpoint'ы MKassa

| Метод | URL | Назначение |
| --- | --- | --- |
| `POST` | `/api/partners/transactions/init_payment/` | Создать динамическую QR-транзакцию |
| `PUT` | `/api/partners/transactions/{transaction_id}/cancel/` | Отменить динамическую QR-транзакцию до оплаты |
| `GET` | `/api/partners/transactions/{transaction_id}/` | Получить статус динамической QR-транзакции |
| `POST` | `/api/partners/qr-static/create_static_qr/` | Создать статическую QR-транзакцию |
| `GET` | `/api/partners/v1/transactions/` | Получить или отфильтровать список транзакций |
| `GET` | `/api/partners/transactions-detail/` | Получить детальную информацию о транзакциях |
| `GET` | `/api/partners/branches/` | Получить торговые точки и кассиров |

## Callback payload

MKassa отправляет примерно такой payload:

```json
{
  "id": "MKSA-99f1e3bd71134019af970fc429af8448",
  "status": "paid",
  "amount": "100",
  "created_at": "2024-12-31T18:20:22.800966+06:00",
  "paid_at": null,
  "metadata": {
    "key1": "value1"
  }
}
```

Сервис должен вернуть `HTTP 200 OK`. В документе указано, что MKassa повторяет отправку при `HTTP 500`, поэтому callback сохраняется идемпотентно по hash payload.
