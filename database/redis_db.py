"""Redis-backed data layer — Phase 1 of the MongoDB purge.

Replaces the previous MongoSession layer for the read-heavy endpoints. A
single global connection pool targets ``127.0.0.1:6379`` (db 0); the live
demo data lives in two Redis structures:

* ``product:{id}``                -> Hash  (id, name, category,
                                             day1_price, day2_price, stock_kg,
                                             dynamic_price, max_price, min_price,
                                             capped)
* ``stream:<id>``                 -> Hash  (id, title, status)
* ``streams:all``                 -> Set   (active stream ids, spec §3.2)
* ``stream:<id>:chats``           -> List  (newest-first JSON strings, capped
                                             at 50 entries via LTRIM, spec §3.4)

Prices and stock are stored as strings (Redis Hashes are byte-safe) and
converted to floats on read by :func:`get_all_products`.
"""

import csv
import hashlib
import json
import sys
import uuid
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

# ---------------------------------------------------------------------------
# Constants — keys mirror phase_integration_specification.md §3
# ---------------------------------------------------------------------------

PRODUCT_KEY_PREFIX = "product:"          # Hash  product:<id>
STREAM_KEY_PREFIX = "stream:"            # Hash  stream:<id>
STREAMS_SET_KEY = "streams:all"          # Set   active stream ids
CHAT_LIST_SUFFIX = ":chats"              # List  stream:<id>:chats
DEFAULT_STREAM_ID = "1"
DEFAULT_STREAM_TITLE = "Bangalore APMC Live Auction"
DEFAULT_STREAM = {
    "id": DEFAULT_STREAM_ID,
    "title": DEFAULT_STREAM_TITLE,
    "status": "active",
}
# Pre-refactor chat feed key; kept readable for backward-compatible merge.
LEGACY_CHAT_KEY = "stream:active:chats"
CHAT_CAP = 50
MARKET_RECORDS_SET_KEY = "market:records"
MARKET_RECORD_KEY_PREFIX = "market:record:"
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


def chat_key(stream_id: Any) -> str:
    """Return the per-stream chat List key: ``stream:<id>:chats`` (spec §3.4)."""
    return f"{STREAM_KEY_PREFIX}{stream_id}{CHAT_LIST_SUFFIX}"


def seed_default_stream(client: Optional[redis.Redis] = None) -> None:
    """Register the default live stream (spec §3.2/§3.3 + §5 seeder fallback).

    Idempotent: always ensures ``stream:1`` is a member of ``streams:all`` but
    only writes the Hash metadata when the stream does not exist yet, so a
    seller-renamed stream keeps its title.
    """
    client = client or get_client()
    client.sadd(STREAMS_SET_KEY, DEFAULT_STREAM_ID)
    stream_hash_key = f"{STREAM_KEY_PREFIX}{DEFAULT_STREAM_ID}"
    if not client.exists(stream_hash_key):
        client.hset(
            stream_hash_key,
            mapping={
                "id": DEFAULT_STREAM_ID,
                "title": DEFAULT_STREAM_TITLE,
                "status": "active",
            },
        )


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


def store_market_records(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert parsed report rows in Redis, keyed by commodity/market/date."""
    client = get_client()
    result = {"inserted": 0, "updated": 0}
    for record in records:
        commodity = str(record.get("commodity") or "").strip()
        market = str(record.get("market") or "").strip()
        market_date = str(record.get("market_date") or "").strip()
        if not commodity or not market or not market_date:
            continue

        identity = json.dumps(
            [commodity.casefold(), market.casefold(), market_date],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        key = f"{MARKET_RECORD_KEY_PREFIX}{record_id}"
        exists = bool(client.exists(key))
        client.set(key, json.dumps(record, ensure_ascii=False, allow_nan=False))
        client.sadd(MARKET_RECORDS_SET_KEY, record_id)
        result["updated" if exists else "inserted"] += 1
    return result


def get_market_records(
    commodity: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return stored report rows, newest first, with optional exact filters."""
    client = get_client()
    normalized_commodity = commodity.strip().casefold() if commodity else None
    normalized_market = market.strip().casefold() if market else None
    rows: List[Dict[str, Any]] = []
    for record_id in client.smembers(MARKET_RECORDS_SET_KEY):
        raw = client.get(f"{MARKET_RECORD_KEY_PREFIX}{record_id}")
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if normalized_commodity and str(row.get("commodity") or "").casefold() != normalized_commodity:
            continue
        if normalized_market and str(row.get("market") or "").casefold() != normalized_market:
            continue
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("market_date") or ""), reverse=True)
    return rows[: max(0, min(limit, 1000))]


def _normalise_chat_payload(message_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Return the chat payload in the spec §3.4 shape.

    Adds ``id`` (unique message key), the ``message``/``sentiment`` aliases and
    an epoch ``timestamp`` while preserving the legacy fields
    (``message_text``/``sentiment_score``) every consumer already reads.
    """
    payload = dict(message_dict)
    text = str(payload.get("message_text") or payload.get("message") or "")
    payload["message_text"] = text  # legacy/internal alias
    payload["message"] = text      # spec §3.4 field
    try:
        score = float(payload.get("sentiment_score", payload.get("sentiment", 0.0)) or 0.0)
        score = max(-1.0, min(1.0, score))
    except (TypeError, ValueError):
        score = 0.0
    payload["sentiment_score"] = round(score, 4)  # legacy/internal alias
    payload["sentiment"] = round(score, 4)        # spec §3.4 field
    payload.setdefault("id", uuid.uuid4().hex[:12])
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, (int, float)):
        try:
            timestamp = datetime.fromisoformat(str(timestamp)).timestamp()
        except (TypeError, ValueError):
            timestamp = datetime.now(timezone.utc).timestamp()
    payload["timestamp"] = int(timestamp)
    if not payload.get("created_at"):
        payload["created_at"] = datetime.fromtimestamp(
            payload["timestamp"], tz=timezone.utc
        ).isoformat()
    if not payload.get("stream_id"):
        payload["stream_id"] = DEFAULT_STREAM_ID
    return payload


def push_chat_message(message_dict: Dict[str, Any]) -> None:
    """Push one analysed chat JSON onto its stream's capped List.

    Key layout (spec §3.4): ``stream:<id>:chats``; ``stream_id`` defaults to
    ``"1"`` when the caller does not tag the message. Newest first (LPUSH) so
    ``LTRIM 0 49`` always keeps the 50 freshest messages — a bounded in-Redis
    ring buffer. The stored payload carries both the legacy fields and the
    spec aliases (``id``, ``message``, ``sentiment``, epoch ``timestamp``).
    """
    r = get_client()
    payload = _normalise_chat_payload(message_dict)
    key = chat_key(payload["stream_id"])
    r.lpush(key, json.dumps(payload, default=str))
    r.ltrim(key, 0, CHAT_CAP - 1)


def _chat_epoch(item: Dict[str, Any]) -> float:
    """Best-effort numeric timestamp for sorting a chat payload."""
    timestamp = item.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    try:
        return datetime.fromisoformat(str(timestamp)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def get_recent_chats(limit: int = 20) -> List[Dict[str, Any]]:
    """Deserialize the newest ``limit`` chat messages across streams.

    Reads every ``stream:*:chats`` List (including the pre-refactor
    ``stream:active:chats`` key, which matches the same pattern) so chat
    posted through any stream id feeds the shared polling endpoint.
    """
    r = get_client()
    chats: List[Dict[str, Any]] = []
    for key in r.scan_iter(match=f"{STREAM_KEY_PREFIX}*{CHAT_LIST_SUFFIX}"):
        for raw in r.lrange(key, 0, CHAT_CAP - 1):
            try:
                chat = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(chat, dict):
                chats.append(chat)
    chats.sort(key=_chat_epoch, reverse=True)
    return chats[: max(limit, 0)]


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
