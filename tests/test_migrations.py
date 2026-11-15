"""Exercise the real Alembic schema without changing the working database."""

from pathlib import Path
import re

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import Base
from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app.seed import seed_demo
from app.services.operations import deliver_fbs, reserve_fbs, ship_fbs
from tests.helpers import NORTH, P101, run, snapshot, transit


def migration_config(connection):
    schema = connection.scalar(text("SELECT current_schema()"))
    # Every migration test is confined to its fixture-owned schema.
    assert re.fullmatch(r"test_checkstock_[0-9a-f]{32}", schema)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["connection"] = connection
    return config


def test_fresh_migration_contains_no_implicit_demo_data(empty_db):
    with empty_db() as session:
        assert session.bind.dialect.name == "postgresql"
        for model in (Product, Warehouse, StockBalance, Operation, StockEvent):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0002_fbs_delivery"


def test_migration_matches_orm_metadata(empty_db):
    with empty_db() as session:
        context = MigrationContext.configure(session.connection())
        assert compare_metadata(context, Base.metadata) == []


def test_database_timestamps_preserve_timezone_information(empty_db):
    with empty_db() as session:
        columns = session.execute(text("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND column_name IN ('created_at', 'updated_at', 'archived_at')
        """)).all()
        assert {(table, column) for table, column, _ in columns} == {
            ("warehouses", "created_at"), ("warehouses", "archived_at"),
            ("operations", "created_at"), ("operations", "updated_at"),
            ("stock_events", "created_at"),
        }
        assert all(data_type == "timestamp with time zone" for _, _, data_type in columns)


def test_migration_round_trip_only_in_disposable_schema(empty_db):
    with empty_db.begin() as session:
        connection = session.connection()
        schema = connection.scalar(text("SELECT current_schema()"))
        # The fixture owns this exact generated schema. Never downgrade public.
        assert re.fullmatch(r"test_checkstock_[0-9a-f]{32}", schema)
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        assert connection.scalar(text("""
            SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname = 'checkstock_reject_event_mutation'
        """)) == 0
        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == {
            "alembic_version", "products", "warehouses", "stock_balances", "operations", "stock_events",
        }
        seed_demo(session)
        assert session.scalar(select(func.sum(StockBalance.on_hand))) == 25


def test_fbs_delivery_upgrade_preserves_populated_initial_schema(empty_db):
    with empty_db.begin() as session:
        command.downgrade(migration_config(session.connection()), "0001_initial")
        seed_demo(session)
        operation = reserve_fbs(session, product_id=P101, warehouse_id=NORTH, qty=4,
                                customer="Клиент", idempotency_key="existing-shipment")
        operation_id = operation.id
        ship_fbs(session, operation_id)
    before = snapshot(empty_db)

    with empty_db.begin() as session:
        command.upgrade(migration_config(session.connection()), "head")
    assert snapshot(empty_db) == before
    with empty_db() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0002_fbs_delivery"
        assert session.get(Operation, operation_id).status == "shipped"
    assert transit(empty_db, "fbs") == 4
    assert run(empty_db, deliver_fbs, operation_id).status == "delivered"
    assert snapshot(empty_db)["stock_balances"] == before["stock_balances"]
    assert transit(empty_db, "fbs") == 0


def test_fbs_delivery_downgrade_refuses_to_rewrite_delivered_history(db):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
                    customer="Клиент", idempotency_key="delivered-before-downgrade")
    run(db, ship_fbs, operation.id)
    run(db, deliver_fbs, operation.id)
    before = snapshot(db)

    with pytest.raises(DBAPIError, match="Cannot downgrade: delivered FBS shipments must be preserved"):
        with db.begin() as session:
            command.downgrade(migration_config(session.connection()), "0001_initial")
    assert snapshot(db) == before
    with db() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0002_fbs_delivery"
        assert session.get(Operation, operation.id).status == "delivered"
        constraints = inspect(session.connection()).get_check_constraints("operations")
        status_constraint = next(c for c in constraints if c["name"] == "ck_operation_status")
        assert "delivered" in status_constraint["sqltext"]


def test_fbs_delivery_downgrade_round_trip_preserves_undelivered_rows(db):
    operation = run(db, reserve_fbs, product_id=P101, warehouse_id=NORTH, qty=4,
                    customer="Клиент", idempotency_key="undelivered-before-downgrade")
    run(db, ship_fbs, operation.id)
    before = snapshot(db)

    with db.begin() as session:
        command.downgrade(migration_config(session.connection()), "0001_initial")
    assert snapshot(db) == before
    with db() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0001_initial"
    with pytest.raises(IntegrityError) as caught:
        with db.begin() as session:
            session.get(Operation, operation.id).status = "delivered"
            session.flush()
    assert caught.value.orig.diag.constraint_name == "ck_operation_status"
    assert snapshot(db) == before

    with db.begin() as session:
        command.upgrade(migration_config(session.connection()), "head")
    assert snapshot(db) == before
    assert run(db, deliver_fbs, operation.id).status == "delivered"
