"""Database connectivity and session management.

Primary store is SQLAlchemy-backed SQLite (agri_sales.db) with support for
other SQL databases via configuration. Provides engine, session factory,
and transaction-scoped session context manager.
"""

from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

from config import Config


Base = declarative_base()

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine for the effective database URI."""
    global _engine
    if _engine is None:
        uri = Config.effective_database_uri()
        kwargs = {"pool_pre_ping": True, "echo": Config.SQL_ECHO}
        if uri.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in uri:
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(uri, **kwargs)
    return _engine


def get_session_factory() -> sessionmaker:
    """Return a cached session factory bound to the configured engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autocommit=False, autoflush=False
        )
    return _session_factory


def get_session() -> Session:
    """Create a new SQLAlchemy session bound to the configured engine."""
    return get_session_factory()()


@contextmanager
def session_scope():
    """Transaction-scoped session: commit on success, rollback on error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables (idempotent)."""
    from database import models  # noqa: F401  (ensure mapping registration)

    Base.metadata.create_all(bind=get_engine())


def drop_all() -> None:
    """Drop all tables. Intended for tests/rebuilds only."""
    from database import models  # noqa: F401

    Base.metadata.drop_all(bind=get_engine())


def dispose_engine() -> None:
    """Dispose cached engine (used when swapping databases in tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _session_factory = None


def get_database_uri() -> str:
    """Return the effective database URI."""
    return Config.effective_database_uri()


# Re-export for backward compatibility
__all__ = [
    "Base",
    "get_engine",
    "get_session_factory",
    "get_session",
    "session_scope",
    "init_db",
    "drop_all",
    "dispose_engine",
    "get_database_uri",
]