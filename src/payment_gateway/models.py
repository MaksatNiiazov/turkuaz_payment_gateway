from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_METADATA_KEYS = 5
MAX_METADATA_VALUE_LENGTH = 150
SUPPORTED_PRINT_QR_PROVIDERS = {"mkassa", "odengi"}


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


def parse_code(value: Any) -> str:
    parsed = parse_string(value).lower()
    if not all(char.isalnum() or char in {"_", "-"} for char in parsed):
        raise ValueError("code may contain only letters, numbers, underscore, or dash")
    return parsed


def parse_print_provider(value: Any) -> str:
    parsed = parse_string(value).lower()
    if parsed not in SUPPORTED_PRINT_QR_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PRINT_QR_PROVIDERS))
        raise ValueError(f"provider must be one of: {supported}")
    return parsed


class MetadataRequestMixin(APIModel):
    metadata: dict[str, str] | None = Field(
        default=None,
        description=(
            "Additional MKassa metadata. Maximum 5 keys, each value up to 150 chars. "
            "For stable 1C invoice binding use invoice_id. "
            "For human-readable facture code use invoice_number."
        ),
        examples=[
            {
                "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                "invoice_number": "TIGER-FACTURE-1001",
                "source": "tiger",
            }
        ],
    )

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_request_metadata(cls, value: Any) -> dict[str, str] | None:
        return validate_metadata(value)


class DynamicQRCreate(MetadataRequestMixin):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Dynamic QR with Tiger facture code",
                    "value": {
                        "amount": 100,
                        "metadata": {
                            "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
                {
                    "summary": "Dynamic QR with explicit branch and cashier",
                    "value": {
                        "amount": 100,
                        "branch": 236366,
                        "cashier": 130610,
                        "is_long_living": True,
                        "metadata": {
                            "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
            ]
        }
    )

    amount: int = Field(
        gt=0,
        description="Amount in tyiyn. Example: 100 = 1 som.",
        examples=[100],
    )
    branch: int | None = Field(
        default=None,
        gt=0,
        description=(
            "MKassa branch ID. Optional for dynamic QR when the MKassa key is bound "
            "to a default branch."
        ),
        examples=[236366],
    )
    cashier: int | None = Field(
        default=None,
        gt=0,
        description=(
            "MKassa cashier ID. Optional for dynamic QR when the MKassa key is bound "
            "to a default cashier."
        ),
        examples=[130610],
    )
    is_long_living: bool | None = Field(
        default=None,
        description="Requests increased payment waiting time if MKassa enabled it for the account.",
        examples=[True],
    )

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> int:
        amount = parse_amount(value)
        if amount is None:
            raise ValueError("amount is required")
        return amount


class StaticQRCreate(MetadataRequestMixin):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Static QR with fixed amount",
                    "value": {
                        "branch": 236366,
                        "cashier": 130610,
                        "amount": 100,
                        "change_amount": False,
                        "metadata": {
                            "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                            "invoice_number": "TIGER-FACTURE-1001",
                            "source": "tiger",
                        },
                    },
                },
                {
                    "summary": "Static QR with accounting metadata",
                    "value": {
                        "branch": 236366,
                        "cashier": 130610,
                        "amount": 100,
                        "change_amount": False,
                        "metadata": {
                            "payer_code": "12345678901234",
                            "payer_full_name": "ОсОО Тест",
                            "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                            "invoice_number": "TIGER-FACTURE-1001",
                        },
                    },
                },
            ]
        }
    )

    branch: int | None = Field(
        default=None,
        gt=0,
        description="MKassa branch ID. Not required by O!Dengi.",
        examples=[236366],
    )
    cashier: int | None = Field(
        default=None,
        gt=0,
        description="MKassa cashier ID. Not required by O!Dengi.",
        examples=[130610],
    )
    amount: int | None = Field(
        default=None,
        gt=0,
        description="Optional amount in tyiyn. Example: 100 = 1 som.",
        examples=[100],
    )
    change_amount: bool | None = Field(
        default=None,
        description=(
            "Whether payer may edit amount during payment. If amount and change_amount "
            "are omitted, MKassa creates a static QR without fixed amount."
        ),
        examples=[False],
    )

    @field_validator("amount", mode="before")
    @classmethod
    def validate_optional_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class Transaction(APIModel):
    id: str = Field(description="MKassa transaction ID.", examples=["MKSA-..."])
    amount: int | None = Field(default=None, description="Amount in tyiyn.")
    status: str | None = Field(default=None, description="MKassa transaction status.")
    transaction_type: str | None = Field(default=None, description="MKassa transaction type.")
    created_at: datetime | None = Field(default=None, description="Transaction creation datetime.")
    branch: int | str | None = Field(default=None, description="MKassa branch ID or name.")
    cashier: int | str | None = Field(default=None, description="MKassa cashier ID or name.")
    paid_at: datetime | None = Field(default=None, description="Payment datetime if paid.")
    metadata: Any = Field(default=None, description="Metadata returned by MKassa.")

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
    payment_token: str = Field(
        description="Ready-to-encode QR link returned by MKassa.",
        examples=["https://app.mbank.kg/qr/#000201010212..."],
    )


class StaticQRResponse(APIModel):
    id: int | str = Field(description="MKassa static QR ID.")
    static_qr_link: str = Field(
        description="Ready-to-encode static QR link returned by MKassa.",
        examples=["https://app.mbank.kg/qr/#000201010212..."],
    )
    branch: int | str | None = Field(default=None, description="MKassa branch ID or name.")
    cashier: int | str | None = Field(default=None, description="MKassa cashier ID or name.")
    amount: int | None = Field(default=None, description="Amount in tyiyn.")
    change_amount: bool | None = Field(default=None, description="Whether amount is editable.")
    metadata: Any = Field(default=None, description="Metadata returned by MKassa.")

    @field_validator("amount", mode="before")
    @classmethod
    def validate_response_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class PrintQRCodeConfigItem(APIModel):
    code: str = Field(
        min_length=1,
        max_length=64,
        description="Stable code used by 1C layouts, e.g. mbank or obank.",
    )
    label: str = Field(
        min_length=1,
        max_length=150,
        description="Human-readable label printed near the QR image.",
    )
    provider: str = Field(description="Payment provider used to create this QR.")
    enabled: bool = Field(default=True, description="Whether 1C should print this QR.")
    sort_order: int = Field(default=100, ge=0, le=10000, description="Print order.")

    @field_validator("code", mode="before")
    @classmethod
    def validate_code(cls, value: Any) -> str:
        return parse_code(value)

    @field_validator("label", mode="before")
    @classmethod
    def validate_label(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("provider", mode="before")
    @classmethod
    def validate_provider(cls, value: Any) -> str:
        return parse_print_provider(value)


class PrintQRCodeConfigUpdate(APIModel):
    items: list[PrintQRCodeConfigItem] = Field(min_length=1, max_length=10)

    @field_validator("items")
    @classmethod
    def validate_unique_codes(
        cls,
        value: list[PrintQRCodeConfigItem],
    ) -> list[PrintQRCodeConfigItem]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("print QR codes must be unique")
        return value


class InvoiceQRCodeCreate(APIModel):
    amount: int = Field(gt=0, description="Invoice amount in tyiyn. Example: 100 = 1 som.")
    invoice_id: str = Field(
        min_length=1,
        max_length=150,
        description="Stable 1C invoice/document ID.",
    )
    invoice_number: str | None = Field(
        default=None,
        max_length=150,
        description="Human-readable invoice or facture number.",
    )
    source: str = Field(default="1c", max_length=150, description="Source label.")

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> int:
        amount = parse_amount(value)
        if amount is None:
            raise ValueError("amount is required")
        return amount

    @field_validator("invoice_id", "source", mode="before")
    @classmethod
    def validate_required_string(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("invoice_number", mode="before")
    @classmethod
    def validate_optional_invoice_number(cls, value: Any) -> str | None:
        return parse_optional_string(value)


class InvoiceQRCodeItem(APIModel):
    code: str
    label: str
    provider: str
    transaction_id: str
    status: str | None = None
    amount: int | None = None
    image_path: str
    reused: bool


class InvoiceQRCodeBundleResponse(APIModel):
    invoice_id: str
    invoice_number: str | None = None
    items: list[InvoiceQRCodeItem]


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
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "MKSA-99f1e3bd71134019af970fc429af8448",
                    "status": "paid",
                    "amount": "100",
                    "created_at": "2026-05-25T09:28:12.639897+06:00",
                    "paid_at": "2026-05-25T09:28:30+06:00",
                    "metadata": {
                        "invoice_id": "550e8400-e29b-41d4-a716-446655440000",
                        "invoice_number": "TIGER-FACTURE-1001",
                        "source": "tiger",
                    },
                }
            ]
        },
    )

    id: str = Field(description="MKassa transaction ID.")
    status: str = Field(description="MKassa transaction status.")
    amount: int | None = Field(default=None, description="Amount in tyiyn.")
    created_at: datetime | None = Field(default=None, description="Transaction creation datetime.")
    paid_at: datetime | None = Field(default=None, description="Payment datetime if paid.")
    metadata: Any = Field(default=None, description="Metadata returned by MKassa.")

    @field_validator("id", "status", mode="before")
    @classmethod
    def validate_webhook_string(cls, value: Any) -> str:
        return parse_string(value)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_webhook_amount(cls, value: Any) -> int | None:
        return parse_amount(value)


class ODengiWebhookPayload(APIModel):
    trans_id: str | None = Field(default=None, description="O!Dengi payment transaction ID.")
    status_pay: int | str | None = Field(default=None, description="1=pending, 2=canceled, 3=paid.")
    site_id: str | None = Field(default=None, description="O!Dengi merchant ID.")
    order_id: str | None = Field(default=None, description="Merchant order ID.")
    amount: int | None = Field(default=None, description="Amount in tyiyn.")
    currency: str | None = Field(default=None, description="Payment currency.")
    mktime: str | int | None = Field(default=None, description="Provider callback timestamp.")
    test: int | str | None = Field(default=None, description="1=test, 0=production.")
    fields_other: Any = Field(default=None, description="Merchant metadata echoed by O!Dengi.")
    fields_app: Any = Field(default=None, description="Payer app metadata from O!Dengi.")
    hash: str | None = Field(default=None, description="O!Dengi callback signature.")
    account_id: str | None = None
    mobile: str | None = None
    fname: str | None = None
    lname: str | None = None
    email: str | None = None

    @field_validator("trans_id", "order_id", mode="before")
    @classmethod
    def validate_optional_ids(cls, value: Any) -> str | None:
        return parse_optional_string(value)

    @field_validator("amount", mode="before")
    @classmethod
    def validate_odengi_amount(cls, value: Any) -> int | None:
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
