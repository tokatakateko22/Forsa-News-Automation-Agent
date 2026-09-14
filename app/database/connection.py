"""
app/database/connection.py
──────────────────────────
Async SQLAlchemy engine and session factory.
Usage:
    async with get_session() as session:
        result = await session.execute(...)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.database.models import Base

# ── Engine ────────────────────────────────────────────────────────────────────

def _make_engine() -> AsyncEngine:
    """Create the async SQLAlchemy engine from DATABASE_URL."""
    connect_args: dict = {
        "server_settings": {"application_name": "forsa-news-agent"},
    }
    # Neon and cloud PgBouncer poolers require SSL and statement_cache_size=0
    if "neon.tech" in settings.database_url or "pooler" in settings.database_url:
        connect_args["ssl"] = True
        connect_args["statement_cache_size"] = 0
    elif "localhost" not in settings.database_url and "127.0.0.1" not in settings.database_url:
        connect_args["ssl"] = True

    return create_async_engine(
        settings.database_url,
        echo=(settings.environment == "development"),
        pool_pre_ping=True,         # Detect stale connections
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,          # Recycle connections every 30 min
        connect_args=connect_args,
    )


engine: AsyncEngine = _make_engine()

# ── Session factory ───────────────────────────────────────────────────────────

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields an AsyncSession.
    Automatically commits on success and rolls back on exception.

    Usage:
        async with get_session() as session:
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Schema initialisation (for tests / first-time setup) ─────────────────────

async def init_db() -> None:
    """
    Create all tables if they do not already exist.
    In production, prefer Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db() -> None:
    """
    Drop all tables.  For use in tests only.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_db() -> None:
    """Dispose the engine connection pool gracefully."""
    await engine.dispose()
