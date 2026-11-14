"""Direct read-model behavior using real PostgreSQL, without an HTTP boundary."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import event, text

from app.services.inventory import get_inventory, list_history, list_operations
from app.services.operations import (
    cancel_fbs,
    create_transfer,
    dispatch_transfer,
    receive_fbo,
    receive_transfer,
    reserve_fbs,
    ship_fbo,
    ship_fbs,
)
from app.services.warehouses import archive_warehouse, create_warehouse, rename_warehouse
from tests.helpers import NORTH, P101, P102, P103, SOUTH, run


def read_snapshot(db, reader=get_inventory):
    """Exercise the documented caller-owned snapshot using a fresh session."""
    with db.begin() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return reader(session)


def test_inventory_without_products_or_balances_is_empty(empty_db):
    expected = {
        "products": [],
        "warehouses": [],
        "balances": [],
        "totals": {
            "on_hand": 0,
            "reserved_fbs": 0,
            "available": 0,
            "transit_ff": 0,
            "transit_fbo": 0,
            "transit_fbs": 0,
        },
        "transit": {"ff": [], "fbo": [], "fbs": []},
    }
    assert read_snapshot(empty_db) == expected
    warehouse = run(empty_db, create_warehouse, "Пустой")
    report = read_snapshot(empty_db)
    assert report["products"] == report["balances"] == []
    assert report["totals"] == expected["totals"]
    assert report["transit"] == expected["transit"]
    assert [row["id"] for row in report["warehouses"]] == [warehouse.id]


def test_inventory_keeps_zero_cells_and_archived_warehouses(db, stock_snapshot):
    warehouse = run(db, create_warehouse, "Пустой")
    run(db, archive_warehouse, SOUTH)
    before = stock_snapshot()
    report = read_snapshot(db)
    after = stock_snapshot()
    assert after == before, "Reading zero cells must not create stored balances"

    cells = {(row["product_id"], row["warehouse_id"]): row for row in report["balances"]}
    assert set(cells) == {
        (product_id, warehouse_id)
        for product_id in (P101, P102, P103)
        for warehouse_id in (NORTH, SOUTH, warehouse.id)
    }
    assert {(product_id, warehouse_id): cell["on_hand"]
            for (product_id, warehouse_id), cell in cells.items()} == {
        (P101, NORTH): 10, (P101, SOUTH): 6, (P101, warehouse.id): 0,
        (P102, NORTH): 5, (P102, SOUTH): 0, (P102, warehouse.id): 0,
        (P103, NORTH): 0, (P103, SOUTH): 4, (P103, warehouse.id): 0,
    }
    assert all(cell["reserved_fbs"] == 0 and cell["available"] == cell["on_hand"]
               for cell in cells.values())
    assert all(cell["archived"] == (cell["warehouse_id"] == SOUTH) for cell in cells.values())
    warehouses = {row["id"]: row for row in report["warehouses"]}
    assert warehouses[SOUTH]["name"] == "Юг"
    assert warehouses[SOUTH]["archived"] is True
    assert warehouses[SOUTH]["archived_at"].utcoffset() == timedelta(0)
    assert warehouses[warehouse.id]["archived"] is False
    assert warehouses[warehouse.id]["archived_at"] is None
    assert report["products"] == [
        {"id": P101, "sku": "SKU-101", "name": "Органайзер"},
        {"id": P102, "sku": "SKU-102", "name": "Контейнер"},
        {"id": P103, "sku": "SKU-103", "name": "Корзина"},
    ]
    assert report["totals"] == {
        "on_hand": 25, "reserved_fbs": 0, "available": 25,
        "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 0,
    }


def test_inventory_separates_three_transits_and_preserves_quantity(db):
    transfer = run(
        db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
        destination_warehouse_id=SOUTH, qty=3, idempotency_key="report-transfer",
    )
    run(db, dispatch_transfer, transfer.id)
    fbs = run(
        db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=2,
        customer="Клиент отчёта", idempotency_key="report-fbs",
    )
    run(db, ship_fbs, fbs.id)
    fbo = run(
        db, ship_fbo, product_id=P102, warehouse_id=NORTH, qty=1,
        marketplace="Маркетплейс отчёта", idempotency_key="report-fbo",
    )
    run(
        db, reserve_fbs, product_id=P101, warehouse_id=SOUTH, qty=1,
        customer="Неотправленный заказ", idempotency_key="report-reserved",
    )
    run(
        db, create_transfer, product_id=P103, source_warehouse_id=SOUTH,
        destination_warehouse_id=NORTH, qty=2, idempotency_key="report-draft",
    )
    cancelled = run(
        db, reserve_fbs, product_id=P103, warehouse_id=SOUTH, qty=1,
        customer="Отменённый заказ", idempotency_key="report-cancelled",
    )
    run(db, cancel_fbs, cancelled.id)

    report = read_snapshot(db)
    assert report["totals"] == {
        "on_hand": 19, "reserved_fbs": 1, "available": 18,
        "transit_ff": 3, "transit_fbo": 1, "transit_fbs": 2,
    }
    assert {kind: [(row["id"], row["qty"]) for row in rows]
            for kind, rows in report["transit"].items()} == {
        "ff": [(transfer.id, 3)], "fbo": [(fbo.id, 1)], "fbs": [(fbs.id, 2)],
    }
    assert report["transit"]["ff"][0]["source_name"] == "Север"
    assert report["transit"]["ff"][0]["destination_name"] == "Юг"
    assert report["transit"]["fbo"][0]["destination_name"] == "Маркетплейс отчёта"
    assert report["transit"]["fbs"][0]["destination_name"] == "Клиент отчёта"
    assert sum(report["totals"][key] for key in (
        "on_hand", "transit_ff", "transit_fbo", "transit_fbs",
    )) == 25
    for product_id, expected in ((P101, 16), (P102, 5), (P103, 4)):
        physical = sum(row["on_hand"] for row in report["balances"] if row["product_id"] == product_id)
        in_transit = sum(
            row["qty"] for rows in report["transit"].values() for row in rows
            if row["product_id"] == product_id
        )
        assert physical + in_transit == expected

    run(db, receive_transfer, transfer.id)
    run(db, receive_fbo, fbo.id)
    completed = read_snapshot(db)
    assert completed["totals"] == {
        "on_hand": 22, "reserved_fbs": 1, "available": 21,
        "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 2,
    }
    assert completed["transit"]["ff"] == completed["transit"]["fbo"] == []
    assert [row["id"] for row in completed["transit"]["fbs"]] == [fbs.id]
    # One unit has been accepted outside FF stock; a reserve is already physical.
    assert completed["totals"]["on_hand"] + completed["totals"]["transit_fbs"] + fbo.qty == 25


def test_operation_names_are_current_and_history_preserves_each_event_snapshot(db):
    seed_history = read_snapshot(db, list_history)
    transfer = run(
        db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
        destination_warehouse_id=SOUTH, qty=3, idempotency_key="report-renamed",
    )
    run(db, rename_warehouse, NORTH, "Север новый")
    run(db, dispatch_transfer, transfer.id)
    run(db, rename_warehouse, SOUTH, "Юг новый")
    run(db, archive_warehouse, SOUTH)
    run(db, receive_transfer, transfer.id)

    operations = read_snapshot(db, list_operations)
    assert len(operations) == 5
    projected = next(row for row in operations if row["id"] == transfer.id)
    assert projected == {
        "id": transfer.id, "kind": "transfer", "status": "received",
        "product_id": P101, "sku": "SKU-101", "product_name": "Органайзер", "qty": 3,
        "source_warehouse_id": NORTH, "destination_warehouse_id": SOUTH,
        "source_name": "Север новый", "destination_name": "Юг новый",
        "external_party": None, "idempotency_key": "report-renamed",
        "created_at": projected["created_at"], "updated_at": projected["updated_at"],
    }
    assert projected["created_at"].utcoffset() == timedelta(0)
    assert projected["updated_at"] >= projected["created_at"]
    assert all(
        row["source_name"] == "Приход" and row["source_warehouse_id"] is None
        for row in operations if row["kind"] == "receipt"
    )
    assert all(
        left["created_at"] >= right["created_at"]
        for left, right in zip(operations, operations[1:])
    )

    history = read_snapshot(db, list_history)
    assert len(history) == 7
    old_ids = {row["id"] for row in seed_history}
    assert {row["id"]: row for row in history if row["id"] in old_ids} == {
        row["id"]: row for row in seed_history
    }
    transfer_events = {row["event_type"]: row for row in history if row["operation_id"] == transfer.id}
    assert set(transfer_events) == {"transfer_created", "transfer_dispatched", "transfer_received"}
    assert {kind: (row["operation_status"], row["from_name"], row["to_name"])
            for kind, row in transfer_events.items()} == {
        "transfer_created": ("draft", "Север", "Юг"),
        "transfer_dispatched": ("in_transit", "Север новый", "Юг"),
        "transfer_received": ("received", "Север новый", "Юг новый"),
    }
    for row in transfer_events.values():
        assert row["product_id"] == P101 and row["sku"] == "SKU-101" and row["qty"] == 3
        assert row["from_warehouse_id"] == NORTH and row["to_warehouse_id"] == SOUTH
        assert row["created_at"].utcoffset() == timedelta(0)
    assert all(left["created_at"] >= right["created_at"] for left, right in zip(history, history[1:]))


def test_inventory_uses_caller_repeatable_read_snapshot_during_concurrent_shipment(db):
    operation = run(
        db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
        customer="Параллельная отправка", idempotency_key="report-snapshot",
    )
    engine = db.kw["bind"]
    triggered = False
    writer_pid = None

    def ship_in_separate_transaction():
        with db.begin() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            ship_fbs(session, operation.id)
        return pid

    def ship_after_balances(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered, writer_pid
        if not triggered and "SELECT" in statement and "FROM stock_balances" in statement:
            triggered = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer_pid = pool.submit(ship_in_separate_transaction).result(timeout=10)

    event.listen(engine, "after_cursor_execute", ship_after_balances)
    try:
        with db.begin() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            reader_pid = session.scalar(text("SELECT pg_backend_pid()"))
            report = get_inventory(session)
    finally:
        event.remove(engine, "after_cursor_execute", ship_after_balances)

    assert triggered, "The shipment must commit between balance and transit reads"
    assert writer_pid is not None and reader_pid != writer_pid
    assert report["totals"] == {
        "on_hand": 25, "reserved_fbs": 4, "available": 21,
        "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 0,
    }
    assert report["transit"]["fbs"] == []
    current = read_snapshot(db)
    assert current["totals"] == {
        "on_hand": 21, "reserved_fbs": 0, "available": 21,
        "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 4,
    }
    assert [row["id"] for row in current["transit"]["fbs"]] == [operation.id]
