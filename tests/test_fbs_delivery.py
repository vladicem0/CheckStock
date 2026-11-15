"""FBS delivery completes transit without another warehouse movement."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Operation
from app.services import operations
from app.services.inventory import get_inventory
from app.services.operations import (
    cancel_fbs, create_transfer, deliver_fbs, reserve_fbs, ship_fbo, ship_fbs,
)
from app.services.stock import DomainError
from app.services.warehouses import archive_warehouse, rename_warehouse
from tests.helpers import NORTH, SOUTH, P101, balance, events, run, snapshot, transit
from tests.test_concurrency import race


def reservation(db, *, key="delivery", qty=4):
    return run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=qty,
               customer="Клиент", idempotency_key=key)


def shipment(db):
    operation = reservation(db)
    return run(db, ship_fbs, operation.id)


def test_delivery_completes_transit_and_records_one_complete_event(db):
    operation = shipment(db)
    assert balance(db) == (6, 0, 6)
    assert transit(db, "fbs") == 4
    before = snapshot(db)

    delivered = run(db, deliver_fbs, operation.id)
    assert (delivered.id, delivered.status) == (operation.id, "delivered")
    after = snapshot(db)
    for table in ("products", "warehouses", "stock_balances"):
        assert after[table] == before[table]
    assert balance(db) == (6, 0, 6)
    assert transit(db, "fbs") == 0

    rows = events(db, operation.id)
    assert len(rows) == 3
    assert {event.event_type: event.operation_status for event in rows} == {
        "fbs_reserved": "reserved", "fbs_shipped": "shipped", "fbs_delivered": "delivered",
    }
    event = next(event for event in rows if event.event_type == "fbs_delivered")
    assert (event.operation_id, event.product_id, event.sku, event.qty) == (
        operation.id, P101, "SKU-101", 4,
    )
    assert event.from_warehouse_id == NORTH and event.to_warehouse_id is None
    assert (event.from_name, event.to_name) == ("Север", "Клиент")
    assert event.created_at.utcoffset().total_seconds() == 0

    with db() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        inventory = get_inventory(session)
    assert inventory["totals"] == {
        "on_hand": 21, "reserved_fbs": 0, "available": 21,
        "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 0,
    }
    assert inventory["transit"]["fbs"] == []


def test_delivery_and_prior_command_replays_preserve_every_row(db):
    operation = shipment(db)
    run(db, deliver_fbs, operation.id)
    run(db, archive_warehouse, NORTH)
    before = snapshot(db)

    for repeated in (
        run(db, deliver_fbs, operation.id),
        run(db, ship_fbs, operation.id),
        reservation(db),
    ):
        assert (repeated.id, repeated.status) == (operation.id, "delivered")
    assert snapshot(db) == before
    with pytest.raises(DomainError) as caught:
        run(db, cancel_fbs, operation.id)
    assert caught.value.status_code == 409
    assert snapshot(db) == before


@pytest.mark.parametrize("status", ["reserved", "cancelled"])
def test_delivery_rejects_unshipped_reservations_without_effects(db, status):
    operation = reservation(db)
    if status == "cancelled":
        run(db, cancel_fbs, operation.id)
    before = snapshot(db)
    with pytest.raises(DomainError) as caught:
        run(db, deliver_fbs, operation.id)
    assert caught.value.status_code == 409
    assert snapshot(db) == before


@pytest.mark.parametrize("kind", ["receipt", "transfer", "fbo"])
def test_delivery_rejects_other_operation_kinds(db, kind):
    if kind == "receipt":
        with db() as session:
            operation_id = session.scalar(select(Operation.id).where(Operation.kind == "receipt"))
    elif kind == "transfer":
        operation_id = run(
            db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
            destination_warehouse_id=SOUTH, qty=2, idempotency_key="other-kind",
        ).id
    else:
        operation_id = run(db, ship_fbo, product_id=P101, warehouse_id=NORTH, qty=2,
                           marketplace="Маркетплейс", idempotency_key="other-kind").id
    before = snapshot(db)
    with pytest.raises(DomainError) as caught:
        run(db, deliver_fbs, operation_id)
    assert caught.value.status_code == 409
    assert snapshot(db) == before


def test_delivery_rejects_unknown_operation_without_effects(db):
    before = snapshot(db)
    with pytest.raises(DomainError) as caught:
        run(db, deliver_fbs, uuid4())
    assert caught.value.status_code == 404
    assert snapshot(db) == before


def test_delivery_from_archived_renamed_source_preserves_old_event_names(db):
    operation = shipment(db)
    old_events = snapshot(db)["stock_events"]
    run(db, rename_warehouse, NORTH, name="Север-2")
    run(db, archive_warehouse, NORTH)
    before = snapshot(db)
    run(db, deliver_fbs, operation.id)
    after = snapshot(db)
    assert after["stock_balances"] == before["stock_balances"]
    assert after["warehouses"] == before["warehouses"]
    assert set(old_events) <= set(after["stock_events"])
    rows = events(db, operation.id)
    assert {event.event_type: event.from_name for event in rows} == {
        "fbs_reserved": "Север", "fbs_shipped": "Север", "fbs_delivered": "Север-2",
    }
    assert transit(db, "fbs") == 0


def test_delivery_leaves_another_orders_reserve_untouched(db):
    operation = shipment(db)
    reservation(db, key="other-reserve", qty=2)
    before = snapshot(db)
    run(db, deliver_fbs, operation.id)
    assert snapshot(db)["stock_balances"] == before["stock_balances"]
    assert balance(db) == (6, 2, 4)


@pytest.mark.parametrize("failure_point", ["after_status", "after_event"])
def test_failed_delivery_rolls_back_status_and_history(db, monkeypatch, failure_point):
    operation = shipment(db)
    before = snapshot(db)
    original = operations.add_event

    def fail(session, *args, **kwargs):
        if failure_point == "after_event":
            original(session, *args, **kwargs)
        else:
            session.flush()
        raise RuntimeError("injected delivery failure")

    monkeypatch.setattr(operations, "add_event", fail)
    with pytest.raises(RuntimeError, match="injected delivery failure"):
        run(db, deliver_fbs, operation.id)
    assert snapshot(db) == before
    assert transit(db, "fbs") == 4


@pytest.mark.parametrize("concurrent_action", [deliver_fbs, ship_fbs])
def test_concurrent_delivery_and_replays_have_one_delivery_event(db, concurrent_action):
    operation = shipment(db)
    before = snapshot(db)
    results = race(db, lambda session: deliver_fbs(session, operation.id),
                   lambda session: concurrent_action(session, operation.id))
    assert [result[1] for result in results] == [200, 200]
    assert {result[2] for result in results} == {operation.id}
    with db() as session:
        assert session.get(Operation, operation.id).status == "delivered"
    assert snapshot(db)["stock_balances"] == before["stock_balances"]
    assert transit(db, "fbs") == 0
    assert len(events(db, operation.id)) == 3
    assert sum(event.event_type == "fbs_delivered" for event in events(db, operation.id)) == 1


@pytest.mark.parametrize("replay", [deliver_fbs, ship_fbs])
def test_delivery_replay_refreshes_a_stale_operation(db, replay):
    operation = shipment(db)
    with db.begin() as stale_session:
        cached = stale_session.get(Operation, operation.id)
        assert cached.status == "shipped"
        run(db, deliver_fbs, operation.id)
        before = snapshot(db)
        repeated = replay(stale_session, operation.id)
        assert repeated is cached
        assert repeated.status == "delivered"
    assert snapshot(db) == before
