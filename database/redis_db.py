"""Redis-backed data layer — Phase 1 of the MongoDB purge.

Replaces the previous MongoSession layer for the read-heavy endpoints. A
single global connection pool targets ``127.0.0.1:6379`` (db 0); the live
demo data lives in two Redis structures:

* ``product:{id}``                -> Hash  (id, name, category,
                                             day1_price, day2_price, stock_kg)
* ``stream:active:chats``         -> List  (newest-first JSON strings, capped
                                             at 50 entries via LTRIM)

Prices and stock are stored as strings (Redis Hashes are byte-safe) and
converted to floats on read by :func:`get_all_products`.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis

# Standalone-execution bootstrap: running ``python database/redis_db.py``
# from any directory puts ``database/`` (not the repo root) on ``sys.path``,
# so ``config`` would not import.  Guarantee the repo root is always the
# first candidate (no-op when this module is imported normally through the
# package — the root is already on the path).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import Config  # noqa: E402



# ---------------------------------------------------------------------------
# Global connection pool (127.0.0.1:6379, db 0)
# ---------------------------------------------------------------------------

_REDIS_URL = "redis://127.0.0.1:6379/0"
_pool = redis.ConnectionPool(
    host="127.0.0.1",
    port=6379,
    db=0,
    decode_responses=True,
    protocol=2,  # RESP2 — skips the HELLO 3 handshake Redis 5.0 doesn't have
)
_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Return the process-wide Redis client, creating it on first use."""
    global _client
    if _client is None:
        _client = redis.Redis(connection_pool=_pool)
    return _client


# ---------------------------------------------------------------------------
# Seed helpers — 2-day APMC market CSV
# ---------------------------------------------------------------------------

PRODUCT_KEY_PREFIX = "product:"
CHAT_KEY = "stream:active:chats"
CHAT_CAP = 50
_FIELDS = ("id", "name", "category", "day1_price", "day2_price", "stock_kg")

_CREATE_ORDER_SCRIPT = """
local stock = tonumber(redis.call('HGET', KEYS[1], 'stock_kg'))
if not stock then
    return 'NOT_FOUND'
end
local quantity = tonumber(ARGV[2])
if stock < quantity then
    return 'INSUFFICIENT'
end
local remaining = redis.call('HINCRBYFLOAT', KEYS[1], 'stock_kg', -quantity)
redis.call(
    'HSET', ARGV[1],
    'id', ARGV[6],
    'product_id', ARGV[3],
    'quantity_kg', ARGV[2],
    'buyer_id', ARGV[4],
    'price_per_kg', ARGV[5],
    'total_price', tostring(quantity * tonumber(ARGV[5])),
    'remaining_stock_kg', remaining,
    'created_at', ARGV[7]
)
redis.call('SADD', KEYS[2], ARGV[6])
return remaining
"""


def init_redis_db(csv_path: str | None = None) -> int:
    """Flush db 0 and seed the product Hashes from a 2-day APMC CSV.

    Expected CSV columns: ``id, name, category, day1_price, day2_price,
    stock_kg``.  Returns the number of seeded products.
    """
    csv_path = csv_path or Config.APMC_CSV_PATH
    r = get_client()
    r.flushdb()

    seeded = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = int(row["id"])
            r.hset(
                f"{PRODUCT_KEY_PREFIX}{pid}",
                mapping={
                    "id": pid,
                    "name": (row.get("name") or "").strip(),
                    "category": (row.get("category") or "").strip(),
                    "day1_price": float(row["day1_price"] or 0.0),
                    "day2_price": float(row["day2_price"] or 0.0),
                    "stock_kg": float(row["stock_kg"] or 0.0),
                },
            )
            seeded += 1
    return seeded


# ---------------------------------------------------------------------------
# Read/write API used by the REST + SocketIO layers
# ---------------------------------------------------------------------------

def get_all_products() -> List[Dict[str, Any]]:
    """Every ``product:*`` Hash as a clean dict with numeric fields cast."""
    r = get_client()
    products: List[Dict[str, Any]] = []
    for key in r.scan_iter(f"{PRODUCT_KEY_PREFIX}*"):
        raw = r.hgetall(key)
        if not raw:
            continue
        products.append(
            {
                "id": int(raw.get("id", 0)),
                "name": raw.get("name", ""),
                "category": raw.get("category", ""),
                "day1_price": float(raw.get("day1_price", 0.0) or 0.0),
                "day2_price": float(raw.get("day2_price", 0.0) or 0.0),
                "stock_kg": float(raw.get("stock_kg", 0.0) or 0.0),
                "capped": raw.get("capped", "").lower() in ("1", "true", "yes"),
            }
        )
    products.sort(key=lambda p: p["id"])
    return products


def get_product(product_id: str) -> Optional[Dict[str, Any]]:
    """Return one Redis product Hash with numeric fields cast."""
    raw = get_client().hgetall(f"{PRODUCT_KEY_PREFIX}{product_id}")
    if not raw:
        return None
    return {
        "id": int(raw.get("id", 0)),
        "name": raw.get("name", ""),
        "category": raw.get("category", ""),
        "day1_price": float(raw.get("day1_price", 0.0) or 0.0),
        "day2_price": float(raw.get("day2_price", 0.0) or 0.0),
        "stock_kg": float(raw.get("stock_kg", 0.0) or 0.0),
        "capped": raw.get("capped", "").lower() in ("1", "true", "yes"),
    }


def update_product_fields(product_id: str, fields: Dict[str, Any]) -> bool:
    """Update validated Redis product Hash fields; return False if missing."""
    client = get_client()
    key = f"{PRODUCT_KEY_PREFIX}{product_id}"
    if not client.exists(key):
        return False
    client.hset(key, mapping=fields)
    return True


def create_redis_order(
    product_id: str,
    quantity_kg: float,
    buyer_id: str,
    price_per_kg: float,
    order_id: str,
) -> tuple[str, Optional[float]]:
    """Atomically reserve product stock and persist the order Hash and index.

    The Lua script performs the stock check and HINCRBYFLOAT in one Redis
    operation, so concurrent checkouts cannot oversell the same inventory.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    result = get_client().eval(
        _CREATE_ORDER_SCRIPT,
        2,
        f"{PRODUCT_KEY_PREFIX}{product_id}",
        "orders:all",
        f"order:{order_id}",
        quantity_kg,
        product_id,
        buyer_id,
        price_per_kg,
        order_id,
        created_at,
    )
    if result in ("NOT_FOUND", b"NOT_FOUND"):
        return "not_found", None
    if result in ("INSUFFICIENT", b"INSUFFICIENT"):
        return "insufficient", None
    return "created", float(result)


def get_all_orders() -> List[Dict[str, Any]]:
    """Return persisted Redis order Hashes, newest first."""
    client = get_client()
    orders: List[Dict[str, Any]] = []
    for order_id in client.smembers("orders:all"):
        raw = client.hgetall(f"order:{order_id}")
        if not raw:
            continue
        orders.append(
            {
                "id": raw.get("id", order_id),
                "product_id": raw.get("product_id", ""),
                "quantity_kg": float(raw.get("quantity_kg", 0.0) or 0.0),
                "buyer_id": raw.get("buyer_id", ""),
                "price_per_kg": float(raw.get("price_per_kg", 0.0) or 0.0),
                "total_price": float(raw.get("total_price", 0.0) or 0.0),
                "remaining_stock_kg": float(
                    raw.get("remaining_stock_kg", 0.0) or 0.0
                ),
                "created_at": raw.get("created_at", ""),
            }
        )
    orders.sort(key=lambda order: order["created_at"], reverse=True)
    return orders


def push_chat_message(message_dict: Dict[str, Any]) -> None:
    """Push one analysed chat JSON onto the capped live-feed List.

    Newest first (LPUSH) so ``LTRIM 0 49`` always keeps the 50 freshest
    messages — effectively a bounded in-Redis ring buffer.
    """
    r = get_client()
    r.lpush(CHAT_KEY, json.dumps(message_dict, default=str))
    r.ltrim(CHAT_KEY, 0, CHAT_CAP - 1)


def get_recent_chats(limit: int = 20) -> List[Dict[str, Any]]:
    """Deserialize the newest ``limit`` chat messages."""
    r = get_client()
    chats: List[Dict[str, Any]] = []
    for raw in r.lrange(CHAT_KEY, 0, max(limit, 0) - 1):
        try:
            chats.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return chats


if __name__ == "__main__":
    # Standalone use: python database/redis_db.py [path/to/apmc_2day.csv]
    # Works from any working directory (see the path bootstrap above).
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        count = init_redis_db(csv_arg)
    except redis.exceptions.ConnectionError as exc:
        print(f"Redis server unreachable: {exc}")
        print("Start Redis on 127.0.0.1:6379 (docker run -d -p 6379:6379 redis:7).")
        sys.exit(1)
    except FileNotFoundError as exc:
        print(f"APMC CSV not found: {exc}")
        print("Pass a CSV path: python database/redis_db.py data/apmc_2day.csv")
        sys.exit(1)
    print(f"Seeded {count} products into Redis ({_REDIS_URL}); db flushed first.")
