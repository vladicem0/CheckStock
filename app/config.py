"""Configuration is explicit; importing the app never loads demo data."""

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError("Set DATABASE_URL to a PostgreSQL psycopg connection URL")
