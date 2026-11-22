"""
Alembic migration environment.
Supports both PostgreSQL (production/Docker) and SQLite (local dev).
"""
import sqlite_compat  # noqa: F401 — must be first; patches UUID + pgvector

from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool, event, text

# Import all models so Alembic can detect schema changes
from app.models import *  # noqa: F401, F403
from app.models.base import Base
from app.core.config import settings

config = context.config

sync_url = settings.database_url_sync
_is_sqlite = sync_url.startswith("sqlite")

if not _is_sqlite:
    # Force psycopg2 driver for Postgres
    sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")

config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,  # required for ALTER TABLE on SQLite
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(sync_url, poolclass=pool.NullPool)

    if _is_sqlite:
        # Enable WAL mode and foreign keys for SQLite
        @event.listens_for(connectable, "connect")
        def set_sqlite_pragmas(dbapi_conn, _rec):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
