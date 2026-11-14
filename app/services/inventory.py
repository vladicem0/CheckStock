"""Read models with transaction boundaries and isolation owned by the caller.

For a combined report, provide a fresh session and select REPEATABLE READ before
its first SQL statement. These functions issue multiple reads and do not start,
commit, or change the isolation of a caller's transaction. A previously used
session may also contain stale ORM instances and is not a fresh report snapshot.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse


def warehouse_view(warehouse: Warehouse) -> dict[str, Any]:
    return dict(id=warehouse.id, name=warehouse.name, archived=warehouse.archived_at is not None,
                archived_at=warehouse.archived_at, created_at=warehouse.created_at)


def operation_view(session: Session, operation: Operation) -> dict[str, Any]:
    """Project current warehouse names; historical snapshots belong to events."""
    product = session.get(Product, operation.product_id)
    source = session.get(Warehouse, operation.source_warehouse_id) if operation.source_warehouse_id else None
    destination = session.get(Warehouse, operation.destination_warehouse_id) if operation.destination_warehouse_id else None
    return dict(id=operation.id, kind=operation.kind, status=operation.status,
                product_id=product.id, sku=product.sku, product_name=product.name, qty=operation.qty,
                source_warehouse_id=operation.source_warehouse_id,
                destination_warehouse_id=operation.destination_warehouse_id,
                source_name=source.name if source else "Приход",
                destination_name=destination.name if destination else operation.external_party,
                external_party=operation.external_party, idempotency_key=operation.idempotency_key,
                created_at=operation.created_at, updated_at=operation.updated_at)


def list_operations(session: Session) -> list[dict[str, Any]]:
    return [operation_view(session, operation) for operation in session.scalars(
        select(Operation).order_by(Operation.created_at.desc(), Operation.id))]


def list_history(session: Session) -> list[dict[str, Any]]:
    """Return immutable event fields, including names and status at event time."""
    return [{column.name: getattr(event, column.name) for column in StockEvent.__table__.columns}
            for event in session.scalars(select(StockEvent).order_by(StockEvent.created_at.desc(), StockEvent.id))]


def get_inventory(session: Session) -> dict[str, Any]:
    """Read all SKU/FF combinations and three transit kinds in the given snapshot."""
    products = session.scalars(select(Product).order_by(Product.sku)).all()
    warehouses = session.scalars(select(Warehouse).order_by(Warehouse.created_at, Warehouse.id)).all()
    balances = {(b.product_id, b.warehouse_id): b for b in session.scalars(select(StockBalance))}
    cells = []
    for product in products:
        for warehouse in warehouses:
            balance = balances.get((product.id, warehouse.id))
            physical = balance.on_hand if balance else 0
            reserved = balance.reserved_fbs if balance else 0
            cells.append(dict(product_id=product.id, warehouse_id=warehouse.id, sku=product.sku,
                              warehouse_name=warehouse.name, archived=warehouse.archived_at is not None,
                              on_hand=physical, reserved_fbs=reserved, available=physical-reserved))
    transit = {"ff": [], "fbo": [], "fbs": []}
    for operation in session.scalars(select(Operation).where(Operation.status.in_(["in_transit", "shipped"]))
                                    .order_by(Operation.created_at, Operation.id)):
        category = "ff" if operation.kind == "transfer" else operation.kind
        transit[category].append(operation_view(session, operation))
    totals = {field: sum(cell[field] for cell in cells) for field in ("on_hand", "reserved_fbs", "available")}
    totals.update({f"transit_{key}": sum(op["qty"] for op in rows) for key, rows in transit.items()})
    return dict(products=[dict(id=p.id, sku=p.sku, name=p.name) for p in products],
                warehouses=[warehouse_view(w) for w in warehouses], balances=cells,
                totals=totals, transit=transit)
