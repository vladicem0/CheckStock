"""Read models with transaction boundaries and isolation owned by the caller.

Callers select PostgreSQL REPEATABLE READ or SERIALIZABLE before the transaction's
first SQL statement. Aggregate readers verify the active isolation and refresh
ORM instances from that snapshot, including objects retained from an earlier
transaction. They never commit or change a caller's transaction isolation.
"""

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse


def _require_report_snapshot(session: Session) -> None:
    isolation = session.scalar(text("SHOW transaction_isolation"))
    if isolation not in {"repeatable read", "serializable"}:
        raise RuntimeError(
            "Reports require REPEATABLE READ or SERIALIZABLE; "
            "configure the caller's transaction before its first SQL statement"
        )


def warehouse_view(warehouse: Warehouse) -> dict[str, Any]:
    return dict(id=warehouse.id, name=warehouse.name, archived=warehouse.archived_at is not None,
                archived_at=warehouse.archived_at, created_at=warehouse.created_at)


def operation_view(session: Session, operation: Operation) -> dict[str, Any]:
    """Project current warehouse names; historical snapshots belong to events."""
    product = session.get(Product, operation.product_id, populate_existing=True)
    source = (
        session.get(Warehouse, operation.source_warehouse_id, populate_existing=True)
        if operation.source_warehouse_id else None
    )
    destination = (
        session.get(Warehouse, operation.destination_warehouse_id, populate_existing=True)
        if operation.destination_warehouse_id else None
    )
    return _operation_view(operation, product, source, destination)


def _operation_view(
    operation: Operation,
    product: Product,
    source: Warehouse | None,
    destination: Warehouse | None,
) -> dict[str, Any]:
    """Project already loaded references without further database access."""
    return dict(id=operation.id, kind=operation.kind, status=operation.status,
                product_id=product.id, sku=product.sku, product_name=product.name, qty=operation.qty,
                source_warehouse_id=operation.source_warehouse_id,
                destination_warehouse_id=operation.destination_warehouse_id,
                source_name=source.name if source else "Приход",
                destination_name=destination.name if destination else operation.external_party,
                external_party=operation.external_party, idempotency_key=operation.idempotency_key,
                created_at=operation.created_at, updated_at=operation.updated_at)


def list_operations(session: Session) -> list[dict[str, Any]]:
    _require_report_snapshot(session)
    operations = session.scalars(
        select(Operation).order_by(Operation.created_at.desc(), Operation.id)
        .execution_options(populate_existing=True)
    ).all()
    # Keep refreshed references alive for this report, including reused sessions.
    products = {product.id: product for product in session.scalars(
        select(Product).execution_options(populate_existing=True))}
    warehouses = {warehouse.id: warehouse for warehouse in session.scalars(
        select(Warehouse).execution_options(populate_existing=True))}
    return [_operation_view(
        operation, products[operation.product_id],
        warehouses.get(operation.source_warehouse_id),
        warehouses.get(operation.destination_warehouse_id),
    ) for operation in operations]


def list_history(session: Session) -> list[dict[str, Any]]:
    """Return immutable event fields, including names and status at event time."""
    _require_report_snapshot(session)
    return [{column.name: getattr(event, column.name) for column in StockEvent.__table__.columns}
            for event in session.scalars(
                select(StockEvent).order_by(StockEvent.created_at.desc(), StockEvent.id)
                .execution_options(populate_existing=True))]


def get_inventory(session: Session) -> dict[str, Any]:
    """Read all SKU/FF combinations and three transit kinds in the given snapshot."""
    _require_report_snapshot(session)
    products = session.scalars(
        select(Product).order_by(Product.sku).execution_options(populate_existing=True)
    ).all()
    warehouses = session.scalars(
        select(Warehouse).order_by(Warehouse.created_at, Warehouse.id)
        .execution_options(populate_existing=True)
    ).all()
    products_by_id = {product.id: product for product in products}
    warehouses_by_id = {warehouse.id: warehouse for warehouse in warehouses}
    balances = {(b.product_id, b.warehouse_id): b for b in session.scalars(
        select(StockBalance).execution_options(populate_existing=True))}
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
                                    .order_by(Operation.created_at, Operation.id)
                                    .execution_options(populate_existing=True)):
        category = "ff" if operation.kind == "transfer" else operation.kind
        transit[category].append(_operation_view(
            operation, products_by_id[operation.product_id],
            warehouses_by_id.get(operation.source_warehouse_id),
            warehouses_by_id.get(operation.destination_warehouse_id),
        ))
    totals = {field: sum(cell[field] for cell in cells) for field in ("on_hand", "reserved_fbs", "available")}
    totals.update({f"transit_{key}": sum(op["qty"] for op in rows) for key, rows in transit.items()})
    return dict(products=[dict(id=p.id, sku=p.sku, name=p.name) for p in products],
                warehouses=[warehouse_view(w) for w in warehouses], balances=cells,
                totals=totals, transit=transit)
