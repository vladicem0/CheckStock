"""Real PostgreSQL fixtures: one migrated, disposable schema per test.

The database may also contain demo data. Only the exact randomly created test
schema is ever dropped; public is never truncated, migrated, or cleared here.
"""
from collections.abc import Callable, Iterator
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import DATABASE_URL
from app.models import Operation, Product, StockBalance, StockEvent, Warehouse
from app.seed import seed_demo


@pytest.fixture
def empty_db() -> Iterator[sessionmaker[Session]]:
    schema = f"test_checkstock_{uuid4().hex}"
    if re.fullmatch(r"test_checkstock_[0-9a-f]{32}", schema) is None:
        raise RuntimeError("Refusing to create an unsafe test schema")
    admin_engine = create_engine(DATABASE_URL, poolclass=NullPool)
    schema_created = False
    test_engine = None
    try:
        # A generated SQL identifier, validated above; no user input is interpolated.
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        test_engine = create_engine(
            DATABASE_URL,
            connect_args={"options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=30000"},
            pool_pre_ping=True,
        )
        with test_engine.begin() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            root = Path(__file__).resolve().parents[1]
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "alembic"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield sessionmaker(bind=test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if schema_created:
            # Revalidate immediately before the only destructive test command.
            if re.fullmatch(r"test_checkstock_[0-9a-f]{32}", schema) is None:
                raise RuntimeError("Refusing to drop a non-test schema")
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def db(empty_db: sessionmaker[Session]) -> sessionmaker[Session]:
    with empty_db.begin() as session:
        seed_demo(session)
    return empty_db


@pytest.fixture
def stock_snapshot(empty_db: sessionmaker[Session]) -> Callable[[], dict]:
    """Read committed rows through a new session, including IDs and timestamps."""
    def snapshot() -> dict:
        with empty_db() as session:
            return {
                model.__tablename__: session.execute(
                    select(*model.__table__.columns).order_by(*model.__table__.primary_key)
                ).all()
                for model in (Product, Warehouse, StockBalance, Operation, StockEvent)
            }
    return snapshot
