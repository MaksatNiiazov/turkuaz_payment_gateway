# O!Dengi Sandbox API Notes

Based on `Инструкция Sandbox.pdf` and `https://sandbox.dengi.kg/qrpay/*`.

## Endpoint

Sandbox:

```text
https://mw-api-test.dengi.kg/api/json/json.php
```

All requests are JSON `POST` requests with this envelope:

```json
{
  "cmd": "createInvoice",
  "version": 1005,
  "sid": "<merchant_sid>",
  "mktime": "<unix_seconds>",
  "lang": "ru",
  "data": {},
  "hash": "<hmac_md5>"
}
```

## Signature

`hash` is `HMAC-MD5` over compact JSON without the `hash` field, using the merchant API
password as key.

The sandbox UI uses the same logic:

```text
hash_hmac("md5", json_without_hash, merchant_password)
```

For JSON generation, keep UTF-8, no spaces/newlines, and do not escape Unicode.

## Supported Commands

- `createInvoice` - create dynamic QR or reusable/static QR.
- `statusPayment` - read invoice/payment status by `order_id`.
- `invoiceCancel` - cancel unpaid invoice by `invoice_id`.
- `refundPaymentToEwallet` - partial refund by `trans_id`.
- `voidPayment` - full payment cancellation by `trans_id`.
- `getHistoryCsv` - report CSV link for a date range.

## Gateway Mapping

The gateway keeps public client payloads stable:

- Public `id` for O!Dengi responses is our `order_id`.
- Provider `invoice_id` is stored in response extras and local `raw_payload`.
- `metadata.invoice_number` becomes `order_id` when present.
- Dynamic QR is sent as one-time QR: `long_term=0`.
- Dynamic QR receives `date_life` in local UTC+6 time; default lifetime is 24 hours.
- Static QR uses `long_term=1`.
- O!Dengi ready links are returned as extras: `qr`, `qr_url`, `link_app`, `site_pay`.

Callback/result URL:

```text
/api/v1/webhooks/odengi
```

The callback payload includes `order_id`, so local rows are updated by that public gateway ID.
