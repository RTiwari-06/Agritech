"""Framework entrypoint: Flask + SocketIO server.

Run:
    python main.py

Then launch the Streamlit frontend in a second terminal:
    streamlit run frontend/app.py
"""

import os
import sys

# Silence Windows asyncio ProactorBasePipeTransport socket shutdown tracebacks on connection lost
if sys.platform == "win32":
    import asyncio
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost
        def _silent_call_connection_lost(self, exc=None):
            try:
                _orig_call_connection_lost(self, exc)
            except (OSError, RuntimeError):
                pass
        _ProactorBasePipeTransport._call_connection_lost = _silent_call_connection_lost
    except (ImportError, AttributeError):
        pass

from api.app import create_app
from config import Config
from database.db import get_db
from seed_data import reset_and_seed

# Auto-seed on first run if the DB is empty (mongomock = in-process,
# so a separate seed script can't share data with the server).
_db = get_db()
_users = _db["users"]
_total_users = _users.count_documents({})
if _total_users == 0:
    print(f"Auto-seeding demo data on startup (users={_total_users})...")
    reset_and_seed()
    print(
        f"DEBUG after seed — "
        f"users={_db['users'].count_documents({})}, "
        f"products={_db['products'].count_documents({})}, "
        f"orders={_db['orders'].count_documents({})}, "
        f"streams={_db['live_streams'].count_documents({})}, "
        f"reviews={_db['reviews'].count_documents({})}, "
        f"messages={_db['chat_messages'].count_documents({})}, "
        f"counters={_db['_counters'].count_documents({})}"
    )
    print(f"DEBUG first_user={_db['users'].find_one()}")
    print(f"DEBUG first_product={_db['products'].find_one()}")
else:
    print(
        f"Using existing data in MongoDB ({_total_users} users)."
    )

app, socketio = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
    )