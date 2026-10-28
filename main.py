"""Agritech REST engine entrypoint — Redis + Flask + SocketIO (spec §1).

Network interfaces (phase_integration_specification.md §Phase 1):
    * Redis  127.0.0.1:6379 (DB 0, RESP2 protocol)
    * Flask  127.0.0.1:5000 (REST + Socket.IO)

Run:
    python main.py

Then launch the Streamlit frontend in a second terminal:
    streamlit run frontend/app.py

The engine self-seeds from the default APMC PDF report the first time it
boots against an empty Redis; the remaining data flows are Redis-backed
(product Hashes, streams Set/Hashes, chat Lists, order receipts).
"""

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
from database.pdf_seeder import default_pdf_path, seed_redis
from database.redis_db import (
    get_all_products,
    get_client,
    seed_default_stream,
)
from redis.exceptions import RedisError


def _bootstrap_redis() -> None:
    """Verify Redis reachability, seed APMC commodities if empty, and make
    sure the default live stream exists (spec §3.2/§3.3)."""
    try:
        client = get_client()
        client.ping()
    except RedisError as exc:
        print(
            "WARNING: Redis at 127.0.0.1:6379 is unavailable; the product, "
            f"stream, chat, and order endpoints will report failures: {exc}"
        )
        return

    try:
        products = get_all_products()
        if not products:
            seeded = seed_redis([default_pdf_path()], flush_db=False)
            print(f"Redis empty — seeded {seeded} APMC commodities from the default report.")
        else:
            print(f"Using {len(products)} existing Redis commodities.")
        seed_default_stream(client)
    except RedisError as exc:
        print(f"WARNING: Redis bootstrap seeding failed: {exc}")


app, socketio = create_app()


if __name__ == "__main__":
    _bootstrap_redis()
    print(f"Flask REST engine listening on http://{Config.HOST}:{Config.PORT}")
    socketio.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
    )
