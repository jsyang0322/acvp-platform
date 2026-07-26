"""Database engine and session factory (lazy).

Importing this module never opens a connection: the engine is built on first use
and SQLAlchemy's pool connects lazily, so the app and the test suite behave exactly
as before when DATABASE_URL is unset. Wiring the ORM store onto these sessions is
the next phase; for now only the /health/db readiness probe (db_ping) uses them.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


class DatabaseNotConfigured(RuntimeError):
    """Raised when a DB operation is attempted while DATABASE_URL is unset."""


@lru_cache
def get_engine() -> Engine:
    """The process-wide SQLAlchemy engine. Cached, and only built once a caller
    actually needs the DB — so nothing connects at import time."""
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Configure it (see .env.example) to enable "
            "database features."
        )
    return create_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,  # transparently recover from dropped/stale connections
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session for the DB-backed store
    (used from the next phase). Commits on success, rolls back on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_ping() -> bool:
    """True if the database is reachable. Backs the /health/db readiness probe.

    Never raises — returns False when the DB is unconfigured (DatabaseNotConfigured)
    or unreachable, so the probe can report status without taking the app down.
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
