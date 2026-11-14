"""T11: race real independent PostgreSQL connections, not shared sessions."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text

from app.models import Operation, StockBalance, Warehouse
from app.services.operations import (
    cancel_fbs, create_transfer, dispatch_transfer, receive_fbo, receive_transfer,
    reserve_fbs, ship_fbo, ship_fbs,
)
from app.services.stock import DomainError
from app.services.warehouses import archive_warehouse, rename_warehouse
from tests.helpers import NORTH, SOUTH, P101, balance, events, run, snapshot, transit
from tests.test_idempotency_and_rollback import creation


def race(db, first, second):
    barrier = Barrier(2, timeout=10)

    def worker(command):
        backend_pid = None
        try:
            with db.begin() as session:
                backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                barrier.wait()
                operation = command(session)
                operation_id = operation.id
            return backend_pid, 200, operation_id
        except DomainError as error:
            return backend_pid, error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, command) for command in (first, second)]
        results = [future.result(timeout=45) for future in futures]
    assert len({result[0] for result in results}) == 2
    return results


def test_t11_concurrent_reserves_never_overreserve(db):
    def command(key):
        return lambda session: reserve_fbs(session, product_id=P101, warehouse_id=NORTH,
            qty=7, customer="Клиент", idempotency_key=key)
    results = race(db, command("race-a"), command("race-b"))
    assert sorted(result[1] for result in results) == [200, 409]
    assert balance(db) == (10, 7, 3)
    winner = next(result[2] for result in results if result[1] == 200)
    assert len(events(db, winner)) == 1
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Operation).where(Operation.kind == "fbs")) == 1


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_concurrent_identical_create_has_one_id_and_one_event(db, kind):
    service, payload = creation(kind, key="concurrent", qty=2)
    command = lambda session: service(session, **payload)
    results = race(db, command, command)
    assert [result[1] for result in results] == [200, 200]
    assert results[0][2] == results[1][2]
    assert len(events(db, results[0][2])) == 1
    assert balance(db) == {
        "transfer": (10, 0, 10),
        "fbs": (10, 2, 8), "fbo": (8, 0, 8),
    }[kind]


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_concurrent_conflicting_create_key_returns_domain_conflict(db, kind):
    service, first_payload = creation(kind, key="conflicting-race", qty=2)
    _, second_payload = creation(kind, key="conflicting-race", qty=3)
    results = race(db, lambda session: service(session, **first_payload),
                   lambda session: service(session, **second_payload))
    assert sorted(result[1] for result in results) == [200, 409]
    operation_id = next(result[2] for result in results if result[1] == 200)
    with db() as session:
        quantity = session.get(Operation, operation_id).qty
    assert quantity in {2, 3}
    assert balance(db) == {
        "transfer": (10, 0, 10),
        "fbs": (10, quantity, 10 - quantity),
        "fbo": (10 - quantity, 0, 10 - quantity),
    }[kind]
    assert len(events(db, operation_id)) == 1


def test_concurrent_fbs_and_fbo_cannot_spend_the_same_available_stock(db):
    results = race(
        db,
        lambda session: reserve_fbs(session, product_id=P101, warehouse_id=NORTH, qty=7,
            customer="Клиент", idempotency_key="competing-fbs"),
        lambda session: ship_fbo(session, product_id=P101, warehouse_id=NORTH, qty=7,
            marketplace="Маркетплейс", idempotency_key="competing-fbo"),
    )
    assert sorted(result[1] for result in results) == [200, 409]
    fbs_won = results[0][1] == 200
    assert balance(db) == ((10, 7, 3) if fbs_won else (3, 0, 3))
    assert transit(db, "fbo") == (0 if fbs_won else 7)
    winner = next(result[2] for result in results if result[1] == 200)
    assert len(events(db, winner)) == 1


@pytest.mark.parametrize("action", ["dispatch", "receive", "ship", "cancel", "fbo_receive"])
def test_concurrent_duplicate_transition_has_one_stock_effect(db, action):
    if action in {"dispatch", "receive"}:
        operation = run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                        destination_warehouse_id=SOUTH, qty=2, idempotency_key="transition")
        if action == "receive":
            run(db, dispatch_transfer, operation.id)
        service = dispatch_transfer if action == "dispatch" else receive_transfer
    elif action in {"ship", "cancel"}:
        operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=2,
                        customer="Клиент", idempotency_key="transition")
        service = ship_fbs if action == "ship" else cancel_fbs
    else:
        operation = run(db, ship_fbo, product_id=P101, warehouse_id=NORTH, qty=2,
                        marketplace="Маркетплейс", idempotency_key="transition")
        service = receive_fbo
    command = lambda session: service(session, operation.id)
    results = race(db, command, command)
    assert [result[1] for result in results] == [200, 200]
    assert {result[2] for result in results} == {operation.id}
    assert balance(db) == ((10, 0, 10) if action == "cancel" else (8, 0, 8))
    assert balance(db, warehouse_id=SOUTH) == ((8, 0, 8) if action == "receive" else (6, 0, 6))
    assert transit(db, "transfer") == (2 if action == "dispatch" else 0)
    assert transit(db, "fbs") == (2 if action == "ship" else 0)
    assert transit(db, "fbo") == 0
    assert len(events(db, operation.id)) == (3 if action == "receive" else 2)


def test_concurrent_ship_and_cancel_choose_one_terminal_state(db):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=2,
                    customer="Клиент", idempotency_key="ship-or-cancel")
    results = race(db, lambda session: ship_fbs(session, operation.id),
                   lambda session: cancel_fbs(session, operation.id))
    assert sorted(result[1] for result in results) == [200, 409]
    with db() as session:
        status = session.get(Operation, operation.id).status
    assert status in {"shipped", "cancelled"}
    assert balance(db) == ((8, 0, 8) if status == "shipped" else (10, 0, 10))
    assert transit(db, "fbs") == (2 if status == "shipped" else 0)
    assert len(events(db, operation.id)) == 2


def test_opposite_transfers_lock_warehouses_in_consistent_order(db):
    first = run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                destination_warehouse_id=SOUTH, qty=3, idempotency_key="north-south")
    second = run(db, create_transfer, product_id=P101, source_warehouse_id=SOUTH,
                 destination_warehouse_id=NORTH, qty=3, idempotency_key="south-north")
    results = race(db, lambda session: dispatch_transfer(session, first.id),
                   lambda session: dispatch_transfer(session, second.id))
    assert [result[1] for result in results] == [200, 200]
    assert balance(db) == (7, 0, 7)
    assert balance(db, warehouse_id=SOUTH) == (3, 0, 3)
    assert transit(db, "transfer") == 6
    results = race(db, lambda session: receive_transfer(session, first.id),
                   lambda session: receive_transfer(session, second.id))
    assert [result[1] for result in results] == [200, 200]
    assert balance(db) == (10, 0, 10)
    assert balance(db, warehouse_id=SOUTH) == (6, 0, 6)
    assert transit(db, "transfer") == 0


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_transition_refreshes_preloaded_operation_before_replay(db, kind):
    service, payload = creation(kind, key="stale-operation")
    operation = run(db, service, **payload)
    with db.begin() as stale_session:
        cached = stale_session.get(Operation, operation.id)
        if kind == "transfer":
            run(db, dispatch_transfer, operation.id)
            run(db, receive_transfer, operation.id)
            transition, terminal = dispatch_transfer, "received"
        elif kind == "fbs":
            run(db, ship_fbs, operation.id)
            transition, terminal = ship_fbs, "shipped"
        else:
            run(db, receive_fbo, operation.id)
            transition, terminal = receive_fbo, "received"
        assert cached.status != terminal
        before = snapshot(db)
        repeated = transition(stale_session, operation.id)
        assert repeated.id == operation.id and repeated.status == terminal
    assert snapshot(db) == before


def test_reservation_refreshes_preloaded_balance_after_other_transaction(db):
    with db.begin() as stale_session:
        cached = stale_session.get(StockBalance, (P101, NORTH))
        assert cached.reserved_fbs == 0
        run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=7,
            customer="Первый клиент", idempotency_key="first-reservation")
        before = snapshot(db)
        with pytest.raises(DomainError) as error:
            reserve_fbs(stale_session, product_id=P101, warehouse_id=NORTH, qty=7,
                customer="Второй клиент", idempotency_key="second-reservation")
        assert error.value.status_code == 409
        assert error.value.details == {"available_here": 3, "requested": 7}
    assert snapshot(db) == before
    assert balance(db) == (10, 7, 3)


def wait_for_database_lock(db, future, backend_pid):
    """Observe the worker actually waiting before releasing the owning transaction."""
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if future.done():
            pytest.fail("Command completed before the warehouse lock was released")
        with db() as session:
            wait_type = session.scalar(text(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
            ), {"pid": backend_pid})
        if wait_type == "Lock":
            return
        sleep(0.01)
    pytest.fail("Worker did not reach the expected PostgreSQL lock within five seconds")


@pytest.mark.parametrize("change", ["archive", "rename"])
def test_new_reservation_waits_for_warehouse_change_and_refreshes_cached_fields(db, change):
    worker_ready = Queue(maxsize=1)
    start_command = Event()

    def worker():
        with db.begin() as session:
            cached = session.get(Warehouse, NORTH)
            assert cached.name == "Север" and cached.archived_at is None
            worker_ready.put(session.scalar(text("SELECT pg_backend_pid()")))
            assert start_command.wait(timeout=10)
            operation = reserve_fbs(session, product_id=P101, warehouse_id=NORTH, qty=2,
                customer="Клиент", idempotency_key="warehouse-change")
            # Keep the original object alive so the command must refresh its stale fields.
            assert cached.name == "Новое имя Севера"
            return operation.id

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        backend_pid = worker_ready.get(timeout=10)
        try:
            with db.begin() as writer:
                if change == "archive":
                    archive_warehouse(writer, NORTH)
                else:
                    rename_warehouse(writer, NORTH, "Новое имя Севера")
                start_command.set()
                wait_for_database_lock(db, future, backend_pid)
            if change == "archive":
                with pytest.raises(DomainError) as error:
                    future.result(timeout=15)
                assert error.value.status_code == 409
            else:
                operation_id = future.result(timeout=15)
        finally:
            start_command.set()
    if change == "archive":
        assert balance(db) == (10, 0, 10)
        with db() as session:
            assert session.get(Warehouse, NORTH).archived_at is not None
            assert session.scalar(select(func.count()).select_from(Operation).where(Operation.kind == "fbs")) == 0
    else:
        assert balance(db) == (10, 2, 8)
        assert events(db, operation_id)[0].from_name == "Новое имя Севера"
