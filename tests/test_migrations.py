"""Exercise the real Alembic schema without changing the working database."""

from pathlib import Path
import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text

from app.db import Base
from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app.seed import seed_demo


def test_fresh_migration_contains_no_implicit_demo_data(empty_db):
    with empty_db() as session:
        assert session.bind.dialect.name == "postgresql"
        for model in (Product, Warehouse, StockBalance, Operation, StockEvent):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0001_initial"


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
