from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from payment_gateway.gateway import PaymentGateway
from payment_gateway.models import (
    BranchListResponse,
    CancelResponse,
    DynamicQRCreate,
    DynamicQRResponse,
    StaticQRCreate,
    StaticQRResponse,
    Transaction,
    TransactionDetailListResponse,
    TransactionListResponse,
    WebhookPayload,
)
from payment_gateway.store import PaymentStore, WebhookStoreResult


REUSABLE_INVOICE_QR_STATUSES = {"inited", "waiting", "qr_scanned", "paid"}
AUTO_CANCEL_INVOICE_QR_STATUSES = {"inited", "waiting", "qr_scanned", "unknown"}
PAID_STATUS = "paid"
logger = logging.getLogger(__name__)


class InvoiceQRCodeReuseError(ValueError):
    """Existing invoice QR cannot be reused safely."""


class PaymentService:
    def __init__(self, *, gateway: PaymentGateway, store: PaymentStore) -> None:
        self.gateway = gateway
        self.store = store

    async def create_dynamic_qr(
        self,
        payload: DynamicQRCreate,
        *,
        provider_name: str | None = None,
    ) -> DynamicQRResponse:
        provider = self.gateway.provider(provider_name)
        response = await provider.create_dynamic_qr(payload)
        self.store.upsert_transaction_payload(response, provider=provider.name)
        return response

    async def create_or_reuse_invoice_qr(
        self,
        payload: DynamicQRCreate,
        *,
        provider_name: str,
        invoice_id: str,
        print_qr_code: str,
    ) -> tuple[dict, bool]:
        provider = self.gateway.provider(provider_name)
        existing = self.store.find_invoice_transaction(
            external_invoice_id=invoice_id,
            provider=provider.name,
            print_qr_code=print_qr_code,
            statuses=REUSABLE_INVOICE_QR_STATUSES,
        )
        if existing is not None:
            existing_amount = existing.get("amount")
            if existing_amount != payload.amount:
                raise InvoiceQRCodeReuseError(
                    "Existing QR amount does not match current invoice amount. "
                    "Cancel or reset the old QR before printing a new invoice QR."
                )
            merged = self.store.merge_transaction_metadata(
                str(existing["id"]),
                payload.metadata or {},
            )
            return merged or existing, True

        response = await provider.create_dynamic_qr(payload)
        self.store.upsert_transaction_payload(response, provider=provider.name)
        saved = self.store.get_transaction(str(response.id))
        return saved or response.model_dump(mode="json", exclude_none=True), False

    async def create_static_qr(
        self,
        payload: StaticQRCreate,
        *,
        provider_name: str | None = None,
    ) -> StaticQRResponse:
        provider = self.gateway.provider(provider_name)
        response = await provider.create_static_qr(payload)
        self.store.upsert_transaction_payload(response, provider=provider.name)
        return response

    async def get_transaction(
        self,
        transaction_id: str,
        *,
        provider_name: str | None = None,
    ) -> Transaction:
        provider = self.gateway.provider(self._saved_provider_name(transaction_id) or provider_name)
        response = await provider.get_transaction(transaction_id)
        self.store.upsert_transaction_payload(
            response,
            provider=provider.name,
            transaction_id_override=transaction_id,
        )
        await self.cancel_other_invoice_transactions_if_paid(transaction_id)
        return response

    async def cancel_transaction(
        self,
        transaction_id: str,
        *,
        provider_name: str | None = None,
    ) -> CancelResponse:
        provider = self.gateway.provider(self._saved_provider_name(transaction_id) or provider_name)
        provider_transaction_id = self._provider_transaction_id(transaction_id)
        response = await provider.cancel_transaction(provider_transaction_id)
        self.store.update_transaction_status(
            transaction_id,
            status="canceled",
            provider=provider.name,
        )
        return CancelResponse(transaction_id=transaction_id, message=response.message)

    async def list_transactions(
        self,
        *,
        page: int | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        branch: int | None = None,
        cashier: int | None = None,
        provider_name: str | None = None,
    ) -> TransactionListResponse:
        return await self.gateway.provider(provider_name).list_transactions(
            page=page,
            status=status,
            transaction_type=transaction_type,
            start_date=start_date,
            end_date=end_date,
            branch=branch,
            cashier=cashier,
        )

    async def transaction_details(
        self,
        *,
        start_date: date,
        end_date: date,
        page: int | None = None,
        provider_name: str | None = None,
    ) -> TransactionDetailListResponse:
        return await self.gateway.provider(provider_name).transaction_details(
            start_date=start_date,
            end_date=end_date,
            page=page,
        )

    async def branches(
        self,
        *,
        page: int | None = None,
        provider_name: str | None = None,
    ) -> BranchListResponse:
        return await self.gateway.provider(provider_name).branches(page=page)

    async def save_webhook(
        self,
        payload: WebhookPayload,
        *,
        provider_name: str | None = None,
    ) -> WebhookStoreResult:
        provider = self.gateway.provider(provider_name)
        result = self.store.save_webhook(payload, provider=provider.name)
        await self.cancel_other_invoice_transactions_if_paid(result.transaction_id)
        return result

    async def cancel_other_invoice_transactions_if_paid(self, transaction_id: str) -> None:
        paid_transaction = self.store.get_transaction(transaction_id)
        if paid_transaction is None or paid_transaction.get("status") != PAID_STATUS:
            return

        external_invoice_id = paid_transaction.get("external_invoice_id")
        if not isinstance(external_invoice_id, str) or not external_invoice_id.strip():
            return

        self.store.upsert_one_c_payment_export(build_one_c_payment_event(paid_transaction))
        tiger_event = build_tiger_invoice_event(paid_transaction)
        tiger_validation_error = validate_tiger_invoice_event(tiger_event)
        self.store.upsert_tiger_invoice_export(
            tiger_event,
            status="error" if tiger_validation_error else "pending",
            error_message=tiger_validation_error,
        )

        related_transactions = self.store.list_invoice_transactions_for_cancel(
            external_invoice_id=external_invoice_id,
            exclude_transaction_id=transaction_id,
            statuses=AUTO_CANCEL_INVOICE_QR_STATUSES,
        )
        for item in related_transactions:
            related_transaction_id = str(item["id"])
            try:
                await self.cancel_transaction(related_transaction_id)
            except Exception:
                logger.exception(
                    "Failed to auto-cancel transaction %s after invoice %s was paid by %s",
                    related_transaction_id,
                    external_invoice_id,
                    transaction_id,
                )

    def _saved_provider_name(self, transaction_id: str) -> str | None:
        saved = self.store.get_transaction(transaction_id)
        if saved is None:
            return None
        provider = saved.get("provider")
        return provider if isinstance(provider, str) and provider.strip() else None

    def _provider_transaction_id(self, transaction_id: str) -> str:
        saved = self.store.get_transaction(transaction_id)
        if saved is None:
            return transaction_id
        raw_payload = saved.get("raw_payload")
        if not isinstance(raw_payload, dict):
            return transaction_id
        provider_transaction_id = raw_payload.get("provider_transaction_id") or raw_payload.get(
            "invoice_id"
        )
        if provider_transaction_id is None:
            return transaction_id
        return str(provider_transaction_id)


def build_tiger_invoice_event(transaction: dict) -> dict[str, object]:
    payment = _paid_payment_fields(transaction)
    metadata = payment["metadata"]
    provider = payment["paidProvider"]
    invoice_id = payment["invoiceId"]
    invoice_number = payment["invoiceNumber"]
    target_bank_code = (
        metadata.get("tiger_bank_code")
        or metadata.get("print_qr_code")
        or str(provider).upper()
    )

    event: dict[str, object] = {
        "invoiceId": invoice_id,
        "invoiceNumber": invoice_number,
        "paidTransactionId": payment["paymentId"],
        "paidProvider": provider,
        "providerPaymentId": payment["providerPaymentId"],
        "targetBankCode": str(target_bank_code).upper(),
        "targetBankAccountCode": metadata.get("tiger_bank_account_code"),
        "paidAt": payment["paidAt"],
        "amountTyiyn": payment["amountTyiyn"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "clientCode": payment["clientCode"],
        "clientName": payment["clientName"],
        "paymentMethod": payment["paymentMethod"],
        "description": f"QR payment for {invoice_number or invoice_id}",
    }
    return {key: value for key, value in event.items() if value is not None}


def build_one_c_payment_event(transaction: dict) -> dict[str, object]:
    payment = _paid_payment_fields(transaction)
    event = {
        key: value
        for key, value in payment.items()
        if key != "metadata" and value is not None
    }
    event["status"] = PAID_STATUS
    return event


def validate_tiger_invoice_event(event: dict[str, object]) -> str | None:
    required_fields = {
        "invoiceId": "invoiceId is required.",
        "paidTransactionId": "paidTransactionId is required.",
        "paidAt": "paidAt is required.",
        "amountTyiyn": "amountTyiyn is required.",
        "targetBankAccountCode": "targetBankAccountCode must contain the Tiger BANKACC.CODE.",
        "clientCode": "clientCode is required.",
    }
    for field, message in required_fields.items():
        value = event.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return message

    amount_tyiyn = event.get("amountTyiyn")
    if not isinstance(amount_tyiyn, int) or amount_tyiyn <= 0:
        return "amountTyiyn must be a positive integer."

    currency = event.get("currency")
    if not isinstance(currency, str) or currency.upper() != "KGS":
        return "Only KGS bank vouchers are currently supported."

    return None


def _paid_payment_fields(transaction: dict) -> dict[str, object]:
    metadata = transaction.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    invoice_id = transaction.get("external_invoice_id") or metadata.get("invoice_id")
    if not isinstance(invoice_id, str) or not invoice_id.strip():
        raise ValueError("Transaction does not have metadata.invoice_id")
    invoice_id = invoice_id.strip()

    raw_payload = transaction.get("raw_payload")
    if not isinstance(raw_payload, dict):
        raw_payload = {}

    provider = str(transaction.get("provider") or "unknown")
    provider_payment_id = (
        raw_payload.get("provider_transaction_id")
        or raw_payload.get("invoice_id")
        or raw_payload.get("id")
        or transaction.get("id")
    )
    amount_tyiyn = transaction.get("amount")
    amount = amount_tyiyn / 100 if isinstance(amount_tyiyn, int) else None
    invoice_number = metadata.get("invoice_number")
    payment_code = str(metadata.get("print_qr_code") or provider).strip().lower()

    return {
        "metadata": metadata,
        "paymentId": str(transaction["id"]),
        "invoiceId": invoice_id,
        "invoiceNumber": invoice_number,
        "paymentCode": payment_code,
        "paidProvider": provider,
        "providerPaymentId": str(provider_payment_id),
        "paidAt": _payment_paid_at(transaction, raw_payload),
        "amountTyiyn": amount_tyiyn,
        "amount": amount,
        "currency": metadata.get("currency") or raw_payload.get("currency") or "KGS",
        "clientCode": metadata.get("client_code") or metadata.get("payer_code"),
        "clientName": metadata.get("client_name") or metadata.get("payer_full_name"),
        "paymentMethod": transaction.get("transaction_type") or "qr",
    }


def _payment_paid_at(transaction: dict, raw_payload: dict) -> object | None:
    """Always give downstream exports a confirmation time without blocking webhooks."""
    for value in (transaction.get("paid_at"), raw_payload.get("paid_at")):
        normalized = _normalize_export_datetime(value)
        if normalized is not None:
            return normalized

    odengi_payload = raw_payload.get("odengi_payload")
    if isinstance(odengi_payload, dict):
        for value in (odengi_payload.get("date_pay"), odengi_payload.get("mktime")):
            normalized = _normalize_export_datetime(value)
            if normalized is not None:
                return normalized

    return _normalize_export_datetime(transaction.get("updated_at"))


def _normalize_export_datetime(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        timestamp = int(value)
        if timestamp >= 100_000_000_000:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for format_value in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%d.%m.%Y %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, format_value)
                break
            except ValueError:
                continue
        if parsed is None:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=6)))
    return parsed.isoformat()
