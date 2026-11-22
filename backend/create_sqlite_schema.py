"""
Create the SQLite database schema directly from SQLAlchemy models.
Bypasses Alembic migrations (which contain Postgres-specific DDL).

Usage:
    cd backend
    export $(grep -v '^#' .env.local | xargs)
    python3 create_sqlite_schema.py
"""
import sqlite_compat  # must be first — patches UUID + pgvector

import os
from pathlib import Path
from sqlalchemy import create_engine, event, text

# Load .env.local if not already set
env_file = Path(__file__).parent / ".env.local"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app.core.config import settings
from app.models.base import Base
from app.models import *  # noqa: F401,F403 — register all models

db_url = settings.database_url_sync
print(f"Creating schema at: {db_url}")

engine = create_engine(db_url, echo=False)

@event.listens_for(engine, "connect")
def set_pragmas(dbapi_conn, _rec):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")

Base.metadata.create_all(engine)

# Stamp the alembic_version table so future `alembic upgrade head` is a no-op
with engine.connect() as conn:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    ))
    existing = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    if not existing:
        conn.execute(text("INSERT INTO alembic_version VALUES ('0001')"))
    conn.commit()

tables = list(Base.metadata.tables.keys())
print(f"Created {len(tables)} tables: {', '.join(sorted(tables))}")
print("Done.")
