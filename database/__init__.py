"""Data access layer: SQLAlchemy ORM (primary) and optional Mongo client."""

from database import models  # noqa: F401  (registers ORM mappings)
from database.db import (  # noqa: F401
    Base,
    dispose_engine as dispose_engines,
    drop_all,
    get_engine,
    get_session,
    get_session_factory,
    init_db,
    session_scope,
)
from database.connection import (  # noqa: F401
    get_mongo_client,
    get_mongo_db,
)

__all__ = [
    "Base",
    "dispose_engines",
    "drop_all",
    "get_engine",
    "get_mongo_client",
    "get_mongo_db",
    "get_session",
    "get_session_factory",
    "init_db",
    "session_scope",
    "models",
]