"""T01: actual migration and receipt-based seed, without modifying demo data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app import seed
from app.seed import demo_id, seed_demo
from app.services.stock import DomainError


def snapshot(db):
    with db() as session:
        return {
            "balances": session.execute(select(StockBalance.product_id, StockBalance.warehouse_id,
                StockBalance.on_hand, StockBalance.reserved_fbs).order_by(
                    StockBalance.product_id, StockBalance.warehouse_id)).all(),
            "operations": session.execute(select(Operation.id, Operation.kind, Operation.status,
                Operation.qty, Operation.idempotency_key).order_by(Operation.id)).all(),
            "events": session.execute(select(StockEvent.id, StockEvent.operation_id,
                StockEvent.event_type, StockEvent.qty, StockEvent.from_name,
                StockEvent.to_name).order_by(StockEvent.id)).all(),
        }


def test_t01_seed_has_25_units_four_receipts_and_six_combinations(db):
    with db() as session:
        assert session.bind.dialect.name == "postgresql"
        assert session.scalar(text("SELECT current_schema()" )).startswith("test_checkstock_")
        assert session.scalar(select(func.count()).select_from(Product)) == 3
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 2
        assert session.scalar(select(func.sum(StockBalance.on_hand))) == 25
        assert session.scalar(select(func.sum(StockBalance.reserved_fbs))) == 0
        rows = session.execute(text("""
            SELECT p.sku, w.name, coalesce(b.on_hand, 0) AS on_hand,
                   coalesce(b.reserved_fbs, 0) AS reserved
            FROM products p CROSS JOIN warehouses w
            LEFT JOIN stock_balances b ON b.product_id = p.id AND b.warehouse_id = w.id
        """)).all()
        assert {(sku, warehouse): (physical, reserved) for sku, warehouse, physical, reserved in rows} == {
            ("SKU-101", "Север"): (10, 0), ("SKU-101", "Юг"): (6, 0),
            ("SKU-102", "Север"): (5, 0), ("SKU-102", "Юг"): (0, 0),
            ("SKU-103", "Север"): (0, 0), ("SKU-103", "Юг"): (4, 0),
        }
        operations = session.scalars(select(Operation)).all()
        events = session.scalars(select(StockEvent)).all()
        assert len(operations) == len(events) == 4
        assert all(op.kind == "receipt" and op.status == "completed" for op in operations)
        assert all(event.event_type == "receipt_completed" for event in events)
        assert all(event.operation_status == "completed" for event in events)
        assert sum(event.qty for event in events) == 25
        assert {event.operation_id for event in events} == {op.id for op in operations}
        assert all(event.created_at.tzinfo is not None for event in events)
        assert session.execute(text("""
            SELECT coalesce(sum(qty) FILTER (WHERE kind = 'transfer' AND status = 'in_transit'), 0),
                   coalesce(sum(qty) FILTER (WHERE kind = 'fbo' AND status = 'in_transit'), 0),
                   coalesce(sum(qty) FILTER (WHERE kind = 'fbs' AND status = 'shipped'), 0)
            FROM operations
        """)).one() == (0, 0, 0)
    before = snapshot(db)
    with db.begin() as session:
        seed_demo(session)
    assert snapshot(db) == before


def test_seed_after_rename_preserves_warehouse_ids_and_history(db):
    before = snapshot(db)
    with db.begin() as session:
        warehouse = session.get(Warehouse, demo_id("north"))
        warehouse.name = "Север после переименования"
    with db.begin() as session:
        seed_demo(session)
    assert snapshot(db) == before
    with db() as session:
        assert session.get(Warehouse, demo_id("north")).name == "Север после переименования"
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 2


def test_seed_replay_after_archive_preserves_every_row(db, stock_snapshot):
    with db.begin() as session:
        session.get(Warehouse, demo_id("north")).archived_at = datetime.now(timezone.utc)
    before = stock_snapshot()
    with db.begin() as session:
        seed_demo(session)
    assert stock_snapshot() == before


def test_seed_does_not_substitute_an_unrelated_same_name_warehouse(empty_db):
    unrelated_id = uuid4()
    with empty_db.begin() as session:
        session.add(Warehouse(id=unrelated_id, name="Север"))
    with empty_db.begin() as session:
        seed_demo(session)
    with empty_db() as session:
        assert session.get(Warehouse, unrelated_id).name == "Север"
        assert session.get(Warehouse, demo_id("north")).name == "Север"
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 3
        assert session.scalar(select(func.count()).select_from(StockBalance).where(
            StockBalance.warehouse_id == unrelated_id,
        )) == 0
        assert session.scalar(select(func.sum(StockBalance.on_hand))) == 25


def test_seed_conflicting_sku_rolls_back_without_replacing_existing_product(empty_db, stock_snapshot):
    existing_id = uuid4()
    with empty_db.begin() as session:
        session.add(Product(id=existing_id, sku="SKU-101", name="Ранее созданный товар"))
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with empty_db.begin() as session:
            seed_demo(session)
    assert caught.value.status_code == 409
    assert stock_snapshot() == before


def test_seed_failure_rolls_back_catalog_and_earlier_receipts(empty_db, stock_snapshot, monkeypatch):
    before = stock_snapshot()
    original_receipt = seed.receive_stock
    calls = 0

    def fail_third_receipt(session, **values):
        nonlocal calls
        calls += 1
        if calls == 3:
            assert session.scalar(select(func.count()).select_from(Operation)) == 2
            raise RuntimeError("Injected seed failure")
        return original_receipt(session, **values)

    monkeypatch.setattr(seed, "receive_stock", fail_third_receipt)
    with pytest.raises(RuntimeError, match="Injected seed failure"):
        with empty_db.begin() as session:
            seed_demo(session)
    assert calls == 3
    assert stock_snapshot() == before


def test_concurrent_seed_creates_one_set_of_receipts(empty_db):
    barrier = Barrier(2)

    def worker():
        with empty_db.begin() as session:
            backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
            barrier.wait(timeout=10)
            seed_demo(session)
        return backend_pid

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        backend_pids = [future.result(timeout=30) for future in futures]
    assert len(set(backend_pids)) == 2
    with empty_db() as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 3
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 2
        assert session.scalar(select(func.sum(StockBalance.on_hand))) == 25
        assert session.scalar(select(func.sum(StockBalance.reserved_fbs))) == 0
        assert session.scalar(select(func.count()).select_from(Operation)) == 4
        assert session.scalar(select(func.count()).select_from(StockEvent)) == 4
