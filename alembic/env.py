"""Run migrations against PostgreSQL or a fixture-owned connection."""

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from app import models  # Register all tables with the declarative metadata.
from app.config import DATABASE_URL
from app.db import Base

config = context.config


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=DATABASE_URL, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    engine = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
