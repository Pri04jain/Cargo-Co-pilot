"""
SQLAlchemy async engine and session factory.

We use two engines:
- async_engine  — used by FastAPI request handlers (truly async, no blocking)
- sync_engine   — used by Alembic migrations and Celery tasks (sync context)

The get_db() dependency is a context manager yielding an AsyncSession,
automatically closed and rolled back on error.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine

from app.core.config import settings

# SQLite doesn't support pool_size/max_overflow — detect and strip those args
_is_sqlite = settings.database_url.startswith("sqlite")

_async_engine_kwargs: dict = dict(
    echo=settings.environment == "development",
    pool_pre_ping=not _is_sqlite,  # not supported by SQLite dialect
)
if not _is_sqlite:
    _async_engine_kwargs["pool_size"] = 10
    _async_engine_kwargs["max_overflow"] = 20

# Async engine for FastAPI
async_engine = create_async_engine(settings.database_url, **_async_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Sync engine for Alembic / Celery
_sync_kwargs: dict = {}
if not _is_sqlite:
    _sync_kwargs["pool_pre_ping"] = True

sync_engine = create_engine(settings.database_url_sync, **_sync_kwargs)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a scoped async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
