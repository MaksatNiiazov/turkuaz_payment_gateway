from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class WebhookStoreResult:
    transaction_id: str
    duplicate: bool


class SQLiteMKassaStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    status TEXT,
                    transaction_type TEXT,
                    amount INTEGER,
                    branch TEXT,
                    cashier TEXT,
                    created_at TEXT,
                    paid_at TEXT,
                    payment_token TEXT,
                    static_qr_link TEXT,
                    metadata TEXT,
                    raw_payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL,
                    status TEXT,
                    payload_hash TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_webhook_events_transaction_id
                    ON webhook_events(transaction_id);
                CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at
                    ON webhook_events(received_at);

                CREATE TABLE IF NOT EXISTS api_access_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    integration_name TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER,
                    user_agent TEXT,
                    remote_addr TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_api_access_events_integration_name
                    ON api_access_events(integration_name);
                CREATE INDEX IF NOT EXISTS idx_api_access_events_created_at
                    ON api_access_events(created_at);
                """
            )

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
    ) -> None:
        now = self._now()
        values = {
            "id": transaction_id,
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
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transactions (
                    id, status, transaction_type, amount, branch, cashier, created_at, paid_at,
                    payment_token, static_qr_link, metadata, raw_payload, updated_at
                )
                VALUES (
                    :id, :status, :transaction_type, :amount, :branch, :cashier, :created_at,
                    :paid_at, :payment_token, :static_qr_link, :metadata, :raw_payload, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    status = COALESCE(excluded.status, transactions.status),
                    transaction_type = COALESCE(excluded.transaction_type, transactions.transaction_type),
                    amount = COALESCE(excluded.amount, transactions.amount),
                    branch = COALESCE(excluded.branch, transactions.branch),
                    cashier = COALESCE(excluded.cashier, transactions.cashier),
                    created_at = COALESCE(excluded.created_at, transactions.created_at),
                    paid_at = COALESCE(excluded.paid_at, transactions.paid_at),
                    payment_token = COALESCE(excluded.payment_token, transactions.payment_token),
                    static_qr_link = COALESCE(excluded.static_qr_link, transactions.static_qr_link),
                    metadata = COALESCE(excluded.metadata, transactions.metadata),
                    raw_payload = excluded.raw_payload,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def save_webhook(self, payload: BaseModel | dict[str, Any]) -> WebhookStoreResult:
        data = self._model_to_dict(payload)
        transaction_id = str(data["id"])
        payload_json = self._json_dumps(data)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = self._now()

        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO webhook_events (transaction_id, status, payload_hash, payload, received_at)
                    VALUES (:transaction_id, :status, :payload_hash, :payload, :received_at)
                    """,
                    {
                        "transaction_id": transaction_id,
                        "status": data.get("status"),
                        "payload_hash": payload_hash,
                        "payload": payload_json,
                        "received_at": now,
                    },
                )
                duplicate = False
            except sqlite3.IntegrityError:
                duplicate = True

        self.upsert_transaction_payload(data)
        return WebhookStoreResult(transaction_id=transaction_id, duplicate=duplicate)

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
        return self._transaction_row_to_dict(row) if row else None

    def list_webhook_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, transaction_id, status, payload, received_at
                FROM webhook_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (capped_limit,),
            ).fetchall()
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
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_access_events (
                    integration_name, method, path, status_code, user_agent, remote_addr, created_at
                )
                VALUES (
                    :integration_name, :method, :path, :status_code, :user_agent,
                    :remote_addr, :created_at
                )
                """,
                {
                    "integration_name": integration_name,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "user_agent": user_agent,
                    "remote_addr": remote_addr,
                    "created_at": self._now(),
                },
            )

    def list_api_access_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id, integration_name, method, path, status_code, user_agent,
                    remote_addr, created_at
                FROM api_access_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (capped_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _model_to_dict(payload: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump(mode="json", exclude_none=True)
        return payload

    @staticmethod
    def _transaction_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = SQLiteMKassaStore._json_loads(data.get("metadata"))
        data["raw_payload"] = SQLiteMKassaStore._json_loads(data.get("raw_payload")) or {}
        return data

    @staticmethod
    def _webhook_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = SQLiteMKassaStore._json_loads(data.get("payload")) or {}
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
