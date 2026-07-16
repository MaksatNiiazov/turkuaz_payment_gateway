from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    desc,
    inspect,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError


@dataclass(frozen=True)
class WebhookStoreResult:
    transaction_id: str
    duplicate: bool


DEFAULT_PRINT_QR_CODES = [
    {
        "code": "mbank",
        "label": "MBank",
        "provider": "mkassa",
        "enabled": True,
        "slot": 1,
        "sort_order": 10,
        "tiger_bank_account_code": None,
    },
    {
        "code": "obank",
        "label": "О!Банк",
        "provider": "odengi",
        "enabled": True,
        "slot": 2,
        "sort_order": 20,
        "tiger_bank_account_code": None,
    },
    {
        "code": "qr_3",
        "label": "QR 3",
        "provider": "mkassa",
        "enabled": False,
        "slot": 3,
        "sort_order": 30,
        "tiger_bank_account_code": None,
    },
    {
        "code": "qr_4",
        "label": "QR 4",
        "provider": "odengi",
        "enabled": False,
        "slot": 4,
        "sort_order": 40,
        "tiger_bank_account_code": None,
    },
]
FIXED_PRINT_QR_CODE_CODES = tuple(item["code"] for item in DEFAULT_PRINT_QR_CODES)


metadata = MetaData()

transactions = Table(
    "transactions",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("provider", String(32), nullable=False, default="mkassa"),
    Column("status", String(64)),
    Column("transaction_type", String(64)),
    Column("amount", Integer),
    Column("branch", String(128)),
    Column("cashier", String(128)),
    Column("external_invoice_id", String(150)),
    Column("created_at", String(64)),
    Column("paid_at", String(64)),
    Column("payment_token", Text),
    Column("static_qr_link", Text),
    Column("metadata", Text),
    Column("raw_payload", Text, nullable=False),
    Column("updated_at", String(64), nullable=False),
)

webhook_events = Table(
    "webhook_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String(32), nullable=False, default="mkassa"),
    Column("transaction_id", String(128), nullable=False),
    Column("status", String(64)),
    Column("payload_hash", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("received_at", String(64), nullable=False),
    UniqueConstraint("provider", "payload_hash", name="uq_webhook_events_provider_payload_hash"),
)

api_access_events = Table(
    "api_access_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("integration_name", String(128), nullable=False),
    Column("method", String(16), nullable=False),
    Column("path", Text, nullable=False),
    Column("status_code", Integer),
    Column("user_agent", Text),
    Column("remote_addr", String(128)),
    Column("created_at", String(64), nullable=False),
)

print_qr_codes = Table(
    "print_qr_codes",
    metadata,
    Column("code", String(64), primary_key=True),
    Column("label", String(150), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("slot", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=100),
    Column("tiger_bank_account_code", String(64)),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

tiger_invoice_exports = Table(
    "tiger_invoice_exports",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("invoice_id", String(150), nullable=False),
    Column("invoice_number", String(150)),
    Column("paid_transaction_id", String(128), nullable=False),
    Column("paid_provider", String(32), nullable=False),
    Column("provider_payment_id", String(150)),
    Column("target_bank_code", String(64)),
    Column("target_bank_account_code", String(64)),
    Column("amount", Integer),
    Column("currency", String(16)),
    Column("status", String(32), nullable=False, default="pending"),
    Column("event_payload", Text, nullable=False),
    Column("tiger_logical_ref", String(128)),
    Column("tiger_fiche_no", String(128)),
    Column("error_message", Text),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("last_attempt_at", String(64)),
    Column("exported_at", String(64)),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
    UniqueConstraint("invoice_id", name="uq_tiger_invoice_exports_invoice_id"),
)

one_c_payment_exports = Table(
    "one_c_payment_exports",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("payment_id", String(128), nullable=False),
    Column("invoice_id", String(150), nullable=False),
    Column("invoice_number", String(150)),
    Column("payment_code", String(64)),
    Column("paid_provider", String(32), nullable=False),
    Column("provider_payment_id", String(150)),
    Column("amount", Integer),
    Column("currency", String(16)),
    Column("status", String(32), nullable=False, default="pending"),
    Column("event_payload", Text, nullable=False),
    Column("one_c_document_id", String(150)),
    Column("error_message", Text),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("last_attempt_at", String(64)),
    Column("exported_at", String(64)),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
    UniqueConstraint("payment_id", name="uq_one_c_payment_exports_payment_id"),
)

Index("idx_transactions_provider_status", transactions.c.provider, transactions.c.status)
Index("idx_transactions_external_invoice_id", transactions.c.external_invoice_id)
Index("idx_transactions_updated_at", transactions.c.updated_at)
Index(
    "uq_transactions_paid_invoice",
    transactions.c.external_invoice_id,
    unique=True,
    sqlite_where=and_(
        transactions.c.status == "paid",
        transactions.c.external_invoice_id.is_not(None),
        transactions.c.external_invoice_id != "",
    ),
    postgresql_where=and_(
        transactions.c.status == "paid",
        transactions.c.external_invoice_id.is_not(None),
        transactions.c.external_invoice_id != "",
    ),
)
Index("idx_webhook_events_transaction_id", webhook_events.c.transaction_id)
Index("idx_webhook_events_received_at", webhook_events.c.received_at)
Index("idx_api_access_events_integration_name", api_access_events.c.integration_name)
Index("idx_api_access_events_created_at", api_access_events.c.created_at)
Index("idx_print_qr_codes_enabled_sort", print_qr_codes.c.enabled, print_qr_codes.c.sort_order)
Index("idx_tiger_invoice_exports_status", tiger_invoice_exports.c.status)
Index(
    "idx_tiger_invoice_exports_status_created",
    tiger_invoice_exports.c.status,
    tiger_invoice_exports.c.created_at,
    tiger_invoice_exports.c.id,
)
Index("idx_tiger_invoice_exports_updated_at", tiger_invoice_exports.c.updated_at)
Index("idx_one_c_payment_exports_status", one_c_payment_exports.c.status)
Index(
    "idx_one_c_payment_exports_status_created",
    one_c_payment_exports.c.status,
    one_c_payment_exports.c.created_at,
    one_c_payment_exports.c.id,
)
Index("idx_one_c_payment_exports_updated_at", one_c_payment_exports.c.updated_at)


class PaymentStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = self._create_engine(database_url)

    def initialize(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            if not database_path.is_absolute():
                database_path = Path.cwd() / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._migrate_legacy_sqlite()

        metadata.create_all(self.engine)
        self.ensure_default_print_qr_codes()

    def close(self) -> None:
        self.engine.dispose()

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(select(1)).scalar_one()

    def upsert_transaction_payload(
        self,
        payload: BaseModel | dict[str, Any],
        *,
        provider: str = "mkassa",
        transaction_id_override: str | None = None,
    ) -> None:
        data = self._model_to_dict(payload)
        transaction_id = transaction_id_override or data.get("id") or data.get("transaction_id")
        if transaction_id is None:
            return

        self.upsert_transaction(
            transaction_id=str(transaction_id),
            status=data.get("status") or data.get("transaction_status"),
            transaction_type=data.get("transaction_type")
            or ("static" if data.get("static_qr_link") else None),
            amount=data.get("amount") if "amount" in data else data.get("transaction_sum"),
            branch=data.get("branch") if "branch" in data else data.get("branch_id"),
            cashier=data.get("cashier"),
            external_invoice_id=self._extract_external_invoice_id(data),
            created_at=data.get("created_at") or data.get("transaction_date"),
            paid_at=data.get("paid_at"),
            payment_token=data.get("payment_token"),
            static_qr_link=data.get("static_qr_link"),
            metadata=data.get("metadata"),
            raw_payload=data,
            provider=provider,
            update_metadata=False,
        )

    def upsert_transaction(
        self,
        *,
        transaction_id: str,
        status: str | None = None,
        transaction_type: str | None = None,
        amount: int | str | None = None,
        branch: int | str | None = None,
        cashier: int | str | None = None,
        external_invoice_id: str | None = None,
        created_at: str | None = None,
        paid_at: str | None = None,
        payment_token: str | None = None,
        static_qr_link: str | None = None,
        metadata: dict[str, Any] | None = None,
        raw_payload: dict[str, Any] | None = None,
        provider: str = "mkassa",
        update_metadata: bool = True,
    ) -> None:
        now = self._now()
        values = {
            "id": transaction_id,
            "provider": provider,
            "status": status,
            "transaction_type": transaction_type,
            "amount": self._parse_int(amount),
            "branch": None if branch is None else str(branch),
            "cashier": None if cashier is None else str(cashier),
            "external_invoice_id": self._clean_string(external_invoice_id)
            or self._extract_external_invoice_id({"metadata": metadata}),
            "created_at": self._serialize_value(created_at),
            "paid_at": self._serialize_value(paid_at),
            "payment_token": payment_token,
            "static_qr_link": static_qr_link,
            "metadata": self._json_dumps(metadata) if metadata is not None else None,
            "raw_payload": self._json_dumps(raw_payload or {}),
            "updated_at": now,
        }

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
            if existing is None:
                try:
                    with connection.begin_nested():
                        connection.execute(transactions.insert().values(**values))
                    return
                except IntegrityError:
                    existing = connection.execute(
                        select(transactions).where(transactions.c.id == transaction_id)
                    ).mappings().first()
                    if existing is None and self._is_paid_invoice_values(values):
                        values["status"] = "duplicate"
                        connection.execute(transactions.insert().values(**values))
                        return
                    if existing is None:
                        raise

            merged = {
                key: (value if value is not None else existing[key])
                for key, value in values.items()
                if key not in {"id", "raw_payload", "updated_at"}
            }
            if existing["external_invoice_id"]:
                merged["external_invoice_id"] = existing["external_invoice_id"]
            if metadata is not None and update_metadata:
                existing_metadata = self._json_loads(existing["metadata"])
                merged_metadata = existing_metadata if isinstance(existing_metadata, dict) else {}
                merged_metadata.update(metadata)
                merged["metadata"] = self._json_dumps(merged_metadata)
            elif not update_metadata:
                merged["metadata"] = existing["metadata"]
            existing_status = existing["status"]
            if existing_status in {"paid", "duplicate"} and merged.get("status") != existing_status:
                merged["status"] = existing_status
            merged["raw_payload"] = values["raw_payload"]
            merged["updated_at"] = values["updated_at"]
            self._update_transaction_with_paid_fallback(
                connection,
                transaction_id=transaction_id,
                values=merged,
            )

    def save_webhook(
        self,
        payload: BaseModel | dict[str, Any],
        *,
        provider: str = "mkassa",
    ) -> WebhookStoreResult:
        data = self._model_to_dict(payload)
        transaction_id = str(data["id"])
        payload_json = self._json_dumps(data)
        payload_hash = hashlib.sha256(
            f"{provider}:{payload_json}".encode("utf-8")
        ).hexdigest()
        now = self._now()

        with self.engine.begin() as connection:
            try:
                connection.execute(
                    webhook_events.insert().values(
                        provider=provider,
                        transaction_id=transaction_id,
                        status=data.get("status"),
                        payload_hash=payload_hash,
                        payload=payload_json,
                        received_at=now,
                    )
                )
                duplicate = False
            except IntegrityError:
                duplicate = True

        existing_transaction = self.get_transaction(transaction_id)
        if existing_transaction is not None and self._webhook_matches_existing_transaction(
            data,
            provider=provider,
            existing_transaction=existing_transaction,
        ):
            self._update_transaction_from_webhook(
                transaction_id=transaction_id,
                data=data,
            )
        return WebhookStoreResult(transaction_id=transaction_id, duplicate=duplicate)

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
        return self._transaction_row_to_dict(row) if row else None

    def _update_transaction_from_webhook(
        self,
        *,
        transaction_id: str,
        data: dict[str, Any],
    ) -> None:
        now = self._now()
        webhook_status = self._clean_string(data.get("status"))
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
            if existing is None or webhook_status is None:
                return

            # A provider callback is allowed to advance payment state, but it must
            # never rewrite invoice/client/bank metadata supplied by our caller.
            # Once a payment is accepted, delayed cancel/unknown callbacks cannot
            # downgrade it.
            if existing["status"] in {"paid", "duplicate"} and webhook_status != "paid":
                return

            values: dict[str, Any] = {
                "status": webhook_status,
                "external_invoice_id": existing["external_invoice_id"],
                "updated_at": now,
            }
            if existing["amount"] is None and data.get("amount") is not None:
                values["amount"] = self._parse_int(data.get("amount"))
            if existing["created_at"] is None and data.get("created_at") is not None:
                values["created_at"] = self._serialize_value(data.get("created_at"))
            if data.get("paid_at") is not None:
                values["paid_at"] = self._serialize_value(data.get("paid_at"))
            odengi_payload = data.get("odengi_payload")
            if isinstance(odengi_payload, dict) and odengi_payload.get("trans_id") is not None:
                existing_raw_payload = self._json_loads(existing["raw_payload"])
                raw_payload = (
                    dict(existing_raw_payload)
                    if isinstance(existing_raw_payload, dict)
                    else {}
                )
                raw_payload["provider_payment_id"] = str(odengi_payload["trans_id"])
                values["raw_payload"] = self._json_dumps(raw_payload)

            self._update_transaction_with_paid_fallback(
                connection,
                transaction_id=transaction_id,
                values=values,
            )

    def update_transaction_status(
        self,
        transaction_id: str,
        *,
        status: str,
        provider: str = "mkassa",
    ) -> None:
        now = self._now()
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
            if existing is None:
                connection.execute(
                    transactions.insert().values(
                        id=transaction_id,
                        provider=provider,
                        status=status,
                        raw_payload=self._json_dumps({"id": transaction_id, "status": status}),
                        updated_at=now,
                    )
                )
                return

            self._update_transaction_with_paid_fallback(
                connection,
                transaction_id=transaction_id,
                values={
                    "status": status,
                    "external_invoice_id": existing["external_invoice_id"],
                    "updated_at": now,
                },
            )

    def list_transactions(
        self,
        *,
        limit: int = 50,
        provider: str | None = None,
        status: str | None = None,
        external_invoice_id: str | None = None,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        query = select(transactions).order_by(desc(transactions.c.updated_at)).limit(capped_limit)
        if provider:
            query = query.where(transactions.c.provider == provider)
        if status:
            query = query.where(transactions.c.status == status)
        if external_invoice_id:
            query = query.where(transactions.c.external_invoice_id == external_invoice_id)

        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._transaction_row_to_dict(row) for row in rows]

    def find_invoice_transaction(
        self,
        *,
        external_invoice_id: str,
        provider: str,
        print_qr_code: str,
        statuses: set[str],
    ) -> dict[str, Any] | None:
        query = (
            select(transactions)
            .where(transactions.c.external_invoice_id == external_invoice_id)
            .where(transactions.c.provider == provider)
            .order_by(desc(transactions.c.updated_at))
            .limit(100)
        )
        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            for row in rows:
                item = self._transaction_row_to_dict(row)
                if item.get("status") not in statuses:
                    continue
                item_metadata = item.get("metadata")
                if not isinstance(item_metadata, dict):
                    continue
                if item_metadata.get("print_qr_code") == print_qr_code:
                    return item
        return None

    def merge_transaction_metadata(
        self,
        transaction_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not metadata:
            return self.get_transaction(transaction_id)

        now = self._now()
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
            if existing is None:
                return None

            existing_metadata = self._json_loads(existing["metadata"])
            merged_metadata = existing_metadata if isinstance(existing_metadata, dict) else {}
            merged_metadata.update(metadata)

            existing_raw_payload = self._json_loads(existing["raw_payload"]) or {}
            if isinstance(existing_raw_payload, dict):
                raw_metadata = existing_raw_payload.get("metadata")
                merged_raw_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                merged_raw_metadata.update(metadata)
                existing_raw_payload["metadata"] = merged_raw_metadata

            connection.execute(
                transactions.update()
                .where(transactions.c.id == transaction_id)
                .values(
                    metadata=self._json_dumps(merged_metadata),
                    raw_payload=self._json_dumps(existing_raw_payload),
                    updated_at=now,
                )
            )
            row = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().one()
            return self._transaction_row_to_dict(row)

    def list_invoice_transactions_for_cancel(
        self,
        *,
        external_invoice_id: str,
        exclude_transaction_id: str,
        statuses: set[str],
    ) -> list[dict[str, Any]]:
        query = (
            select(transactions)
            .where(transactions.c.external_invoice_id == external_invoice_id)
            .where(transactions.c.id != exclude_transaction_id)
            .where(transactions.c.status.in_(statuses))
            .order_by(desc(transactions.c.updated_at))
            .limit(100)
        )
        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._transaction_row_to_dict(row) for row in rows]

    def list_print_qr_codes(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = (
            select(print_qr_codes)
            .where(print_qr_codes.c.code.in_(FIXED_PRINT_QR_CODE_CODES))
            .order_by(print_qr_codes.c.sort_order, print_qr_codes.c.code)
        )
        if enabled_only:
            query = query.where(print_qr_codes.c.enabled.is_(True))
        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._print_qr_code_row_to_dict(row) for row in rows]

    def replace_print_qr_codes(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = self._now()
        with self.engine.begin() as connection:
            connection.execute(print_qr_codes.delete())
            for item in items:
                connection.execute(
                    print_qr_codes.insert().values(
                        code=item["code"],
                        label=item["label"],
                        provider=item["provider"],
                        enabled=bool(item["enabled"]),
                        slot=int(item["slot"]),
                        sort_order=int(item["sort_order"]),
                        tiger_bank_account_code=self._clean_string(
                            item.get("tiger_bank_account_code")
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                )
        return self.list_print_qr_codes()

    def upsert_tiger_invoice_export(
        self,
        event: dict[str, Any],
        *,
        status: str = "pending",
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"pending", "error"}:
            raise ValueError("Unsupported initial Tiger export status")
        invoice_id = self._clean_string(event.get("invoiceId"))
        paid_transaction_id = self._clean_string(event.get("paidTransactionId"))
        paid_provider = self._clean_string(event.get("paidProvider"))
        if not invoice_id:
            raise ValueError("invoiceId is required")
        if not paid_transaction_id:
            raise ValueError("paidTransactionId is required")
        if not paid_provider:
            raise ValueError("paidProvider is required")

        now = self._now()
        values = {
            "invoice_id": invoice_id,
            "invoice_number": self._clean_string(event.get("invoiceNumber")),
            "paid_transaction_id": paid_transaction_id,
            "paid_provider": paid_provider,
            "provider_payment_id": self._clean_string(event.get("providerPaymentId")),
            "target_bank_code": self._clean_string(event.get("targetBankCode")),
            "target_bank_account_code": self._clean_string(event.get("targetBankAccountCode")),
            "amount": self._parse_int(event.get("amountTyiyn")),
            "currency": self._clean_string(event.get("currency")),
            "event_payload": self._json_dumps(event),
            "error_message": self._clean_string(error_message) if status == "error" else None,
            "updated_at": now,
        }
        insert_values = {
            **values,
            "status": status,
            "attempt_count": 0,
            "last_attempt_at": now if status == "error" else None,
            "created_at": now,
        }

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(tiger_invoice_exports).where(
                    tiger_invoice_exports.c.invoice_id == invoice_id
                )
            ).mappings().first()
            if existing is None:
                try:
                    result = connection.execute(
                        tiger_invoice_exports.insert().values(**insert_values)
                    )
                    event_id = result.inserted_primary_key[0]
                except IntegrityError:
                    existing = connection.execute(
                        select(tiger_invoice_exports).where(
                            tiger_invoice_exports.c.invoice_id == invoice_id
                        )
                    ).mappings().first()
                else:
                    row = connection.execute(
                        select(tiger_invoice_exports).where(tiger_invoice_exports.c.id == event_id)
                    ).mappings().one()
                    return self._tiger_invoice_export_row_to_dict(row)

            if existing is None:
                raise RuntimeError("Failed to create or load Tiger invoice export")

            if existing["status"] == "success":
                return self._tiger_invoice_export_row_to_dict(existing)
            if existing["paid_transaction_id"] != paid_transaction_id:
                return self._tiger_invoice_export_row_to_dict(existing)

            existing_status = existing["status"]
            update_values = {
                key: value
                for key, value in values.items()
                if key != "error_message"
            }
            if existing_status == "pending" and status == "error":
                update_values["status"] = "error"
                update_values["error_message"] = self._clean_string(error_message)
                update_values["last_attempt_at"] = now
            elif existing_status == "error" and status == "error":
                update_values["error_message"] = self._clean_string(error_message)

            connection.execute(
                tiger_invoice_exports.update()
                .where(tiger_invoice_exports.c.id == existing["id"])
                .values(**update_values)
            )
            row = connection.execute(
                select(tiger_invoice_exports).where(tiger_invoice_exports.c.id == existing["id"])
            ).mappings().one()
            return self._tiger_invoice_export_row_to_dict(row)

    def list_tiger_invoice_exports(
        self,
        *,
        limit: int = 20,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        query = select(tiger_invoice_exports)
        if statuses:
            query = query.where(tiger_invoice_exports.c.status.in_(statuses))
        query = query.order_by(tiger_invoice_exports.c.created_at, tiger_invoice_exports.c.id).limit(
            capped_limit
        )

        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._tiger_invoice_export_row_to_dict(row) for row in rows]

    def claim_tiger_invoice_exports(
        self,
        *,
        limit: int = 20,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 100)
        now = self._now()
        stale_before = (datetime.now(UTC) - timedelta(seconds=lease_seconds)).isoformat()
        claimed: list[dict[str, Any]] = []

        with self.engine.begin() as connection:
            connection.execute(
                tiger_invoice_exports.update()
                .where(tiger_invoice_exports.c.status == "processing")
                .where(
                    or_(
                        tiger_invoice_exports.c.last_attempt_at.is_(None),
                        tiger_invoice_exports.c.last_attempt_at < stale_before,
                    )
                )
                .values(status="pending", updated_at=now)
            )
            candidate_ids = (
                select(tiger_invoice_exports.c.id)
                .where(tiger_invoice_exports.c.status == "pending")
                .order_by(tiger_invoice_exports.c.created_at, tiger_invoice_exports.c.id)
                .limit(capped_limit)
            )
            rows = connection.execute(
                tiger_invoice_exports.update()
                .where(tiger_invoice_exports.c.id.in_(candidate_ids))
                .where(tiger_invoice_exports.c.status == "pending")
                .values(status="processing", last_attempt_at=now, updated_at=now)
                .returning(tiger_invoice_exports)
            ).mappings().all()
            rows.sort(key=lambda row: (row["created_at"], row["id"]))
            claimed.extend(self._tiger_invoice_export_row_to_dict(row) for row in rows)
        return claimed

    def get_tiger_invoice_export(self, event_id: int) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(tiger_invoice_exports).where(tiger_invoice_exports.c.id == event_id)
            ).mappings().first()
        return self._tiger_invoice_export_row_to_dict(row) if row else None

    def update_tiger_invoice_export_result(
        self,
        event_id: int,
        *,
        status: str,
        tiger_logical_ref: str | None = None,
        tiger_fiche_no: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"processing", "success", "error", "skipped"}:
            raise ValueError("Unsupported Tiger export status")

        now = self._now()
        values: dict[str, Any] = {
            "status": status,
            "updated_at": now,
            "last_attempt_at": now,
        }
        if status in {"success", "error"}:
            values["attempt_count"] = tiger_invoice_exports.c.attempt_count + 1
        if status == "success":
            values["exported_at"] = now
            values["error_message"] = None
        if tiger_logical_ref is not None:
            values["tiger_logical_ref"] = self._clean_string(tiger_logical_ref)
        if tiger_fiche_no is not None:
            values["tiger_fiche_no"] = self._clean_string(tiger_fiche_no)
        if error_message is not None or status == "error":
            values["error_message"] = self._clean_string(error_message)

        with self.engine.begin() as connection:
            result = connection.execute(
                tiger_invoice_exports.update()
                .where(tiger_invoice_exports.c.id == event_id)
                .where(tiger_invoice_exports.c.status.in_({"pending", "processing"}))
                .values(**values)
            )
            if result.rowcount == 0:
                return None
            row = connection.execute(
                select(tiger_invoice_exports).where(tiger_invoice_exports.c.id == event_id)
            ).mappings().one()
            return self._tiger_invoice_export_row_to_dict(row)

    def reset_tiger_invoice_export(self, event_id: int) -> dict[str, Any] | None:
        now = self._now()
        with self.engine.begin() as connection:
            result = connection.execute(
                tiger_invoice_exports.update()
                .where(tiger_invoice_exports.c.id == event_id)
                .where(tiger_invoice_exports.c.status.in_({"error", "skipped"}))
                .values(
                    status="pending",
                    tiger_logical_ref=None,
                    tiger_fiche_no=None,
                    error_message=None,
                    exported_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount == 0:
                return None
            row = connection.execute(
                select(tiger_invoice_exports).where(tiger_invoice_exports.c.id == event_id)
            ).mappings().one()
            return self._tiger_invoice_export_row_to_dict(row)

    def upsert_one_c_payment_export(self, event: dict[str, Any]) -> dict[str, Any]:
        payment_id = self._clean_string(event.get("paymentId"))
        invoice_id = self._clean_string(event.get("invoiceId"))
        paid_provider = self._clean_string(event.get("paidProvider"))
        if not payment_id:
            raise ValueError("paymentId is required")
        if not invoice_id:
            raise ValueError("invoiceId is required")
        if not paid_provider:
            raise ValueError("paidProvider is required")

        now = self._now()
        values = {
            "invoice_id": invoice_id,
            "invoice_number": self._clean_string(event.get("invoiceNumber")),
            "payment_code": self._clean_string(event.get("paymentCode")),
            "paid_provider": paid_provider,
            "provider_payment_id": self._clean_string(event.get("providerPaymentId")),
            "amount": self._parse_int(event.get("amountTyiyn")),
            "currency": self._clean_string(event.get("currency")),
            "event_payload": self._json_dumps(event),
            "updated_at": now,
        }
        insert_values = {
            **values,
            "payment_id": payment_id,
            "status": "pending",
            "attempt_count": 0,
            "created_at": now,
        }

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(one_c_payment_exports).where(
                    one_c_payment_exports.c.payment_id == payment_id
                )
            ).mappings().first()
            if existing is None:
                try:
                    result = connection.execute(
                        one_c_payment_exports.insert().values(**insert_values)
                    )
                    event_id = result.inserted_primary_key[0]
                except IntegrityError:
                    existing = connection.execute(
                        select(one_c_payment_exports).where(
                            one_c_payment_exports.c.payment_id == payment_id
                        )
                    ).mappings().first()
                else:
                    row = connection.execute(
                        select(one_c_payment_exports).where(
                            one_c_payment_exports.c.id == event_id
                        )
                    ).mappings().one()
                    return self._one_c_payment_export_row_to_dict(row)

            if existing is None:
                raise RuntimeError("Failed to create or load 1C payment export")

            if existing["status"] == "success":
                return self._one_c_payment_export_row_to_dict(existing)

            connection.execute(
                one_c_payment_exports.update()
                .where(one_c_payment_exports.c.id == existing["id"])
                .values(**values)
            )
            row = connection.execute(
                select(one_c_payment_exports).where(
                    one_c_payment_exports.c.id == existing["id"]
                )
            ).mappings().one()
            return self._one_c_payment_export_row_to_dict(row)

    def list_one_c_payment_exports(
        self,
        *,
        limit: int = 20,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        query = select(one_c_payment_exports)
        if statuses:
            query = query.where(one_c_payment_exports.c.status.in_(statuses))
        query = query.order_by(one_c_payment_exports.c.created_at, one_c_payment_exports.c.id).limit(
            capped_limit
        )

        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._one_c_payment_export_row_to_dict(row) for row in rows]

    def get_one_c_payment_export(self, event_id: int) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(one_c_payment_exports).where(one_c_payment_exports.c.id == event_id)
            ).mappings().first()
        return self._one_c_payment_export_row_to_dict(row) if row else None

    def claim_one_c_payment_exports(
        self,
        *,
        limit: int = 20,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 100)
        now = self._now()
        stale_before = (datetime.now(UTC) - timedelta(seconds=lease_seconds)).isoformat()
        claimed: list[dict[str, Any]] = []

        with self.engine.begin() as connection:
            connection.execute(
                one_c_payment_exports.update()
                .where(one_c_payment_exports.c.status == "processing")
                .where(
                    or_(
                        one_c_payment_exports.c.last_attempt_at.is_(None),
                        one_c_payment_exports.c.last_attempt_at < stale_before,
                    )
                )
                .values(status="pending", updated_at=now)
            )
            candidate_ids = (
                select(one_c_payment_exports.c.id)
                .where(one_c_payment_exports.c.status == "pending")
                .order_by(one_c_payment_exports.c.created_at, one_c_payment_exports.c.id)
                .limit(capped_limit)
            )
            rows = connection.execute(
                one_c_payment_exports.update()
                .where(one_c_payment_exports.c.id.in_(candidate_ids))
                .where(one_c_payment_exports.c.status == "pending")
                .values(status="processing", last_attempt_at=now, updated_at=now)
                .returning(one_c_payment_exports)
            ).mappings().all()
            rows.sort(key=lambda row: (row["created_at"], row["id"]))
            claimed.extend(self._one_c_payment_export_row_to_dict(row) for row in rows)
        return claimed

    def update_one_c_payment_export_result(
        self,
        event_id: int,
        *,
        status: str,
        one_c_document_id: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"success", "error"}:
            raise ValueError("Unsupported 1C export status")

        now = self._now()
        values: dict[str, Any] = {
            "status": status,
            "updated_at": now,
            "last_attempt_at": now,
            "attempt_count": one_c_payment_exports.c.attempt_count + 1,
        }
        if status == "success":
            values["exported_at"] = now
            values["error_message"] = None
        if one_c_document_id is not None:
            values["one_c_document_id"] = self._clean_string(one_c_document_id)
        if error_message is not None or status == "error":
            values["error_message"] = self._clean_string(error_message)

        with self.engine.begin() as connection:
            result = connection.execute(
                one_c_payment_exports.update()
                .where(one_c_payment_exports.c.id == event_id)
                .where(one_c_payment_exports.c.status.in_({"pending", "processing"}))
                .values(**values)
            )
            if result.rowcount == 0:
                return None
            row = connection.execute(
                select(one_c_payment_exports).where(one_c_payment_exports.c.id == event_id)
            ).mappings().one()
            return self._one_c_payment_export_row_to_dict(row)

    def reset_one_c_payment_export(self, event_id: int) -> dict[str, Any] | None:
        now = self._now()
        with self.engine.begin() as connection:
            result = connection.execute(
                one_c_payment_exports.update()
                .where(one_c_payment_exports.c.id == event_id)
                .where(one_c_payment_exports.c.status.in_({"error", "skipped"}))
                .values(
                    status="pending",
                    one_c_document_id=None,
                    error_message=None,
                    exported_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount == 0:
                return None
            row = connection.execute(
                select(one_c_payment_exports).where(one_c_payment_exports.c.id == event_id)
            ).mappings().one()
            return self._one_c_payment_export_row_to_dict(row)

    def ensure_default_print_qr_codes(self) -> None:
        with self.engine.begin() as connection:
            existing_codes = {
                row[0] for row in connection.execute(select(print_qr_codes.c.code)).fetchall()
            }
            missing_items = [
                item for item in DEFAULT_PRINT_QR_CODES if item["code"] not in existing_codes
            ]
            if not missing_items:
                return
            now = self._now()
            connection.execute(
                print_qr_codes.insert(),
                [
                    {
                        **item,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for item in missing_items
                ],
            )

    def list_webhook_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(
                    webhook_events.c.id,
                    webhook_events.c.provider,
                    webhook_events.c.transaction_id,
                    webhook_events.c.status,
                    webhook_events.c.payload,
                    webhook_events.c.received_at,
                )
                .order_by(desc(webhook_events.c.id))
                .limit(capped_limit)
            ).mappings()
            return [self._webhook_row_to_dict(row) for row in rows]

    def save_api_access(
        self,
        *,
        integration_name: str,
        method: str,
        path: str,
        status_code: int | None,
        user_agent: str | None,
        remote_addr: str | None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                api_access_events.insert().values(
                    integration_name=integration_name,
                    method=method,
                    path=path,
                    status_code=status_code,
                    user_agent=user_agent,
                    remote_addr=remote_addr,
                    created_at=self._now(),
                )
            )

    def list_api_access_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(
                    api_access_events.c.id,
                    api_access_events.c.integration_name,
                    api_access_events.c.method,
                    api_access_events.c.path,
                    api_access_events.c.status_code,
                    api_access_events.c.user_agent,
                    api_access_events.c.remote_addr,
                    api_access_events.c.created_at,
                )
                .order_by(desc(api_access_events.c.id))
                .limit(capped_limit)
            ).mappings()
            return [dict(row) for row in rows]

    def _migrate_legacy_sqlite(self) -> None:
        if not self.database_url.startswith("sqlite:///"):
            return

        with self.engine.begin() as connection:
            transaction_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(transactions)").fetchall()
            }
            if transaction_columns and "provider" not in transaction_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE transactions ADD COLUMN provider TEXT NOT NULL DEFAULT 'mkassa'"
                )
            if transaction_columns and "external_invoice_id" not in transaction_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE transactions ADD COLUMN external_invoice_id TEXT"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_transactions_external_invoice_id "
                    "ON transactions (external_invoice_id)"
                )
            if transaction_columns:
                self._deduplicate_paid_invoice_transactions(connection)
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_paid_invoice "
                    "ON transactions (external_invoice_id) "
                    "WHERE status = 'paid' "
                    "AND external_invoice_id IS NOT NULL "
                    "AND external_invoice_id <> ''"
                )

            webhook_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(webhook_events)").fetchall()
            }
            if webhook_columns and "provider" not in webhook_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE webhook_events ADD COLUMN provider TEXT NOT NULL DEFAULT 'mkassa'"
                )

            access_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(api_access_events)"
                ).fetchall()
            }
            if "client_id" in access_columns and "integration_name" not in access_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE api_access_events RENAME COLUMN client_id TO integration_name"
                )

            print_qr_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(print_qr_codes)").fetchall()
            }
            if print_qr_columns:
                if "slot" not in print_qr_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE print_qr_codes ADD COLUMN slot INTEGER NOT NULL DEFAULT 1"
                    )
                    connection.exec_driver_sql(
                        "UPDATE print_qr_codes SET slot = 1 WHERE code = 'mbank'"
                    )
                    connection.exec_driver_sql(
                        "UPDATE print_qr_codes SET slot = 2 WHERE code = 'obank'"
                    )
                if "tiger_bank_account_code" not in print_qr_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE print_qr_codes "
                        "ADD COLUMN tiger_bank_account_code VARCHAR(64)"
                    )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_print_qr_codes_enabled_sort "
                    "ON print_qr_codes (enabled, sort_order)"
                )

            tiger_export_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(tiger_invoice_exports)"
                ).fetchall()
            }
            if tiger_export_columns:
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tiger_invoice_exports_invoice_id "
                    "ON tiger_invoice_exports (invoice_id)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_tiger_invoice_exports_status "
                    "ON tiger_invoice_exports (status)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_tiger_invoice_exports_status_created "
                    "ON tiger_invoice_exports (status, created_at, id)"
                )

            one_c_export_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(one_c_payment_exports)"
                ).fetchall()
            }
            if one_c_export_columns:
                if "payment_code" not in one_c_export_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE one_c_payment_exports ADD COLUMN payment_code TEXT"
                    )
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_c_payment_exports_payment_id "
                    "ON one_c_payment_exports (payment_id)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_one_c_payment_exports_status "
                    "ON one_c_payment_exports (status)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_one_c_payment_exports_status_created "
                    "ON one_c_payment_exports (status, created_at, id)"
                )

    @staticmethod
    def _deduplicate_paid_invoice_transactions(connection: Any) -> None:
        paid_rows = connection.execute(
            select(
                transactions.c.id,
                transactions.c.external_invoice_id,
                transactions.c.paid_at,
                transactions.c.created_at,
                transactions.c.updated_at,
            )
            .where(transactions.c.status == "paid")
            .where(transactions.c.external_invoice_id.is_not(None))
            .where(transactions.c.external_invoice_id != "")
        ).mappings().all()
        paid_by_invoice: dict[str, list[Any]] = {}
        for row in paid_rows:
            paid_by_invoice.setdefault(str(row["external_invoice_id"]), []).append(row)

        inspector = inspect(connection)
        tiger_winners: dict[str, str] = {}
        if inspector.has_table("tiger_invoice_exports"):
            rows = connection.execute(
                select(
                    tiger_invoice_exports.c.invoice_id,
                    tiger_invoice_exports.c.paid_transaction_id,
                )
            ).mappings()
            tiger_winners = {
                str(row["invoice_id"]): str(row["paid_transaction_id"])
                for row in rows
            }

        one_c_candidates: dict[str, list[tuple[int, str]]] = {}
        if inspector.has_table("one_c_payment_exports"):
            rows = connection.execute(
                select(
                    one_c_payment_exports.c.invoice_id,
                    one_c_payment_exports.c.payment_id,
                    one_c_payment_exports.c.status,
                )
            ).mappings()
            status_priority = {"success": 0, "processing": 1, "pending": 1, "error": 2}
            for row in rows:
                invoice_id = str(row["invoice_id"])
                payment_id = str(row["payment_id"])
                priority = status_priority.get(str(row["status"]), 3)
                one_c_candidates.setdefault(invoice_id, []).append((priority, payment_id))

        for invoice_id, invoice_rows in paid_by_invoice.items():
            if len(invoice_rows) < 2:
                continue
            paid_ids = {str(row["id"]) for row in invoice_rows}
            winner_id = tiger_winners.get(invoice_id)
            if winner_id not in paid_ids:
                winner_id = None
            if winner_id is None:
                for _, payment_id in sorted(one_c_candidates.get(invoice_id, [])):
                    if payment_id in paid_ids:
                        winner_id = payment_id
                        break
            if winner_id is None:
                winner_id = str(
                    min(
                        invoice_rows,
                        key=lambda row: (
                            row["paid_at"] or row["created_at"] or row["updated_at"] or "",
                            row["id"],
                        ),
                    )["id"]
                )

            duplicate_ids = paid_ids - {winner_id}
            if duplicate_ids:
                connection.execute(
                    transactions.update()
                    .where(transactions.c.id.in_(duplicate_ids))
                    .where(transactions.c.status == "paid")
                    .values(status="duplicate")
                )

    @staticmethod
    def _create_engine(database_url: str) -> Engine:
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:///"):
            connect_args["check_same_thread"] = False
            connect_args["timeout"] = 30
        return create_engine(database_url, pool_pre_ping=True, future=True, connect_args=connect_args)

    @staticmethod
    def _model_to_dict(payload: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump(mode="json", exclude_none=True)
        return payload

    @staticmethod
    def _transaction_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = PaymentStore._json_loads(data.get("metadata"))
        data["raw_payload"] = PaymentStore._json_loads(data.get("raw_payload")) or {}
        return data

    @staticmethod
    def _webhook_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = PaymentStore._json_loads(data.get("payload")) or {}
        return data

    @staticmethod
    def _print_qr_code_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        data.pop("created_at", None)
        data.pop("updated_at", None)
        return data

    @staticmethod
    def _tiger_invoice_export_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        data["event_payload"] = PaymentStore._json_loads(data.get("event_payload")) or {}
        return data

    @staticmethod
    def _one_c_payment_export_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        event_payload = PaymentStore._json_loads(data.get("event_payload")) or {}
        payment_code = data.get("payment_code") or PaymentStore._legacy_payment_code(
            data.get("paid_provider")
        )
        data["payment_code"] = payment_code
        if payment_code and "paymentCode" not in event_payload:
            event_payload["paymentCode"] = payment_code
        data["event_payload"] = event_payload
        return data

    @staticmethod
    def _legacy_payment_code(provider: Any) -> str | None:
        normalized = PaymentStore._clean_string(provider)
        if normalized == "mkassa":
            return "mbank"
        if normalized == "odengi":
            return "obank"
        return normalized

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _json_loads(value: str | None) -> Any:
        if not value:
            return None
        return json.loads(value)

    @staticmethod
    def _parse_int(value: int | str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_paid_invoice_values(values: dict[str, Any]) -> bool:
        return values.get("status") == "paid" and bool(values.get("external_invoice_id"))

    def _update_transaction_with_paid_fallback(
        self,
        connection: Any,
        *,
        transaction_id: str,
        values: dict[str, Any],
    ) -> None:
        statement = (
            transactions.update()
            .where(transactions.c.id == transaction_id)
            .values(**values)
        )
        try:
            with connection.begin_nested():
                connection.execute(statement)
        except IntegrityError:
            if not self._is_paid_invoice_values(values):
                raise
            fallback_values = {**values, "status": "duplicate"}
            connection.execute(
                transactions.update()
                .where(transactions.c.id == transaction_id)
                .values(**fallback_values)
            )

    @staticmethod
    def _webhook_matches_existing_transaction(
        data: dict[str, Any],
        *,
        provider: str,
        existing_transaction: dict[str, Any],
    ) -> bool:
        existing_provider = PaymentStore._clean_string(existing_transaction.get("provider"))
        if existing_provider and existing_provider != provider:
            return False

        existing_amount = PaymentStore._parse_int(existing_transaction.get("amount"))
        webhook_amount = PaymentStore._parse_int(data.get("amount"))
        webhook_status = PaymentStore._clean_string(data.get("status"))

        if existing_amount is not None:
            return webhook_amount is not None and existing_amount == webhook_amount
        if webhook_status == "paid":
            return webhook_amount is not None
        return True

    @staticmethod
    def _extract_external_invoice_id(data: dict[str, Any]) -> str | None:
        metadata_value = data.get("metadata")
        if not isinstance(metadata_value, dict):
            return None
        for key in ("invoice_id", "external_invoice_id", "onec_invoice_id", "invoice_uid"):
            value = PaymentStore._clean_string(metadata_value.get(key))
            if value:
                return value
        return None

    @staticmethod
    def _clean_string(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _serialize_value(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


class SQLitePaymentStore(PaymentStore):
    def __init__(self, database_path: Path) -> None:
        super().__init__(f"sqlite:///{database_path}")
