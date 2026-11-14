"""Receipt transactions used by the explicit demo seed, on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.models import Operation, StockBalance, StockEvent, Warehouse
from app.seed import demo_id
from app.services import stock
from app.services.stock import DomainError, receive_stock


def receipt(session, **overrides):
    values = {
        "product_id": demo_id("SKU-101"),
        "warehouse_id": demo_id("north"),
        "qty": 3,
        "idempotency_key": "test:receipt",
    }
    values.update(overrides)
    return receive_stock(session, **values)


def test_receipt_changes_only_selected_balance_and_records_history(db):
    with db.begin() as session:
        operation = receipt(session)
        operation_id = operation.id
    with db() as session:
        north = session.get(StockBalance, (demo_id("SKU-101"), demo_id("north")))
        south = session.get(StockBalance, (demo_id("SKU-101"), demo_id("south")))
        assert (north.on_hand, north.reserved_fbs, north.available) == (13, 0, 13)
        assert (south.on_hand, south.reserved_fbs) == (6, 0)
        operation = session.get(Operation, operation_id)
        event = session.scalar(select(StockEvent).where(StockEvent.operation_id == operation_id))
        assert (operation.kind, operation.status, operation.qty) == ("receipt", "completed", 3)
        assert operation.source_warehouse_id is None
        assert operation.destination_warehouse_id == demo_id("north")
        assert (event.event_type, event.operation_status, event.sku, event.qty) == (
            "receipt_completed", "completed", "SKU-101", 3,
        )
        assert event.product_id == demo_id("SKU-101")
        assert event.from_warehouse_id is None
        assert event.to_warehouse_id == demo_id("north")
        assert (event.from_name, event.to_name) == ("Приход", "Север")
        assert event.created_at.tzinfo is not None
        assert operation.created_at.tzinfo is not None
        assert session.scalar(select(func.count()).select_from(Operation)) == 5
        assert session.scalar(select(func.count()).select_from(StockEvent)) == 5


def test_identical_receipt_replay_preserves_id_and_all_rows(db, stock_snapshot):
    with db.begin() as session:
        operation_id = receipt(session).id
    before = stock_snapshot()
    with db.begin() as session:
        assert receipt(session).id == operation_id
    assert stock_snapshot() == before


@pytest.mark.parametrize("changes", [
    {"qty": 4},
    {"product_id": demo_id("SKU-102")},
    {"warehouse_id": demo_id("south")},
])
def test_same_key_with_changed_payload_is_conflict(db, stock_snapshot, changes):
    with db.begin() as session:
        receipt(session)
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, **changes)
    assert caught.value.status_code == 409
    assert stock_snapshot() == before


@pytest.mark.parametrize("qty", [0, -1, 1.5, True, False, "3", None, 2147483648])
def test_receipt_rejects_invalid_quantity_without_effects(db, stock_snapshot, qty):
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, qty=qty)
    assert caught.value.status_code == 422
    assert stock_snapshot() == before


@pytest.mark.parametrize("key", ["", "   ", "a" * 201, None, 17])
def test_receipt_rejects_invalid_key_without_effects(db, stock_snapshot, key):
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, idempotency_key=key)
    assert caught.value.status_code == 422
    assert stock_snapshot() == before


@pytest.mark.parametrize("field", ["product_id", "warehouse_id"])
def test_receipt_rejects_unknown_reference(db, stock_snapshot, field):
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, **{field: uuid4()})
    assert caught.value.status_code == 404
    assert stock_snapshot() == before


def test_replay_after_archive_succeeds_but_new_receipt_is_rejected(db, stock_snapshot):
    with db.begin() as session:
        operation_id = receipt(session).id
    with db.begin() as session:
        warehouse = session.get(Warehouse, demo_id("north"))
        warehouse.archived_at = datetime.now(timezone.utc)
        warehouse.name = "Архивный Север"
    before = stock_snapshot()
    with db.begin() as session:
        assert receipt(session).id == operation_id
    assert stock_snapshot() == before
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, idempotency_key="test:new-after-archive")
    assert caught.value.status_code == 409
    assert stock_snapshot() == before


def test_balance_overflow_is_conflict_with_no_partial_effects(db, stock_snapshot):
    before = stock_snapshot()
    with pytest.raises(DomainError) as caught:
        with db.begin() as session:
            receipt(session, qty=2147483647)
    assert caught.value.status_code == 409
    assert stock_snapshot() == before


@pytest.mark.parametrize("sku", ["SKU-101", "SKU-103"])
def test_event_failure_rolls_back_existing_or_new_balance(db, stock_snapshot, monkeypatch, sku):
    before = stock_snapshot()

    def fail_event(session, operation, event_type, warehouses):
        # The failure occurs after the service has flushed the balance and operation.
        assert session.get(Operation, operation.id) is operation
        balance = session.get(StockBalance, (demo_id(sku), demo_id("north")))
        assert balance.on_hand == (13 if sku == "SKU-101" else 3)
        raise RuntimeError("Injected event failure")

    monkeypatch.setattr(stock, "add_event", fail_event)
    with pytest.raises(RuntimeError, match="Injected event failure"):
        with db.begin() as session:
            receipt(session, product_id=demo_id(sku))
    assert stock_snapshot() == before


def test_caller_rollback_undoes_completed_receipt_and_allows_retry(db, stock_snapshot):
    before = stock_snapshot()
    with pytest.raises(RuntimeError, match="Caller aborted"):
        with db.begin() as session:
            receipt(session)
            raise RuntimeError("Caller aborted")
    assert stock_snapshot() == before
    with db.begin() as session:
        operation_id = receipt(session).id
    with db() as session:
        assert session.get(Operation, operation_id).status == "completed"
        assert session.get(StockBalance, (demo_id("SKU-101"), demo_id("north"))).on_hand == 13


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_receipts_create_missing_balance_once(db, same_key):
    barrier = Barrier(2)

    def worker(index):
        with db.begin() as session:
            backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
            barrier.wait(timeout=10)
            operation = receipt(
                session,
                product_id=demo_id("SKU-103"),
                idempotency_key="test:concurrent" if same_key else f"test:concurrent:{index}",
            )
            operation_id = operation.id
        return operation_id, backend_pid

    with db() as session:
        assert session.get(StockBalance, (demo_id("SKU-103"), demo_id("north"))) is None
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, index) for index in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert len({pid for _, pid in results}) == 2
    effects = 1 if same_key else 2
    assert len({operation_id for operation_id, _ in results}) == effects
    with db() as session:
        balance = session.get(StockBalance, (demo_id("SKU-103"), demo_id("north")))
        assert (balance.on_hand, balance.reserved_fbs) == (3 * effects, 0)
        assert session.scalar(select(func.count()).select_from(StockBalance).where(
            StockBalance.product_id == demo_id("SKU-103"),
            StockBalance.warehouse_id == demo_id("north"),
        )) == 1
        assert session.scalar(select(func.count()).select_from(Operation)) == 4 + effects
        assert session.scalar(select(func.count()).select_from(StockEvent)) == 4 + effects
