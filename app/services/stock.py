"""Receipt accounting and shared primitives; callers own the transaction."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse

MAX_QUANTITY = 2**31 - 1


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 409, **details: object) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details


def require_postgres_text(value: str) -> None:
    """Reject text PostgreSQL/UTF-8 cannot store before issuing any SQL."""
    if "\x00" in value:
        raise DomainError("Текст содержит недопустимый символ", 422)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise DomainError("Текст содержит недопустимый символ", 422) from error


def product(session: Session, product_id: UUID) -> Product:
    item = session.get(Product, product_id)
    if item is None:
        raise DomainError("Товар не найден", 404)
    return item


def lock_warehouses(session: Session, *ids: UUID | None) -> dict[UUID, Warehouse]:
    result: dict[UUID, Warehouse] = {}
    for warehouse_id in sorted({item for item in ids if item is not None}):
        item = session.scalar(
            select(Warehouse)
            .where(Warehouse.id == warehouse_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise DomainError("Склад не найден", 404)
        result[warehouse_id] = item
    return result


def require_active(warehouse: Warehouse) -> None:
    if warehouse.archived_at is not None:
        raise DomainError(f"ФФ «{warehouse.name}» архивирован: новая операция запрещена")


def lock_balance(session: Session, product_id: UUID, warehouse_id: UUID) -> StockBalance:
    session.execute(
        insert(StockBalance)
        .values(product_id=product_id, warehouse_id=warehouse_id, on_hand=0, reserved_fbs=0)
        .on_conflict_do_nothing(index_elements=["product_id", "warehouse_id"])
    )
    return session.scalars(
        select(StockBalance)
        .where(
            StockBalance.product_id == product_id,
            StockBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def require_available(balance: StockBalance, qty: int) -> None:
    """Check the locked local balance; other warehouses cannot cover a shortage."""
    if balance.available < qty:
        raise DomainError(
            "Недостаточно доступного товара на выбранном ФФ",
            available_here=balance.available,
            requested=qty,
        )


def create_identity(
    session: Session,
    kind: str,
    *,
    product_id: UUID,
    qty: int,
    source_warehouse_id: UUID | None = None,
    destination_warehouse_id: UUID | None = None,
    external_party: str | None = None,
    idempotency_key: str,
) -> tuple[Operation | None, str]:
    if type(qty) is not int or not 0 < qty <= MAX_QUANTITY:
        raise DomainError("Количество должно быть положительным целым числом", 422)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 200
    ):
        raise DomainError("Нужен непустой ключ идемпотентности до 200 символов", 422)
    require_postgres_text(idempotency_key)
    payload = {
        "kind": kind,
        "product_id": str(product_id),
        "qty": qty,
        "source_warehouse_id": str(source_warehouse_id) if source_warehouse_id else None,
        "destination_warehouse_id": str(destination_warehouse_id) if destination_warehouse_id else None,
        "external_party": external_party,
    }
    normalized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
    # A hash collision only serializes unrelated keys; UNIQUE + fingerprint decides identity.
    advisory_key = int.from_bytes(hashlib.sha256(idempotency_key.encode("utf-8")).digest()[:8], signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": advisory_key})
    old = session.scalar(
        select(Operation)
        .where(Operation.idempotency_key == idempotency_key)
        .execution_options(populate_existing=True)
    )
    if old is not None and old.request_fingerprint != fingerprint:
        raise DomainError("Ключ идемпотентности уже использован с другими данными")
    return old, fingerprint


def add_event(
    session: Session,
    operation: Operation,
    event_type: str,
    warehouses: dict[UUID, Warehouse],
) -> None:
    item = product(session, operation.product_id)
    session.add(
        StockEvent(
            operation_id=operation.id,
            event_type=event_type,
            operation_status=operation.status,
            product_id=item.id,
            sku=item.sku,
            qty=operation.qty,
            from_warehouse_id=operation.source_warehouse_id,
            to_warehouse_id=operation.destination_warehouse_id,
            from_name=(
                warehouses[operation.source_warehouse_id].name
                if operation.source_warehouse_id else "Приход"
            ),
            to_name=(
                warehouses[operation.destination_warehouse_id].name
                if operation.destination_warehouse_id else operation.external_party
            ),
        )
    )
    session.flush()


def receive_stock(
    session: Session,
    *,
    product_id: UUID,
    warehouse_id: UUID,
    qty: int,
    idempotency_key: str,
) -> Operation:
    """Receive once into the selected warehouse inside the caller's transaction."""
    old, fingerprint = create_identity(
        session,
        "receipt",
        product_id=product_id,
        qty=qty,
        destination_warehouse_id=warehouse_id,
        idempotency_key=idempotency_key,
    )
    if old is not None:
        return old
    product(session, product_id)
    warehouses = lock_warehouses(session, warehouse_id)
    require_active(warehouses[warehouse_id])
    balance = lock_balance(session, product_id, warehouse_id)
    if balance.on_hand + qty > MAX_QUANTITY:
        raise DomainError("Превышен допустимый остаток")
    balance.on_hand += qty
    operation = Operation(
        kind="receipt",
        status="completed",
        product_id=product_id,
        qty=qty,
        destination_warehouse_id=warehouse_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(operation)
    session.flush()
    add_event(session, operation, "receipt_completed", warehouses)
    return operation
