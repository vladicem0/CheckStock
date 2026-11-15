"""Validated HTTP contracts; accounting remains in the service layer."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.services.stock import DomainError, MAX_QUANTITY, require_postgres_text


def _postgres_text(value: str) -> str:
    """Reuse service text validation as a request-validation error."""
    try:
        require_postgres_text(value)
    except DomainError as error:
        raise ValueError(error.message) from None
    return value


def _idempotency_key(value: str) -> str:
    if not value.strip():
        raise ValueError("Ключ идемпотентности не должен быть пустым или пробельным")
    return _postgres_text(value)


Quantity = Annotated[
    int,
    Field(strict=True, gt=0, le=MAX_QUANTITY, description="Положительное целое количество в штуках"),
]
ShortText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_postgres_text),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=200),
    AfterValidator(_idempotency_key),
]


class RequestModel(BaseModel):
    # Keys are opaque. Only explicitly designated name fields trim whitespace.
    model_config = ConfigDict(extra="forbid")


class WarehouseInput(RequestModel):
    name: ShortText = Field(description="Название ФФ; пробелы по краям удаляются", examples=["Новый ФФ"])


class OperationInput(RequestModel):
    product_id: UUID = Field(description="ID товара из GET /api/stocks")
    qty: Quantity = Field(examples=[3])
    idempotency_key: IdempotencyKey = Field(
        description=(
            "Уникальный ключ команды. Повторите тот же ключ и данные для безопасного повтора; "
            "для новой операции укажите новый ключ. Пробелы по краям значимы."
        ),
    )


class ReceiptInput(OperationInput):
    warehouse_id: UUID = Field(description="ID активного ФФ для прихода")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "product_id": "8ed60dc5-29fb-58f9-a6e0-9046c1108ded",
        "warehouse_id": "17451239-1dea-5c9f-8735-ef56097e7b6e",
        "qty": 2,
        "idempotency_key": "swagger:receipt:001",
    }]})


class TransferInput(OperationInput):
    source_warehouse_id: UUID = Field(description="ID ФФ-источника; может быть архивным")
    destination_warehouse_id: UUID = Field(description="ID активного ФФ-получателя, отличный от источника")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "product_id": "8ed60dc5-29fb-58f9-a6e0-9046c1108ded",
        "source_warehouse_id": "17451239-1dea-5c9f-8735-ef56097e7b6e",
        "destination_warehouse_id": "0e00f1c5-23e5-5ae3-9a6c-9a89ead73ac4",
        "qty": 3,
        "idempotency_key": "swagger:transfer:001",
    }]})


class FbsInput(OperationInput):
    warehouse_id: UUID = Field(description="ID активного ФФ, на котором создаётся резерв")
    customer: ShortText = Field(description="Получатель FBS; пробелы по краям удаляются")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "product_id": "8ed60dc5-29fb-58f9-a6e0-9046c1108ded",
        "warehouse_id": "17451239-1dea-5c9f-8735-ef56097e7b6e",
        "qty": 2,
        "customer": "Клиент",
        "idempotency_key": "swagger:fbs:001",
    }]})


class FboInput(OperationInput):
    warehouse_id: UUID = Field(description="ID активного ФФ отправки")
    marketplace: ShortText = Field(description="Маркетплейс-получатель; пробелы по краям удаляются")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "product_id": "8ed60dc5-29fb-58f9-a6e0-9046c1108ded",
        "warehouse_id": "17451239-1dea-5c9f-8735-ef56097e7b6e",
        "qty": 2,
        "marketplace": "Маркетплейс",
        "idempotency_key": "swagger:fbo:001",
    }]})


class WarehouseView(BaseModel):
    id: UUID
    name: str
    archived: bool
    archived_at: datetime | None
    created_at: datetime


class ProductView(BaseModel):
    id: UUID
    sku: str
    name: str


class OperationView(BaseModel):
    id: UUID
    kind: Literal["receipt", "transfer", "fbs", "fbo"]
    status: Literal[
        "completed", "draft", "in_transit", "received", "reserved", "shipped", "delivered", "cancelled"
    ]
    product_id: UUID
    sku: str
    product_name: str
    qty: Quantity
    source_warehouse_id: UUID | None
    destination_warehouse_id: UUID | None
    source_name: str
    destination_name: str
    external_party: str | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class StockEventView(BaseModel):
    id: UUID
    operation_id: UUID
    event_type: str
    operation_status: str
    product_id: UUID
    sku: str
    qty: Quantity
    from_warehouse_id: UUID | None
    to_warehouse_id: UUID | None
    from_name: str
    to_name: str
    created_at: datetime


class BalanceView(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    sku: str
    warehouse_name: str
    archived: bool
    on_hand: int
    reserved_fbs: int
    available: int


class StockTotals(BaseModel):
    on_hand: int
    reserved_fbs: int
    available: int
    transit_ff: int
    transit_fbo: int
    transit_fbs: int


class TransitView(BaseModel):
    ff: list[OperationView]
    fbo: list[OperationView]
    fbs: list[OperationView]


class InventoryView(BaseModel):
    products: list[ProductView]
    warehouses: list[WarehouseView]
    balances: list[BalanceView]
    totals: StockTotals
    transit: TransitView


class FieldErrorView(BaseModel):
    field: str
    message: str


class ErrorView(BaseModel):
    detail: str
    available_here: int | None = None
    requested: int | None = None
    errors: list[FieldErrorView] | None = None
