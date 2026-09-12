"""Backward-compatibility wrapper for database connection.

This module re-exports all database connection utilities from db.py.
New code should import from database.db directly.
"""

from database.db import (
    Base,
    get_engine,
    get_session_factory,
    get_session,
    session_scope,
    init_db,
    drop_all,
    dispose_engine,
    get_database_uri,
)

# Backward compatibility alias
dispose_engines = dispose_engine

# MongoDB utilities (optional)
from config import Config

_mongo_client = None


def get_mongo_client():
    """Return a cached PyMongo client, or None when MONGO_URI is unset."""
    global _mongo_client
    if _mongo_client is None and Config.MONGO_URI:
        from pymongo import MongoClient

        _mongo_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return _mongo_client


def get_mongo_db(name: str | None = None):
    """Return the configured MongoDB database, or None when not configured."""
    client = get_mongo_client()
    if client is None:
        return None
    return client.get_database(name or Config.MONGO_DB)


__all__ = [
    "Base",
    "get_engine",
    "get_session_factory",
    "get_session",
    "session_scope",
    "init_db",
    "drop_all",
    "dispose_engine",
    "dispose_engines",
    "get_database_uri",
    "get_mongo_client",
    "get_mongo_db",
]