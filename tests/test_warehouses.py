"""T08/T09: archiving never removes stock or prevents finishing prior work."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import StockBalance, StockEvent, Warehouse
from app.services.operations import (
    cancel_fbs, create_transfer, dispatch_transfer, receive_fbo, receive_transfer,
    reserve_fbs, ship_fbo, ship_fbs,
)
from app.services.stock import DomainError, receive_stock
from app.services.warehouses import archive_warehouse, create_warehouse, rename_warehouse
from tests.helpers import NORTH, SOUTH, P101, P103, balance, events, run, snapshot, transit


def test_t08_archive_preserves_stock_history_and_is_repeatable(db):
    before = snapshot(db)
    warehouse = run(db, archive_warehouse, SOUTH)
    assert warehouse.id == SOUTH
    assert warehouse.name == "Юг"
    assert warehouse.archived_at.tzinfo is not None
    after = snapshot(db)
    for table in ("stock_balances", "stock_events", "operations"):
        assert after[table] == before[table]
    assert run(db, archive_warehouse, SOUTH).archived_at == warehouse.archived_at
    assert snapshot(db) == after
    with db() as session:
        assert session.scalar(select(func.sum(StockBalance.on_hand)).where(
            StockBalance.warehouse_id == SOUTH)) == 10
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 2
    outgoing = run(db, create_transfer, product_id=P103, source_warehouse_id=SOUTH,
                   destination_warehouse_id=NORTH, qty=2, idempotency_key="evacuate")
    run(db, dispatch_transfer, outgoing.id)
    run(db, receive_transfer, outgoing.id)
    assert balance(db, P103, SOUTH) == (2, 0, 2)
    assert balance(db, P103, NORTH) == (2, 0, 2)


@pytest.mark.parametrize("command", ["receipt", "reserve", "fbo", "incoming"])
def test_t08_archived_warehouse_rejects_new_operations(db, command):
    run(db, archive_warehouse, SOUTH)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        if command == "receipt":
            run(db, receive_stock, product_id=P101, warehouse_id=SOUTH, qty=1, idempotency_key="new")
        elif command == "reserve":
            run(db, reserve_fbs, product_id=P101, warehouse_id=SOUTH, qty=1,
                customer="Клиент", idempotency_key="new")
        elif command == "fbo":
            run(db, ship_fbo, product_id=P101, warehouse_id=SOUTH, qty=1,
                marketplace="Маркетплейс", idempotency_key="new")
        else:
            run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                destination_warehouse_id=SOUTH, qty=1, idempotency_key="new")
    assert error.value.status_code == 409
    assert snapshot(db) == before


def test_t08_old_incoming_transfer_can_dispatch_and_receive_after_archive(db):
    operation = run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                    destination_warehouse_id=SOUTH, qty=3, idempotency_key="old")
    run(db, archive_warehouse, SOUTH)
    run(db, dispatch_transfer, operation.id)
    assert balance(db, warehouse_id=SOUTH) == (6, 0, 6)
    run(db, receive_transfer, operation.id)
    assert balance(db, warehouse_id=SOUTH) == (9, 0, 9)
    assert transit(db, "transfer") == 0


@pytest.mark.parametrize("action", ["ship", "cancel"])
def test_t08_old_fbs_can_finish_after_archive(db, action):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=SOUTH, qty=3,
                    customer="Клиент", idempotency_key="old")
    run(db, archive_warehouse, SOUTH)
    run(db, ship_fbs if action == "ship" else cancel_fbs, operation.id)
    assert balance(db, warehouse_id=SOUTH) == ((3, 0, 3) if action == "ship" else (6, 0, 6))
    assert transit(db, "fbs") == (3 if action == "ship" else 0)


def test_old_fbo_can_be_received_after_source_archive(db):
    operation = run(db, ship_fbo, product_id=P101, warehouse_id=SOUTH, qty=2,
                    marketplace="Маркетплейс", idempotency_key="old")
    run(db, archive_warehouse, SOUTH)
    assert run(db, receive_fbo, operation.id).status == "received"
    assert balance(db, warehouse_id=SOUTH) == (4, 0, 4)
    assert transit(db, "fbo") == 0


def test_t09_rename_keeps_old_names_and_ids_but_new_events_use_new_name(db):
    with db() as session:
        old_events = {event.id: event.to_name for event in session.scalars(select(StockEvent))}
    warehouse = run(db, rename_warehouse, NORTH, "Северный склад")
    assert warehouse.id == NORTH
    operation = run(db, receive_stock, product_id=P101, warehouse_id=NORTH,
                    qty=1, idempotency_key="after-rename")
    assert balance(db) == (11, 0, 11)
    with db() as session:
        for event_id, old_name in old_events.items():
            assert session.get(StockEvent, event_id).to_name == old_name
    event = events(db, operation.id)[0]
    assert event.to_warehouse_id == NORTH
    assert event.to_name == "Северный склад"


def test_new_warehouse_has_stable_id_and_independent_stock(db):
    warehouse = run(db, create_warehouse, "Третий ФФ")
    assert warehouse.id not in {NORTH, SOUTH}
    assert warehouse.archived_at is None
    assert balance(db, warehouse_id=warehouse.id) == (0, 0, 0)
    run(db, receive_stock, product_id=P101, warehouse_id=warehouse.id, qty=2, idempotency_key="third")
    assert balance(db, warehouse_id=warehouse.id) == (2, 0, 2)
    assert balance(db) == (10, 0, 10)


@pytest.mark.parametrize("name", [None, 42, "", " \t\n", "x" * 201, "bad\x00name", "bad\ud800name"])
@pytest.mark.parametrize("command", ["create", "rename"])
def test_invalid_name_is_rejected_without_changing_any_rows(db, name, command):
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        if command == "create":
            run(db, create_warehouse, name)
        else:
            run(db, rename_warehouse, NORTH, name)
    assert error.value.status_code == 422
    assert snapshot(db) == before


def test_names_are_trimmed_and_archived_warehouse_can_be_renamed(db):
    warehouse = run(db, create_warehouse, "  Новый ФФ\n")
    assert warehouse.name == "Новый ФФ"
    archived = run(db, archive_warehouse, warehouse.id)
    before = snapshot(db)
    renamed = run(db, rename_warehouse, warehouse.id, "  " + "я" * 200 + "  ")
    assert renamed.id == warehouse.id
    assert renamed.name == "я" * 200
    assert renamed.archived_at == archived.archived_at
    after = snapshot(db)
    for table in ("stock_balances", "stock_events", "operations"):
        assert after[table] == before[table]


@pytest.mark.parametrize("command", [rename_warehouse, archive_warehouse])
def test_unknown_warehouse_returns_404(db, command):
    before = snapshot(db)
    args = (uuid4(), "Имя") if command is rename_warehouse else (uuid4(),)
    with pytest.raises(DomainError) as error:
        run(db, command, *args)
    assert error.value.status_code == 404
    assert snapshot(db) == before


@pytest.mark.parametrize("command", ["create", "rename", "archive"])
def test_warehouse_service_respects_caller_rollback(db, command):
    before = snapshot(db)
    with pytest.raises(RuntimeError, match="caller failure"):
        with db.begin() as session:
            if command == "create":
                create_warehouse(session, "Новый ФФ")
            elif command == "rename":
                rename_warehouse(session, NORTH, "Новое имя")
            else:
                archive_warehouse(session, NORTH)
            session.flush()
            raise RuntimeError("caller failure")
    assert snapshot(db) == before
