"""
Async SQLAlchemy engine and session setup.

Supports any SQLAlchemy-compatible async driver:
  Development:  sqlite+aiosqlite:///./clip_node.db
  Production:   postgresql+asyncpg://user:pass@host/db
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .migrations import upgrade

# Module-level engine and session factory; initialised in init_db()
_engine = None
_AsyncSessionLocal: async_sessionmaker | None = None


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _engine


async def init_db(database_url: str) -> None:
    """
    Create the async engine, apply numbered migrations, and configure the session factory.
    Called once at application startup via the FastAPI lifespan handler.
    """
    global _engine, _AsyncSessionLocal

    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}

    _engine = create_async_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
    )
    _AsyncSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        if database_url.startswith("sqlite"):
            mode = (await conn.exec_driver_sql("PRAGMA journal_mode=WAL")).scalar_one()
            if mode not in {"wal", "memory"}:
                raise RuntimeError(f"SQLite WAL mode could not be enabled: {mode}")
        await conn.run_sync(upgrade)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    if _AsyncSessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    async with _AsyncSessionLocal() as session:
        yield session


async def close_db() -> None:
    """Dispose database connections during application shutdown."""
    global _engine, _AsyncSessionLocal
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _AsyncSessionLocal = None


def AsyncSessionLocal() -> AsyncSession:
    """
    Return a new async session context manager.

    Used by background tasks (e.g. gossip engine) that cannot use the
    FastAPI dependency-injection system.

    Usage:
        async with AsyncSessionLocal() as session:
            ...
    """
    if _AsyncSessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _AsyncSessionLocal()
