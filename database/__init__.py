"""Data access layer: MongoDB (primary) — no SQLAlchemy in this project."""

from database import models  # noqa: F401  (makes enums + factory functions available)
from database.db import (  # noqa: F401
    MongoSession,
    dispose,
    drop_all,
    get_db,
    get_client,
    init_db,
    session_scope,
)

__all__ = [
    "MongoSession",
    "dispose",
    "drop_all",
    "get_db",
    "get_client",
    "init_db",
    "session_scope",
    "models",
]
