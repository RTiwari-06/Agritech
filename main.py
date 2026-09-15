"""Framework entrypoint: Flask + SocketIO server.

Run:
    python main.py

Then launch the Streamlit frontend in a second terminal:
    streamlit run frontend/app.py
"""

import os

from api.app import create_app
from config import Config
from database.db import get_db
from seed_data import reset_and_seed

# Auto-seed on first run if the DB is empty (mongomock = in-process,
# so a separate seed script can't share data with the server).
_db = get_db()
if _db["users"].count_documents({}) == 0:
    print("Auto-seeding demo data on startup...")
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
    print("Using existing data in MongoDB.")

app, socketio = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
    )