"""T10/T12/T13: durable command identity, rollback, and audit facts."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Operation
from app.services import operations
from app.services.operations import (
    cancel_fbs, create_transfer, dispatch_transfer, receive_fbo, receive_transfer,
    reserve_fbs, ship_fbo, ship_fbs,
)
from app.services.stock import DomainError, MAX_QUANTITY, receive_stock
from app.services.warehouses import archive_warehouse
from tests.helpers import NORTH, SOUTH, P101, P102, balance, events, run, snapshot


def creation(kind, key="same", qty=2):
    common = dict(product_id=P101, qty=qty, idempotency_key=key)
    if kind == "receipt":
        return receive_stock, dict(common, warehouse_id=NORTH)
    if kind == "transfer":
        return create_transfer, dict(common, source_warehouse_id=NORTH, destination_warehouse_id=SOUTH)
    if kind == "fbs":
        return reserve_fbs, dict(common, warehouse_id=NORTH, customer="Клиент")
    return ship_fbo, dict(common, warehouse_id=NORTH, marketplace="Маркетплейс")


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_t10_same_create_key_reuses_id_and_changed_quantity_conflicts(db, kind):
    service, payload = creation(kind)
    first = run(db, service, **payload)
    before = snapshot(db)
    assert run(db, service, **payload).id == first.id
    assert snapshot(db) == before
    with pytest.raises(DomainError) as error:
        run(db, service, **dict(payload, qty=3))
    assert error.value.status_code == 409
    assert snapshot(db) == before


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_create_retry_still_succeeds_after_warehouse_archive(db, kind):
    service, payload = creation(kind)
    first = run(db, service, **payload)
    run(db, archive_warehouse, NORTH)
    run(db, archive_warehouse, SOUTH)
    before = snapshot(db)
    assert run(db, service, **payload).id == first.id
    assert snapshot(db) == before


def test_key_cannot_be_reused_for_other_kind_or_external_destination(db):
    first = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=2,
                customer="Клиент А", idempotency_key="shared")
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=2,
            customer="Клиент Б", idempotency_key="shared")
    assert error.value.status_code == 409
    with pytest.raises(DomainError) as error:
        run(db, receive_stock, product_id=P101, warehouse_id=NORTH, qty=2,
            idempotency_key="shared")
    assert error.value.status_code == 409
    assert snapshot(db) == before
    assert len(events(db, first.id)) == 1


@pytest.mark.parametrize("kind,terminal", [
    ("transfer", "received"), ("fbs", "shipped"), ("fbs", "cancelled"), ("fbo", "received"),
])
def test_create_retry_after_completion_returns_current_state(db, kind, terminal):
    service, payload = creation(kind)
    first = run(db, service, **payload)
    if kind == "transfer":
        run(db, dispatch_transfer, first.id)
        run(db, receive_transfer, first.id)
    elif kind == "fbs":
        run(db, ship_fbs if terminal == "shipped" else cancel_fbs, first.id)
    else:
        run(db, receive_fbo, first.id)
    before = snapshot(db)
    repeated = run(db, service, **payload)
    assert repeated.id == first.id and repeated.status == terminal
    assert snapshot(db) == before


@pytest.mark.parametrize("stage", [
    "transfer_create", "reserve", "dispatch", "receive",
    "fbs_ship", "fbs_cancel", "fbo_ship", "fbo_receive",
])
def test_t12_error_after_mutation_rolls_back_balance_status_and_event(db, monkeypatch, stage):
    operation = None
    if stage in {"dispatch", "receive"}:
        operation = run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                        destination_warehouse_id=SOUTH, qty=3, idempotency_key="rollback-transfer")
        if stage == "receive":
            run(db, dispatch_transfer, operation.id)
    elif stage in {"fbs_ship", "fbs_cancel"}:
        service, payload = creation("fbs", key="rollback-fbs")
        operation = run(db, service, **payload)
    elif stage == "fbo_receive":
        service, payload = creation("fbo", key="rollback-fbo")
        operation = run(db, service, **payload)
    before = snapshot(db)

    def failure_after_mutation(session, *args, **kwargs):
        session.flush()  # Force changed balance/status to PostgreSQL before failing.
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(operations, "add_event", failure_after_mutation)
    with pytest.raises(RuntimeError, match="injected event failure"):
        if stage in {"transfer_create", "reserve", "fbo_ship"}:
            kind = {"transfer_create": "transfer", "reserve": "fbs", "fbo_ship": "fbo"}[stage]
            service, payload = creation(kind, key="rollback")
            run(db, service, **payload)
        else:
            service = {
                "dispatch": dispatch_transfer, "receive": receive_transfer,
                "fbs_ship": ship_fbs, "fbs_cancel": cancel_fbs, "fbo_receive": receive_fbo,
            }[stage]
            run(db, service, operation.id)
    assert snapshot(db) == before


def test_t13_every_transfer_event_is_complete_and_unique(db):
    operation = run(db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
                    destination_warehouse_id=SOUTH, qty=3, idempotency_key="history")
    run(db, dispatch_transfer, operation.id)
    run(db, receive_transfer, operation.id)
    rows = events(db, operation.id)
    assert len(rows) == 3
    assert {event.event_type: event.operation_status for event in rows} == {
        "transfer_created": "draft", "transfer_dispatched": "in_transit", "transfer_received": "received"}
    for event in rows:
        assert event.operation_id == operation.id
        assert event.product_id == P101 and event.sku == "SKU-101"
        assert event.qty == 3
        assert event.from_warehouse_id == NORTH and event.to_warehouse_id == SOUTH
        assert event.from_name == "Север" and event.to_name == "Юг"
        assert event.created_at.tzinfo is not None
        assert event.created_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("service", [dispatch_transfer, receive_transfer, ship_fbs, cancel_fbs, receive_fbo])
def test_unknown_operation_is_domain_404_without_side_effects(db, service):
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, uuid4())
    assert error.value.status_code == 404
    assert snapshot(db) == before


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
@pytest.mark.parametrize("qty", [0, -1, 1.5, True, "1", MAX_QUANTITY + 1])
def test_new_commands_validate_quantity_before_mutation(db, kind, qty):
    service, payload = creation(kind, qty=qty)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **payload)
    assert error.value.status_code == 422
    assert snapshot(db) == before


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
@pytest.mark.parametrize("key", ["", "   ", "a" * 201, None, "bad\x00key", "bad\ud800key"])
def test_new_commands_require_valid_idempotency_key(db, kind, key):
    service, payload = creation(kind, key=key)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **payload)
    assert error.value.status_code == 422
    assert snapshot(db) == before


@pytest.mark.parametrize("key", ["bad\x00key", "bad\ud800key"])
def test_receipt_invalid_postgresql_text_key_is_domain_error(db, key):
    service, payload = creation("receipt", key=key)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **payload)
    assert error.value.status_code == 422
    assert snapshot(db) == before


@pytest.mark.parametrize("kind,field", [
    ("transfer", "product_id"), ("transfer", "source_warehouse_id"),
    ("transfer", "destination_warehouse_id"), ("fbs", "product_id"),
    ("fbs", "warehouse_id"), ("fbo", "product_id"), ("fbo", "warehouse_id"),
])
def test_new_commands_reject_unknown_references_without_effects(db, kind, field):
    service, payload = creation(kind)
    payload[field] = uuid4()
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **payload)
    assert error.value.status_code == 404
    assert snapshot(db) == before


@pytest.mark.parametrize("service", [dispatch_transfer, receive_transfer, ship_fbs, cancel_fbs, receive_fbo])
def test_transition_rejects_wrong_operation_kind(db, service):
    with db() as session:
        receipt_id = session.scalar(select(Operation.id).where(Operation.kind == "receipt").limit(1))
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, receipt_id)
    assert error.value.status_code == 409
    assert snapshot(db) == before


@pytest.mark.parametrize("kind,field", [("fbs", "customer"), ("fbo", "marketplace")])
@pytest.mark.parametrize("value", [None, 42, "", "  ", "a" * 201, "bad\x00name", "bad\ud800name"])
def test_external_destination_validation_is_domain_error(db, kind, field, value):
    service, payload = creation(kind)
    payload[field] = value
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **payload)
    assert error.value.status_code == 422
    assert snapshot(db) == before


@pytest.mark.parametrize("kind,field", [("fbs", "customer"), ("fbo", "marketplace")])
def test_external_destination_normalization_preserves_replay_identity(db, kind, field):
    service, payload = creation(kind)
    payload[field] = "  Получатель  "
    first = run(db, service, **payload)
    assert first.external_party == "Получатель"
    before = snapshot(db)
    assert run(db, service, **dict(payload, **{field: "Получатель"})).id == first.id
    assert snapshot(db) == before


@pytest.mark.parametrize("changes", [
    {"product_id": P102},
    {"source_warehouse_id": SOUTH, "destination_warehouse_id": NORTH},
])
def test_transfer_key_cannot_change_product_or_route(db, changes):
    service, payload = creation("transfer")
    run(db, service, **payload)
    before = snapshot(db)
    with pytest.raises(DomainError) as error:
        run(db, service, **dict(payload, **changes))
    assert error.value.status_code == 409
    assert snapshot(db) == before


@pytest.mark.parametrize("kind,terminal", [("fbs", "shipped"), ("fbs", "cancelled"), ("fbo", "received")])
def test_t13_external_operation_events_have_complete_immutable_snapshots(db, kind, terminal):
    service, payload = creation(kind)
    operation = run(db, service, **payload)
    transition = receive_fbo if kind == "fbo" else (ship_fbs if terminal == "shipped" else cancel_fbs)
    run(db, transition, operation.id)
    rows = events(db, operation.id)
    assert len(rows) == 2
    expected_statuses = {f"{kind}_{terminal}": terminal}
    if kind == "fbs":
        expected_statuses["fbs_reserved"] = "reserved"
    else:
        expected_statuses["fbo_shipped"] = "in_transit"
    assert {event.event_type: event.operation_status for event in rows} == expected_statuses
    for event in rows:
        assert (event.operation_id, event.product_id, event.sku, event.qty) == (
            operation.id, P101, "SKU-101", 2,
        )
        assert event.from_warehouse_id == NORTH and event.to_warehouse_id is None
        assert event.from_name == "Север"
        assert event.to_name == ("Клиент" if kind == "fbs" else "Маркетплейс")
        assert event.created_at.utcoffset().total_seconds() == 0
