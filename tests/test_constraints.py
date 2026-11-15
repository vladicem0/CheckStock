"""PostgreSQL itself rejects invalid accounting, even without service validation."""
import re
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app.seed import demo_id


def receipt_values(**overrides):
    values = {
        "kind": "receipt",
        "status": "completed",
        "product_id": demo_id("SKU-101"),
        "qty": 1,
        "source_warehouse_id": None,
        "destination_warehouse_id": demo_id("north"),
        "external_party": None,
        "idempotency_key": f"constraint:{uuid4()}",
        "request_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return values


def event_values(event):
    return {
        column.name: getattr(event, column.name)
        for column in StockEvent.__table__.columns
        if column.name not in {"id", "created_at"}
    }


@pytest.mark.parametrize("on_hand,reserved", [(-1, 0), (10, -1), (10, 11)])
def test_database_rejects_invalid_balances(db, on_hand, reserved):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.execute(
                update(StockBalance)
                .where(StockBalance.product_id == demo_id("SKU-101"),
                       StockBalance.warehouse_id == demo_id("north"))
                .values(on_hand=on_hand, reserved_fbs=reserved)
            )
    assert caught.value.orig.sqlstate == "23514"
    with db() as session:
        balance = session.get(StockBalance, (demo_id("SKU-101"), demo_id("north")))
        assert (balance.on_hand, balance.reserved_fbs) == (10, 0)


@pytest.mark.parametrize("qty", [0, -1])
def test_database_rejects_nonpositive_operation_quantity(db, qty):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(Operation(**receipt_values(qty=qty)))
            session.flush()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_operation_qty"
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 4


@pytest.mark.parametrize("changes", [
    {"status": "draft"},
    {"kind": "unknown"},
    {"kind": "transfer", "status": "shipped", "source_warehouse_id": demo_id("south")},
    {"kind": "fbs", "status": "received", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None, "external_party": "Клиент"},
    {"kind": "fbo", "status": "reserved", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None, "external_party": "Маркетплейс"},
    {"status": "delivered"},
    {"kind": "transfer", "status": "delivered", "source_warehouse_id": demo_id("south")},
    {"kind": "fbo", "status": "delivered", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None, "external_party": "Маркетплейс"},
])
def test_database_rejects_kind_status_mismatch(db, changes):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(Operation(**receipt_values(**changes)))
            session.flush()
    assert caught.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("changes", [
    {"source_warehouse_id": demo_id("south")},
    {"destination_warehouse_id": None},
    {"external_party": "Постороннее назначение"},
    {"kind": "transfer", "status": "draft", "source_warehouse_id": demo_id("north")},
    {"kind": "transfer", "status": "draft"},
    {"kind": "fbs", "status": "reserved", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None},
    {"kind": "fbs", "status": "reserved", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None, "external_party": "   "},
    {"kind": "fbs", "status": "reserved", "external_party": "Клиент"},
    {"kind": "fbo", "status": "in_transit", "source_warehouse_id": demo_id("north"),
     "external_party": "Маркетплейс"},
    {"kind": "fbo", "status": "in_transit", "source_warehouse_id": demo_id("north"),
     "destination_warehouse_id": None},
])
def test_database_rejects_invalid_source_destination(db, changes):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(Operation(**receipt_values(**changes)))
            session.flush()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_operation_route"


@pytest.mark.parametrize("model,identifier", [
    (Product, demo_id("SKU-101")), (Warehouse, demo_id("north")), (Operation, None),
])
def test_foreign_keys_restrict_deletion_of_referenced_entities(db, model, identifier):
    with db() as session:
        if identifier is None:
            identifier = session.scalar(select(Operation.id).limit(1))
        event_count = session.scalar(select(func.count()).select_from(StockEvent))
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.execute(delete(model).where(model.id == identifier))
    assert caught.value.orig.sqlstate == "23503"
    with db() as session:
        assert session.get(model, identifier) is not None
        assert session.scalar(select(func.count()).select_from(StockEvent)) == event_count


@pytest.mark.parametrize("missing_reference", ["product_id", "destination_warehouse_id"])
def test_foreign_keys_reject_unknown_operation_references(db, missing_reference):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(Operation(**receipt_values(**{missing_reference: uuid4()})))
            session.flush()
    assert caught.value.orig.sqlstate == "23503"


@pytest.mark.parametrize("action", ["update", "delete", "truncate"])
def test_history_is_append_only_in_postgresql(db, action):
    with db() as session:
        event = session.scalars(select(StockEvent)).first()
        identifier, original = event.id, event_values(event)
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            if action == "update":
                session.execute(update(StockEvent).where(StockEvent.id == identifier).values(qty=999))
            elif action == "delete":
                session.execute(delete(StockEvent).where(StockEvent.id == identifier))
            else:
                schema = session.scalar(text("SELECT current_schema()"))
                assert re.fullmatch(r"test_checkstock_[0-9a-f]{32}", schema)
                session.execute(text(f'TRUNCATE TABLE "{schema}".stock_events'))
    assert caught.value.orig.sqlstate == "23514"
    assert "append-only" in str(caught.value.orig)
    with db() as session:
        assert event_values(session.get(StockEvent, identifier)) == original
        assert session.scalar(select(func.count()).select_from(StockEvent)) == 4


def test_idempotency_key_has_database_unique_constraint(db):
    with db() as session:
        existing_key = session.scalar(select(Operation.idempotency_key).limit(1))
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(Operation(**receipt_values(idempotency_key=existing_key)))
            session.flush()
    assert caught.value.orig.sqlstate == "23505"
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 4


def test_each_transition_has_at_most_one_event_in_database(db):
    with db() as session:
        duplicate = event_values(session.scalars(select(StockEvent)).first())
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(StockEvent(**duplicate))
            session.flush()
    assert caught.value.orig.sqlstate == "23505"
    assert caught.value.orig.diag.constraint_name == "uq_event_transition"
    with db() as session:
        assert session.scalar(select(func.count()).select_from(StockEvent)) == 4


@pytest.mark.parametrize("qty", [0, -1])
def test_event_quantity_must_be_positive(db, qty):
    with db() as session:
        values = event_values(session.scalars(select(StockEvent)).first())
    values.update(qty=qty, event_type="invalid_quantity")
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.add(StockEvent(**values))
            session.flush()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_event_qty"


@pytest.mark.parametrize("entity", ["product", "balance"])
def test_catalog_and_balance_database_uniqueness(db, entity):
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            if entity == "product":
                session.add(Product(sku="SKU-101", name="Duplicate"))
            else:
                session.add(StockBalance(product_id=demo_id("SKU-101"),
                    warehouse_id=demo_id("north"), on_hand=0, reserved_fbs=0))
            session.flush()
    assert caught.value.orig.sqlstate == "23505"


@pytest.mark.parametrize("kind,status", [
    ("receipt", "completed"),
    ("transfer", "draft"), ("transfer", "in_transit"), ("transfer", "received"),
    ("fbs", "reserved"), ("fbs", "shipped"), ("fbs", "delivered"), ("fbs", "cancelled"),
    ("fbo", "in_transit"), ("fbo", "received"),
])
def test_schema_accepts_each_documented_operation_state(db, kind, status):
    """Schema coverage only; lifecycle services are implemented in stage C."""
    route = {}
    if kind == "transfer":
        route = {"source_warehouse_id": demo_id("south")}
    elif kind in {"fbs", "fbo"}:
        route = {
            "source_warehouse_id": demo_id("north"),
            "destination_warehouse_id": None,
            "external_party": "Клиент" if kind == "fbs" else "Маркетплейс",
        }
    with db.begin() as session:
        operation = Operation(**receipt_values(kind=kind, status=status, **route))
        session.add(operation)
        session.flush()
        operation_id = operation.id
    with db() as session:
        saved = session.get(Operation, operation_id)
        assert (saved.kind, saved.status) == (kind, status)
