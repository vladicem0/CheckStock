"""Read committed state through fresh sessions; never inspect stale ORM caches."""
from sqlalchemy import func, select

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app.seed import demo_id

NORTH = demo_id("north")
SOUTH = demo_id("south")
P101 = demo_id("SKU-101")
P102 = demo_id("SKU-102")
P103 = demo_id("SKU-103")


def run(db, service, *args, **kwargs):
    with db.begin() as session:
        result = service(session, *args, **kwargs)
        session.flush()
        return result


def balance(db, product_id=P101, warehouse_id=NORTH):
    with db() as session:
        item = session.get(StockBalance, (product_id, warehouse_id))
        return (item.on_hand, item.reserved_fbs, item.available) if item else (0, 0, 0)


def transit(db, kind):
    with db() as session:
        status = "shipped" if kind == "fbs" else "in_transit"
        return session.scalar(select(func.coalesce(func.sum(Operation.qty), 0)).where(
            Operation.kind == kind, Operation.status == status))


def events(db, operation_id):
    with db() as session:
        return session.scalars(select(StockEvent).where(
            StockEvent.operation_id == operation_id).order_by(StockEvent.created_at, StockEvent.id)).all()


def snapshot(db):
    with db() as session:
        return {
            model.__tablename__: session.execute(select(model.__table__).order_by(
                *model.__table__.primary_key.columns)).all()
            for model in (Product, Warehouse, StockBalance, Operation, StockEvent)
        }
