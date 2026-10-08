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

# Async engine for FastAPI
async_engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",  # log SQL only in dev
    pool_pre_ping=True,   # detect stale connections before using them
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # lets us access attributes after commit without re-query
    autoflush=False,
    autocommit=False,
)

# Sync engine for Alembic / Celery
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a scoped async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
