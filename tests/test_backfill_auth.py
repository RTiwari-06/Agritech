from scripts import backfill_auth
import pytest


@pytest.fixture()
def app():
    from api.app import create_app
    from database.db import drop_all, dispose, init_db

    drop_all()
    init_db()
    application, _ = create_app()
    yield application
    dispose()


def test_backfill_helpers_are_read_only_when_not_applied(app):
    from database.db import get_db

    db = get_db()
    db["users"].insert_one({
        "_id": "legacy-user",
        "username": "legacy",
        "email": "legacy@example.com",
        "role": "buyer",
    })

    assert backfill_auth.ensure_int_ids(db, apply=False) == 1
    assert backfill_auth.backfill_passwords(
        db, "demo-password-123", apply=False
    ) == 1

    user = db["users"].find_one({"_id": "legacy-user"})
    assert user is not None
    assert "password_hash" not in user
