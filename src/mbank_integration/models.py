from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_METADATA_KEYS = 5
MAX_METADATA_VALUE_LENGTH = 150


class TransactionStatus(StrEnum):
    INITED = "inited"
    PAID = "paid"
    FAILED = "failed"
    OVERDUE = "overdue"
    WAITING = "waiting"
    UNKNOWN = "unknown"
    CANCELED = "canceled"
    QR_SCANNED = "qr_scanned"
    FOR_ROLLBACK = "for_rollback"
    ROLLBACK = "rollback"


class TransactionType(StrEnum):
    QR = "qr"
    STATIC = "static"
    CARD = "card"


class APIModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


def validate_metadata(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    if len(value) > MAX_METADATA_KEYS:
        raise ValueError(f"metadata supports at most {MAX_METADATA_KEYS} keys")

    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("metadata keys must be non-empty strings")
        if not isinstance(item, str):
            raise ValueError("metadata values must be strings")
        if len(item) > MAX_METADATA_VALUE_LENGTH:
            raise ValueError(
                f"metadata value for '{key}' must be at most {MAX_METADATA_VALUE_LENGTH} chars"
            )
        normalized[key.strip()] = item
    return normalized


def parse_amount(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("amount must be a number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("amount must be an integer value in tyiyn")
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            as_float = float(stripped)
        except ValueError as exc:
            raise ValueError("amount must be numeric") from exc
        if not as_float.is_integer():
            raise ValueError("amount must be an integer value in tyiyn")
        return int(as_float)
    raise ValueError("amount must be numeric")


def parse_string(value: Any) -> str:
    if value is None:
        raise ValueError("value is required")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped
    return str(value)


def parse_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return parse_string(value)


class MetadataRequestMixin(APIModel):
    metadata: dict[str, str] | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_request_metadata(cls, value: Any) -> dict[str, str] | None:
        return validate_metadata(value)


class DynamicQRCreate(MetadataRequestMixin):
    amount: int = Field(gt=0, description="Amount in tyiyn")
    branch: int | None = Field(default=None, gt=0)
    cashier: int | None = Field(default=None, gt=0)
    is_long_living: bool | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> int:
        amount = parse_amount(value)
        if amount is None:
            raise ValueError("amount is required")
        return amount


class StaticQRCreate(MetadataRequestMixin):
    branch: int = Field(gt=0)
    cashier: int = Field(gt=0)
    amount: int | None = Field(default=None, gt=0, description="Amount in tyiyn")
    change_amount: bool | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_optional_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class Transaction(APIModel):
    id: str
    amount: int | None = None
    status: str | None = None
    transaction_type: str | None = None
    created_at: datetime | None = None
    branch: int | str | None = None
    cashier: int | str | None = None
    paid_at: datetime | None = None
    metadata: Any = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("status", "transaction_type", mode="before")
    @classmethod
    def validate_optional_response_string(cls, value: Any) -> str | None:
        return parse_optional_string(value)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_response_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class DynamicQRResponse(Transaction):
    payment_token: str


class StaticQRResponse(APIModel):
    id: int | str
    static_qr_link: str
    branch: int | str | None = None
    cashier: int | str | None = None
    amount: int | None = None
    change_amount: bool | None = None
    metadata: Any = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_response_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class TransactionListResponse(APIModel):
    count: int
    next: str | None = None
    previous: str | None = None
    page_count: int | None = None
    results: list[Transaction] = Field(default_factory=list)


class TransactionDetail(APIModel):
    branch_id: int | None = None
    branch_name: str | None = None
    transaction_id: str
    transaction_sum: int | float | None = None
    transaction_status: str | None = None
    bank_commission: int | float | None = None
    m_bonus: int | float | None = None
    transaction_date: datetime | None = None
    refund_date: datetime | None = None

    @field_validator("transaction_id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("transaction_status", mode="before")
    @classmethod
    def validate_optional_status(cls, value: Any) -> str | None:
        return parse_optional_string(value)


class TransactionDetailListResponse(APIModel):
    count: int
    next: str | None = None
    previous: str | None = None
    page_count: int | None = None
    results: list[TransactionDetail] = Field(default_factory=list)


class Cashier(APIModel):
    id: int
    name: str | None = None
    phone: str | None = None
    login: str | None = None


class Branch(APIModel):
    id: int
    name: str | None = None
    address: str | None = None
    cashiers: list[Cashier] = Field(default_factory=list)


class BranchListResponse(APIModel):
    count: int
    next: str | None = None
    previous: str | None = None
    page_count: int | None = None
    results: list[Branch] = Field(default_factory=list)


class WebhookPayload(APIModel):
    id: str
    status: str
    amount: int | None = None
    created_at: datetime | None = None
    paid_at: datetime | None = None
    metadata: Any = None

    @field_validator("id", "status", mode="before")
    @classmethod
    def validate_webhook_string(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_webhook_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class WebhookAck(APIModel):
    ok: bool = True
    transaction_id: str
    duplicate: bool = False


class CancelResponse(APIModel):
    transaction_id: str
    message: str


class TransactionFilters(APIModel):
    page: int | None = Field(default=None, ge=1)
    status: str | None = None
    type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    branch: int | None = Field(default=None, gt=0)
    cashier: int | None = Field(default=None, gt=0)
