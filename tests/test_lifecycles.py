"""T02–T07: inventory moves only at the specified lifecycle boundary."""
import pytest

from app.services.operations import (
    cancel_fbs, create_transfer, dispatch_transfer, receive_fbo, receive_transfer,
    reserve_fbs, ship_fbo, ship_fbs,
)
from app.services.stock import DomainError, MAX_QUANTITY, receive_stock
from tests.helpers import NORTH, SOUTH, P101, P102, P103, balance, events, run, snapshot, transit


def reserve(db, qty, key="reserve", product_id=P101, warehouse_id=NORTH):
    return run(db, reserve_fbs, product_id=product_id, warehouse_id=warehouse_id,
               qty=qty, customer="Клиент", idempotency_key=key)


def transfer(db, qty, key="transfer", product_id=P101, source=NORTH, destination=SOUTH):
    return run(db, create_transfer, product_id=product_id, source_warehouse_id=source,
               destination_warehouse_id=destination, qty=qty, idempotency_key=key)


def test_t02_fbs_reserve_ship_and_repeat(db):
    operation = reserve(db, 4)
    assert operation.status == "reserved"
    assert balance(db) == (10, 4, 6)
    assert transit(db, "fbs") == 0
    assert run(db, ship_fbs, operation.id).status == "shipped"
    assert balance(db) == (6, 0, 6)
    assert transit(db, "fbs") == 4
    before = snapshot(db)
    assert run(db, ship_fbs, operation.id).id == operation.id
    assert snapshot(db) == before
    assert {event.event_type for event in events(db, operation.id)} == {"fbs_reserved", "fbs_shipped"}
    with pytest.raises(DomainError) as error:
        run(db, cancel_fbs, operation.id)
    assert error.value.status_code == 409
    assert snapshot(db) == before


def test_t03_cancel_fbs_frees_only_reserve_and_forbids_ship(db):
    operation = reserve(db, 3)
    assert run(db, cancel_fbs, operation.id).status == "cancelled"
    assert balance(db) == (10, 0, 10)
    assert transit(db, "fbs") == 0
    before = snapshot(db)
    run(db, cancel_fbs, operation.id)
    with pytest.raises(DomainError) as error:
        run(db, ship_fbs, operation.id)
    assert error.value.status_code == 409
    assert snapshot(db) == before
    assert {event.event_type for event in events(db, operation.id)} == {"fbs_reserved", "fbs_cancelled"}


def test_t04_transfer_draft_dispatch_receive_and_all_repeats(db):
    operation = transfer(db, 3)
    assert operation.status == "draft"
    assert balance(db) == (10, 0, 10)
    assert balance(db, warehouse_id=SOUTH) == (6, 0, 6)
    assert transit(db, "transfer") == 0
    draft_events = events(db, operation.id)
    assert len(draft_events) == 1
    assert (draft_events[0].event_type, draft_events[0].operation_status) == ("transfer_created", "draft")
    before = snapshot(db)
    assert transfer(db, 3).id == operation.id
    assert snapshot(db) == before
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, receive_transfer, operation.id)
    assert error.value.status_code == 409
    assert snapshot(db) == before
    assert run(db, dispatch_transfer, operation.id).status == "in_transit"
    assert balance(db) == (7, 0, 7)
    assert balance(db, warehouse_id=SOUTH) == (6, 0, 6)
    assert transit(db, "transfer") == 3
    before = snapshot(db)
    run(db, dispatch_transfer, operation.id)
    assert snapshot(db) == before
    assert run(db, receive_transfer, operation.id).status == "received"
    assert balance(db) == (7, 0, 7)
    assert balance(db, warehouse_id=SOUTH) == (9, 0, 9)
    assert transit(db, "transfer") == 0
    before = snapshot(db)
    assert run(db, dispatch_transfer, operation.id).status == "received"
    run(db, receive_transfer, operation.id)
    assert snapshot(db) == before
    assert {event.event_type for event in events(db, operation.id)} == {
        "transfer_created", "transfer_dispatched", "transfer_received"}
    assert len(events(db, operation.id)) == 3


def test_t05_fbo_receipt_never_returns_stock_to_ff(db):
    operation = run(db, ship_fbo, product_id=P102, warehouse_id=NORTH, qty=3,
                    marketplace="Маркетплейс", idempotency_key="fbo")
    assert operation.status == "in_transit"
    assert balance(db, product_id=P102) == (2, 0, 2)
    assert transit(db, "fbo") == 3
    assert run(db, receive_fbo, operation.id).status == "received"
    assert balance(db, product_id=P102) == (2, 0, 2)
    assert transit(db, "fbo") == 0
    before = snapshot(db)
    run(db, receive_fbo, operation.id)
    assert snapshot(db) == before
    assert {event.event_type for event in events(db, operation.id)} == {"fbo_shipped", "fbo_received"}


def test_t06_local_shortage_never_uses_other_warehouse(db):
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        reserve(db, 1, product_id=P103)
    assert error.value.status_code == 409
    assert error.value.details == {"available_here": 0, "requested": 1}
    assert snapshot(db) == before
    operation = transfer(db, 2, product_id=P103, source=SOUTH, destination=NORTH)
    run(db, dispatch_transfer, operation.id)
    assert balance(db, P103, NORTH) == (0, 0, 0)
    assert balance(db, P103, SOUTH) == (2, 0, 2)
    assert transit(db, "transfer") == 2
    run(db, receive_transfer, operation.id)
    assert balance(db, P103, NORTH) == (2, 0, 2)
    assert balance(db, P103, SOUTH) == (2, 0, 2)
    assert transit(db, "transfer") == 0
    reserve(db, 1, product_id=P103)
    assert balance(db, P103, NORTH) == (2, 1, 1)


@pytest.mark.parametrize("kind", ["transfer", "fbo"])
def test_t07_other_shipments_can_only_spend_unreserved_stock(db, kind):
    reserve(db, 8)
    assert balance(db) == (10, 8, 2)
    if kind == "transfer":
        too_large = transfer(db, 3)
        before = snapshot(db)
        with pytest.raises(DomainError) as error:
            run(db, dispatch_transfer, too_large.id)
    else:
        before = snapshot(db)
        with pytest.raises(DomainError) as error:
            run(db, ship_fbo, product_id=P101, warehouse_id=NORTH, qty=3,
                marketplace="Маркетплейс", idempotency_key="too-large")
    assert error.value.status_code == 409
    assert error.value.details == {"available_here": 2, "requested": 3}
    assert snapshot(db) == before
    if kind == "transfer":
        allowed = transfer(db, 2, key="allowed")
        run(db, dispatch_transfer, allowed.id)
    else:
        run(db, ship_fbo, product_id=P101, warehouse_id=NORTH, qty=2,
            marketplace="Маркетплейс", idempotency_key="allowed")
    assert balance(db) == (8, 8, 0)
    assert transit(db, kind) == 2


def test_transfer_to_itself_is_rejected_without_effect(db):
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        transfer(db, 1, destination=NORTH)
    assert error.value.status_code == 422
    assert snapshot(db) == before


def test_transfer_draft_does_not_require_or_create_source_balance(db):
    before = snapshot(db)
    operation = transfer(db, 1, product_id=P103)
    after_create = snapshot(db)
    assert after_create["stock_balances"] == before["stock_balances"]
    assert transit(db, "transfer") == 0
    assert len(events(db, operation.id)) == 1
    with pytest.raises(DomainError) as error:
        run(db, dispatch_transfer, operation.id)
    assert error.value.status_code == 409
    assert error.value.details == {"available_here": 0, "requested": 1}
    assert snapshot(db) == after_create


def test_dispatch_rechecks_availability_after_draft_creation(db):
    operation = transfer(db, 7)
    reservation = reserve(db, 4)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, dispatch_transfer, operation.id)
    assert error.value.status_code == 409
    assert error.value.details == {"available_here": 6, "requested": 7}
    assert snapshot(db) == before
    run(db, cancel_fbs, reservation.id)
    assert run(db, dispatch_transfer, operation.id).status == "in_transit"
    assert balance(db) == (3, 0, 3)
    assert transit(db, "transfer") == 7


def test_fbs_ship_and_cancel_preserve_another_orders_reserve(db):
    first = reserve(db, 3, key="first")
    second = reserve(db, 4, key="second")
    assert balance(db) == (10, 7, 3)
    run(db, ship_fbs, first.id)
    assert balance(db) == (7, 4, 3)
    assert transit(db, "fbs") == 3
    run(db, cancel_fbs, second.id)
    assert balance(db) == (7, 0, 7)
    assert transit(db, "fbs") == 3
    assert len(events(db, first.id)) == len(events(db, second.id)) == 2


def test_fbo_local_shortage_does_not_use_other_warehouses_stock(db):
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, ship_fbo, product_id=P103, warehouse_id=NORTH, qty=1,
            marketplace="Маркетплейс", idempotency_key="local-shortage")
    assert error.value.status_code == 409
    assert error.value.details == {"available_here": 0, "requested": 1}
    assert snapshot(db) == before


def test_transfer_receive_overflow_preserves_transit_and_history(db):
    operation = transfer(db, 3)
    run(db, dispatch_transfer, operation.id)
    run(db, receive_stock, product_id=P101, warehouse_id=SOUTH,
        qty=MAX_QUANTITY - 6, idempotency_key="fill-destination")
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, receive_transfer, operation.id)
    assert error.value.status_code == 409
    assert snapshot(db) == before
    assert balance(db) == (7, 0, 7)
    assert balance(db, warehouse_id=SOUTH) == (MAX_QUANTITY, 0, MAX_QUANTITY)
    assert transit(db, "transfer") == 3
