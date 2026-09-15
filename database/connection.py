"""Backward-compatibility wrapper for database connection.

Re-exports the MongoDB data-layer utilities from ``db.py``.  All previous
SQLAlchemy symbols (``Base``, ``get_engine``, ``sessionmaker``, ``get_session``,
``init_db``, ``drop_all``, ``dispose_engine``, ``get_database_uri``) are no
longer available — the project now uses MongoDB exclusively.
"""

from database.db import (
    MongoSession,
    session_scope,
    init_db,
    drop_all,
    dispose,
)

__all__ = [
    "MongoSession",
    "session_scope",
    "init_db",
    "drop_all",
    "dispose",
]
