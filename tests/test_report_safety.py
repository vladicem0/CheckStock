"""Regression checks for report isolation and retained ORM state (deferred to D)."""

import pytest
from sqlalchemy import text

from app.models import Operation, Product, StockBalance, Warehouse
from app.services.inventory import get_inventory, list_history, list_operations, operation_view
from app.services.operations import (
    create_transfer, dispatch_transfer, receive_transfer, reserve_fbs, ship_fbs,
)
from app.services.warehouses import rename_warehouse
from tests.helpers import NORTH, SOUTH, P101, run, snapshot


READERS = [get_inventory, list_operations, list_history]


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("isolation", ["READ COMMITTED", "READ UNCOMMITTED"])
def test_reports_reject_isolation_that_can_mix_snapshots(db, reader, isolation):
    before = snapshot(db)
    with db.begin() as session:
        session.connection(execution_options={"isolation_level": isolation})
        with pytest.raises(RuntimeError, match="REPEATABLE READ or SERIALIZABLE"):
            reader(session)
        # Rejection does not change isolation, abort or commit the caller's transaction.
        assert session.in_transaction()
        assert session.scalar(text("SHOW transaction_isolation")) == isolation.lower()
        assert session.get(Product, P101).sku == "SKU-101"
    assert snapshot(db) == before


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_reports_accept_consistent_caller_transactions(db, reader, isolation):
    before = snapshot(db)
    with db.begin() as session:
        session.connection(execution_options={"isolation_level": isolation})
        result = reader(session)
        assert session.in_transaction()
        assert session.scalar(text("SHOW transaction_isolation")) == isolation.lower()
        if reader is get_inventory:
            assert result["totals"]["on_hand"] == 25
            assert len(result["balances"]) == 6
        else:
            assert len(result) == 4
    assert snapshot(db) == before


@pytest.mark.parametrize("reader", READERS)
def test_guard_checks_active_transaction_not_connection_configuration(db, reader):
    with db.begin() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        with pytest.raises(RuntimeError, match="REPEATABLE READ or SERIALIZABLE"):
            reader(session)


def test_inventory_refreshes_retained_objects_after_previous_transaction(db):
    reservation = run(
        db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
        customer="Клиент", idempotency_key="report-retained-inventory",
    )
    with db() as session:
        # expire_on_commit=False keeps these references stale across transactions.
        with session.begin():
            cached_product = session.get(Product, P101)
            cached_warehouse = session.get(Warehouse, NORTH)
            cached_balance = session.get(StockBalance, (P101, NORTH))
            cached_operation = session.get(Operation, reservation.id)
        with db.begin() as writer:
            ship_fbs(writer, reservation.id)
            rename_warehouse(writer, NORTH, "Север после отправки")
            # A fixture catalog edit exercises projection refresh, not a new product API.
            writer.get(Product, P101).name = "Обновлённый органайзер"
        assert cached_balance.on_hand == 10 and cached_balance.reserved_fbs == 4
        assert cached_warehouse.name == "Север"
        assert cached_operation.status == "reserved"
        assert cached_product.name == "Органайзер"

        with session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            report = get_inventory(session)
            assert report["totals"] == {
                "on_hand": 21, "reserved_fbs": 0, "available": 21,
                "transit_ff": 0, "transit_fbo": 0, "transit_fbs": 4,
            }
            shipment, = report["transit"]["fbs"]
            assert shipment["id"] == reservation.id and shipment["status"] == "shipped"
            assert shipment["source_name"] == "Север после отправки"
            assert shipment["product_name"] == "Обновлённый органайзер"
            cell = next(row for row in report["balances"]
                        if row["product_id"] == P101 and row["warehouse_id"] == NORTH)
            assert (cell["on_hand"], cell["reserved_fbs"], cell["available"]) == (6, 0, 6)
            assert cell["warehouse_name"] == "Север после отправки"
            assert cached_operation.status == "shipped"
            assert cached_balance.on_hand == 6 and cached_balance.reserved_fbs == 0
            assert cached_warehouse.name == "Север после отправки"
            assert cached_product.name == "Обновлённый органайзер"


def test_operation_list_refreshes_status_and_related_names_in_reused_session(db):
    transfer = run(
        db, create_transfer, product_id=P101, source_warehouse_id=NORTH,
        destination_warehouse_id=SOUTH, qty=2, idempotency_key="report-retained-operations",
    )
    with db() as session:
        with session.begin():
            cached_operation = session.get(Operation, transfer.id)
            cached_source = session.get(Warehouse, NORTH)
            cached_destination = session.get(Warehouse, SOUTH)
            cached_product = session.get(Product, P101)
        with db.begin() as writer:
            rename_warehouse(writer, NORTH, "Новый Север")
            rename_warehouse(writer, SOUTH, "Новый Юг")
            dispatch_transfer(writer, transfer.id)
            receive_transfer(writer, transfer.id)
            writer.get(Product, P101).name = "Новое название товара"
        assert cached_operation.status == "draft"
        assert cached_source.name == "Север" and cached_destination.name == "Юг"
        assert cached_product.name == "Органайзер"

        with session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            operation = next(row for row in list_operations(session) if row["id"] == transfer.id)
            assert operation["status"] == "received"
            assert operation["source_name"] == "Новый Север"
            assert operation["destination_name"] == "Новый Юг"
            assert operation["product_name"] == "Новое название товара"
            assert cached_operation.status == "received"
            assert cached_source.name == "Новый Север" and cached_destination.name == "Новый Юг"
            assert cached_product.name == "Новое название товара"


def test_single_command_projection_remains_available_in_write_transaction(db):
    with db.begin() as session:
        operation = reserve_fbs(
            session, product_id=P101, warehouse_id=NORTH, qty=1,
            customer="Клиент", idempotency_key="write-response",
        )
        response = operation_view(session, operation)
        assert response["id"] == operation.id and response["status"] == "reserved"
        assert response["source_name"] == "Север" and response["qty"] == 1
        assert session.scalar(text("SHOW transaction_isolation")) == "read committed"
