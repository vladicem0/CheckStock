"""Explicit, repeatable demonstration receipts. Never called by server startup."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Product, Warehouse
from app.services.stock import DomainError, receive_stock


def demo_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"checkstock/demo/v1/{name}")


def seed_demo(session: Session) -> None:
    """Create stable catalog entries and four receipts in one caller transaction."""
    for sku, name in [("SKU-101", "Органайзер"), ("SKU-102", "Контейнер"), ("SKU-103", "Корзина")]:
        product_id = demo_id(sku)
        session.execute(
            insert(Product).values(id=product_id, sku=sku, name=name).on_conflict_do_nothing()
        )
        if session.get(Product, product_id) is None:
            raise DomainError(f"Демонстрационный артикул {sku} уже принадлежит другому товару")
    for code, name in [("north", "Север"), ("south", "Юг")]:
        session.execute(
            insert(Warehouse)
            .values(id=demo_id(code), name=name)
            .on_conflict_do_nothing(index_elements=["id"])
        )
    for sku, code, qty in [
        ("SKU-101", "north", 10), ("SKU-101", "south", 6),
        ("SKU-102", "north", 5), ("SKU-103", "south", 4),
    ]:
        receive_stock(
            session,
            product_id=demo_id(sku),
            warehouse_id=demo_id(code),
            qty=qty,
            idempotency_key=f"demo:v1:{sku}:{code}",
        )


if __name__ == "__main__":
    with SessionLocal.begin() as session:
        seed_demo(session)
    print("Демонстрационные приходы применены; повторный запуск не дублирует движения.")
