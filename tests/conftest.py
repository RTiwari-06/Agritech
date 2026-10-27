"""Test bootstrap: keep pytest off the real database.

``pytest.ini`` sets ``pythonpath = .``, so ``config`` is importable as soon as
pytest collects this file.  Several fixtures call ``drop_all()`` before each
test, and ``Config.MONGO_URI`` points at a live MongoDB — without this redirect
every test run would wipe the demo data the running server depends on.

Everything below happens at import time, before any test (or fixture) touches
``database.db``, so ``get_db()`` resolves to the dedicated test database for the
whole session.
"""

from config import Config

TEST_MONGO_DB = "agritech_test"

# Assert the redirect before applying it: if MONGO_DB is already the test name
# (e.g. re-entrant import) keep the original value instead of stacking suffixes.
if Config.MONGO_DB != TEST_MONGO_DB:
    Config.MONGO_DB = TEST_MONGO_DB


def pytest_sessionfinish(session, exitstatus):
    """Drop the scratch database and close the cached client once tests end."""
    try:
        from database.db import dispose, get_client

        try:
            get_client().drop_database(TEST_MONGO_DB)
        finally:
            dispose()
    except Exception:
        pass
