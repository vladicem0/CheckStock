"""Transactional inventory commands; the caller owns commit and rollback."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Operation, StockEvent, Warehouse
from app.services.stock import (
    DomainError,
    MAX_QUANTITY,
    add_event,
    create_identity,
    lock_balance,
    lock_warehouses,
    product,
    require_active,
    require_available,
    require_postgres_text,
)


def _external_party(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise DomainError("Укажите клиента или маркетплейс: от 1 до 200 символов", 422)
    require_postgres_text(value)
    return value.strip()


def _record_created(
    session: Session,
    operation: Operation,
    event_type: str,
    warehouses: dict[UUID, Warehouse],
) -> Operation:
    session.add(operation)
    session.flush()
    add_event(session, operation, event_type, warehouses)
    return operation


def _locked_transition(
    session: Session,
    operation_id: UUID,
    *,
    kind: str,
    event_type: str,
    required_status: str,
) -> tuple[Operation, bool]:
    operation = session.scalar(
        select(Operation)
        .where(Operation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None:
        raise DomainError("Операция не найдена", 404)
    if operation.kind != kind:
        raise DomainError("Вид операции не соответствует выбранному действию")
    already_done = session.scalar(
        select(StockEvent.id).where(
            StockEvent.operation_id == operation.id,
            StockEvent.event_type == event_type,
        )
    )
    if already_done is not None:
        # A dispatch remains a valid replay even after the later receive.
        return operation, True
    if operation.status != required_status:
        raise DomainError(
            f"Действие недоступно в статусе «{operation.status}»; "
            f"необходим статус «{required_status}»"
        )
    return operation, False


def create_transfer(
    session: Session,
    *,
    product_id: UUID,
    source_warehouse_id: UUID,
    destination_warehouse_id: UUID,
    qty: int,
    idempotency_key: str,
) -> Operation:
    """Record a draft and its event without reserving or moving any stock."""
    if source_warehouse_id == destination_warehouse_id:
        raise DomainError("Источник и получатель перемещения должны различаться", 422)
    old, fingerprint = create_identity(
        session,
        "transfer",
        product_id=product_id,
        qty=qty,
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=destination_warehouse_id,
        idempotency_key=idempotency_key,
    )
    if old is not None:
        return old
    product(session, product_id)
    warehouses = lock_warehouses(session, source_warehouse_id, destination_warehouse_id)
    require_active(warehouses[destination_warehouse_id])
    # Drafts do not reserve stock; availability is checked when dispatched.
    return _record_created(
        session,
        Operation(
            kind="transfer",
            status="draft",
            product_id=product_id,
            qty=qty,
            source_warehouse_id=source_warehouse_id,
            destination_warehouse_id=destination_warehouse_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        ),
        "transfer_created",
        warehouses,
    )


def dispatch_transfer(session: Session, operation_id: UUID) -> Operation:
    """Move available source stock into inter-warehouse transit exactly once."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="transfer",
        event_type="transfer_dispatched",
        required_status="draft",
    )
    if replay:
        return operation
    # A previously created transfer may finish after either warehouse is archived.
    warehouses = lock_warehouses(
        session, operation.source_warehouse_id, operation.destination_warehouse_id
    )
    balance = lock_balance(session, operation.product_id, operation.source_warehouse_id)
    require_available(balance, operation.qty)
    balance.on_hand -= operation.qty
    operation.status = "in_transit"
    add_event(session, operation, "transfer_dispatched", warehouses)
    return operation


def receive_transfer(session: Session, operation_id: UUID) -> Operation:
    """Complete transit and credit the destination in the same transaction."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="transfer",
        event_type="transfer_received",
        required_status="in_transit",
    )
    if replay:
        return operation
    warehouses = lock_warehouses(
        session, operation.source_warehouse_id, operation.destination_warehouse_id
    )
    balance = lock_balance(session, operation.product_id, operation.destination_warehouse_id)
    if balance.on_hand + operation.qty > MAX_QUANTITY:
        raise DomainError("Превышен допустимый остаток на складе получателя")
    balance.on_hand += operation.qty
    operation.status = "received"
    add_event(session, operation, "transfer_received", warehouses)
    return operation


def reserve_fbs(
    session: Session,
    *,
    product_id: UUID,
    warehouse_id: UUID,
    qty: int,
    customer: str,
    idempotency_key: str,
) -> Operation:
    """Reserve locally available stock without reducing the physical balance."""
    customer = _external_party(customer)
    old, fingerprint = create_identity(
        session,
        "fbs",
        product_id=product_id,
        qty=qty,
        source_warehouse_id=warehouse_id,
        external_party=customer,
        idempotency_key=idempotency_key,
    )
    if old is not None:
        return old
    product(session, product_id)
    warehouses = lock_warehouses(session, warehouse_id)
    require_active(warehouses[warehouse_id])
    balance = lock_balance(session, product_id, warehouse_id)
    require_available(balance, qty)
    balance.reserved_fbs += qty
    return _record_created(
        session,
        Operation(
            kind="fbs",
            status="reserved",
            product_id=product_id,
            qty=qty,
            source_warehouse_id=warehouse_id,
            external_party=customer,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        ),
        "fbs_reserved",
        warehouses,
    )


def ship_fbs(session: Session, operation_id: UUID) -> Operation:
    """Spend this operation's reserve and move its stock into customer transit."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="fbs",
        event_type="fbs_shipped",
        required_status="reserved",
    )
    if replay:
        return operation
    warehouses = lock_warehouses(session, operation.source_warehouse_id)
    balance = lock_balance(session, operation.product_id, operation.source_warehouse_id)
    if balance.reserved_fbs < operation.qty or balance.on_hand < operation.qty:
        raise DomainError("Резерв операции не согласован с остатком склада")
    balance.on_hand -= operation.qty
    balance.reserved_fbs -= operation.qty
    operation.status = "shipped"
    add_event(session, operation, "fbs_shipped", warehouses)
    return operation


def cancel_fbs(session: Session, operation_id: UUID) -> Operation:
    """Release an unshipped reservation without changing physical stock."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="fbs",
        event_type="fbs_cancelled",
        required_status="reserved",
    )
    if replay:
        return operation
    warehouses = lock_warehouses(session, operation.source_warehouse_id)
    balance = lock_balance(session, operation.product_id, operation.source_warehouse_id)
    if balance.reserved_fbs < operation.qty:
        raise DomainError("Резерв операции не согласован с остатком склада")
    balance.reserved_fbs -= operation.qty
    operation.status = "cancelled"
    add_event(session, operation, "fbs_cancelled", warehouses)
    return operation


def deliver_fbs(session: Session, operation_id: UUID) -> Operation:
    """Finish customer transit without changing warehouse stock or reserves."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="fbs",
        event_type="fbs_delivered",
        required_status="shipped",
    )
    if replay:
        return operation
    # Existing shipments may finish after the source is archived or renamed.
    warehouses = lock_warehouses(session, operation.source_warehouse_id)
    operation.status = "delivered"
    add_event(session, operation, "fbs_delivered", warehouses)
    return operation


def ship_fbo(
    session: Session,
    *,
    product_id: UUID,
    warehouse_id: UUID,
    qty: int,
    marketplace: str,
    idempotency_key: str,
) -> Operation:
    """Ship available stock directly into marketplace transit."""
    marketplace = _external_party(marketplace)
    old, fingerprint = create_identity(
        session,
        "fbo",
        product_id=product_id,
        qty=qty,
        source_warehouse_id=warehouse_id,
        external_party=marketplace,
        idempotency_key=idempotency_key,
    )
    if old is not None:
        return old
    product(session, product_id)
    warehouses = lock_warehouses(session, warehouse_id)
    require_active(warehouses[warehouse_id])
    balance = lock_balance(session, product_id, warehouse_id)
    require_available(balance, qty)
    balance.on_hand -= qty
    return _record_created(
        session,
        Operation(
            kind="fbo",
            status="in_transit",
            product_id=product_id,
            qty=qty,
            source_warehouse_id=warehouse_id,
            external_party=marketplace,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        ),
        "fbo_shipped",
        warehouses,
    )


def receive_fbo(session: Session, operation_id: UUID) -> Operation:
    """Finish marketplace transit without crediting an FF warehouse."""
    operation, replay = _locked_transition(
        session,
        operation_id,
        kind="fbo",
        event_type="fbo_received",
        required_status="in_transit",
    )
    if replay:
        return operation
    warehouses = lock_warehouses(session, operation.source_warehouse_id)
    # The marketplace is outside FF stock; receiving only completes transit.
    operation.status = "received"
    add_event(session, operation, "fbo_received", warehouses)
    return operation
