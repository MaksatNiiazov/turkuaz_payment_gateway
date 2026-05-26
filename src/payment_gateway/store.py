from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    desc,
    select,
)
from sqlalchemy.exc import IntegrityError


@dataclass(frozen=True)
class WebhookStoreResult:
    transaction_id: str
    duplicate: bool


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

Index("idx_transactions_provider_status", transactions.c.provider, transactions.c.status)
Index("idx_transactions_updated_at", transactions.c.updated_at)
Index("idx_webhook_events_transaction_id", webhook_events.c.transaction_id)
Index("idx_webhook_events_received_at", webhook_events.c.received_at)
Index("idx_api_access_events_integration_name", api_access_events.c.integration_name)
Index("idx_api_access_events_created_at", api_access_events.c.created_at)


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

    def close(self) -> None:
        self.engine.dispose()

    def upsert_transaction_payload(self, payload: BaseModel | dict[str, Any]) -> None:
        data = self._model_to_dict(payload)
        transaction_id = data.get("id") or data.get("transaction_id")
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
            created_at=data.get("created_at") or data.get("transaction_date"),
            paid_at=data.get("paid_at"),
            payment_token=data.get("payment_token"),
            static_qr_link=data.get("static_qr_link"),
            metadata=data.get("metadata"),
            raw_payload=data,
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
        created_at: str | None = None,
        paid_at: str | None = None,
        payment_token: str | None = None,
        static_qr_link: str | None = None,
        metadata: dict[str, Any] | None = None,
        raw_payload: dict[str, Any] | None = None,
        provider: str = "mkassa",
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
            "created_at": self._serialize_value(created_at),
            "paid_at": self._serialize_value(paid_at),
            "payment_token": payment_token,
            "static_qr_link": static_qr_link,
            "metadata": self._json_dumps(metadata),
            "raw_payload": self._json_dumps(raw_payload or {}),
            "updated_at": now,
        }

        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
            if existing is None:
                try:
                    connection.execute(transactions.insert().values(**values))
                    return
                except IntegrityError:
                    existing = connection.execute(
                        select(transactions).where(transactions.c.id == transaction_id)
                    ).mappings().first()

            merged = {
                key: (value if value is not None else existing[key])
                for key, value in values.items()
                if key not in {"id", "raw_payload", "updated_at"}
            }
            merged["raw_payload"] = values["raw_payload"]
            merged["updated_at"] = values["updated_at"]
            connection.execute(
                transactions.update().where(transactions.c.id == transaction_id).values(**merged)
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

        self.upsert_transaction_payload(data)
        return WebhookStoreResult(transaction_id=transaction_id, duplicate=duplicate)

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(transactions).where(transactions.c.id == transaction_id)
            ).mappings().first()
        return self._transaction_row_to_dict(row) if row else None

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

            connection.execute(
                transactions.update()
                .where(transactions.c.id == transaction_id)
                .values(status=status, updated_at=now)
            )

    def list_transactions(
        self,
        *,
        limit: int = 50,
        provider: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        query = select(transactions).order_by(desc(transactions.c.updated_at)).limit(capped_limit)
        if provider:
            query = query.where(transactions.c.provider == provider)
        if status:
            query = query.where(transactions.c.status == status)

        with self.engine.begin() as connection:
            rows = connection.execute(query).mappings()
            return [self._transaction_row_to_dict(row) for row in rows]

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

    @staticmethod
    def _create_engine(database_url: str) -> Engine:
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:///"):
            connect_args["check_same_thread"] = False
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
