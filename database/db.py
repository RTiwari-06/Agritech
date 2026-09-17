"""MongoDB-backed data layer — replaces SQLAlchemy/SQLite.

Provides a ``session_scope()`` context manager that yields a
:class:`MongoSession` whose ``session['collection_name']`` gives a
``pymongo.collection.Collection``.

All data lives in MongoDB collections:
    users, products, live_streams, chat_messages, reviews, orders, price_logs

Document ids are **integers** (monotonically increasing via a ``_counters``
collection) so the REST API and frontend continue to work with the same
shapes they had under SQLAlchemy.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any, Generator

import pymongo
from pymongo.database import Database
from pymongo.collection import Collection

from config import Config


# ---------------------------------------------------------------------------
# Client (cached, mongomock when MONGO_URI is empty)
# ---------------------------------------------------------------------------

_client: pymongo.MongoClient | None = None


def get_client() -> pymongo.MongoClient:
    global _client
    if _client is None:
        uri = Config.MONGO_URI.strip()
        if uri:
            _client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000)
            try:
                _client.server_info()
            except Exception:
                import mongomock

                _client = mongomock.MongoClient()
        else:
            import mongomock

            _client = mongomock.MongoClient()
    return _client


def get_db() -> Database:
    return get_client()[Config.MONGO_DB]


# ---------------------------------------------------------------------------
# Schema bootstrapping (idempotent)
# ---------------------------------------------------------------------------

def _ensure_collections(db: Database) -> None:
    existing = set(db.list_collection_names())

    if "users" not in existing:
        db.create_collection("users")
        db["users"].create_index("email", unique=True)
        db["users"].create_index("username", unique=True)

    if "products" not in existing:
        db.create_collection("products")
        db["products"].create_index("seller_id")
        db["products"].create_index("category")

    if "live_streams" not in existing:
        db.create_collection("live_streams")
        db["live_streams"].create_index("seller_id")
        db["live_streams"].create_index("is_active")

    if "chat_messages" not in existing:
        db.create_collection("chat_messages")
        db["chat_messages"].create_index("stream_id")
        db["chat_messages"].create_index("user_id")
        db["chat_messages"].create_index("timestamp")

    if "reviews" not in existing:
        db.create_collection("reviews")
        db["reviews"].create_index("product_id")
        db["reviews"].create_index("user_id")

    if "orders" not in existing:
        db.create_collection("orders")
        db["orders"].create_index("buyer_id")
        db["orders"].create_index("product_id")
        db["orders"].create_index("status")

    if "price_logs" not in existing:
        db.create_collection("price_logs")
        db["price_logs"].create_index("product_id")

    if "sessions" not in existing:
        db.create_collection("sessions")
        db["sessions"].create_index("token_hash", unique=True)
        db["sessions"].create_index("user_id")

    if "market_prices" not in existing:
        db.create_collection("market_prices")
        db["market_prices"].create_index(
            [("commodity", 1), ("market", 1), ("market_date", 1)],
            unique=True,
        )
        db["market_prices"].create_index("market_date")
        db["market_prices"].create_index("synthetic")

    if "market_ingest_log" not in existing:
        db.create_collection("market_ingest_log")
        db["market_ingest_log"].create_index("ingested_at")

    if "_counters" not in existing:
        db.create_collection("_counters")
        db["_counters"].insert_one({"_id": "next_id", "seq": 0})


def init_db() -> None:
    db = get_db()
    _ensure_collections(db)


def drop_all() -> None:
    db = get_db()
    for name in list(db.list_collection_names()):
        db.drop_collection(name)


def dispose() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ---------------------------------------------------------------------------
# Counter for integer ids
# ---------------------------------------------------------------------------

def _next_id(db: Database) -> int:
    counters: Collection = db["_counters"]
    doc = counters.find_one_and_update(
        {"_id": "next_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return int(doc["seq"])


# ---------------------------------------------------------------------------
# Session object
# ---------------------------------------------------------------------------

class MongoSession:
    """Wraps a MongoDB database for use inside ``with session_scope():``.

    Usage::

        with session_scope() as s:
            seller = s['users'].find_one({'username': 'abc'})
            products = list(s['products'].find({'seller_id': 1}))
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> "MongoSession":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.rolled_back = True
        else:
            self.committed = True

    def __getitem__(self, name: str) -> Collection:
        return self.db[name]


@contextmanager
def session_scope() -> Generator[MongoSession, None, None]:
    db = get_db()
    _ensure_collections(db)
    sess = MongoSession(db)
    try:
        yield sess
    except Exception:
        sess.rolled_back = True
        raise


# ---------------------------------------------------------------------------
# Insert helpers — assign integer _id + created_at
# ---------------------------------------------------------------------------

def _insert_one(collection: str, doc: dict, db: Database | None = None) -> dict:
    target_db = db or get_db()
    doc["_id"] = _next_id(target_db)
    doc.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    target_db[collection].insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def get_user(session: MongoSession, user_id: int) -> dict | None:
    return session["users"].find_one({"_id": user_id})


def get_user_by_email(session: MongoSession, email: str) -> dict | None:
    return session["users"].find_one({"email": email})


def get_user_by_username(session: MongoSession, username: str) -> dict | None:
    return session["users"].find_one({"username": username})


def upsert_user(session: MongoSession, doc: dict) -> dict:
    """Insert a new user or return existing one matched by email/username."""
    existing = None
    if doc.get("email"):
        existing = session["users"].find_one({"email": doc["email"]})
    if not existing and doc.get("username"):
        existing = session["users"].find_one({"username": doc["username"]})
    if existing:
        return existing
    return _insert_one("users", doc, session.db)


# ---------------------------------------------------------------------------
# Authentication — demo/app auth: PBKDF2-SHA256 password hashing + DB bearer
# sessions.  Production note: use httpOnly cookies/short-lived bearer tokens,
# periodic expiry cleanup, rate limiting, TLS, and an env-configured secret.
# ---------------------------------------------------------------------------

_HASH_ALGO = "sha256"
_ITERATIONS = 240_000
_SESSION_TTL = timedelta(days=1)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        _HASH_ALGO, password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"pbkdf2${_HASH_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, name, iterations, salt_hex, digest_hex = password_hash.split("$")
        if algo != "pbkdf2" or name != _HASH_ALGO:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            _HASH_ALGO, password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def authenticate_user(
    session: MongoSession, identifier: str, password: str
) -> dict | None:
    user = session["users"].find_one(
        {"$or": [{"email": identifier}, {"username": identifier}]}
    )
    if user is None:
        return None
    stored = user.get("password_hash") or ""
    if not stored or not verify_password(password, stored):
        return None
    return user


def create_login_session(
    session: MongoSession, user_id: int, ttl: timedelta | None = None
) -> str:
    """Create a DB-backed bearer session; returns the raw token."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)
    doc = {
        "_id": _next_id(session.db),
        "user_id": user_id,
        "token_hash": token_hash,
        "created_at": now.isoformat(),
        "expires_at": (now + (ttl or _SESSION_TTL)).isoformat(),
        "is_active": True,
    }
    session["sessions"].insert_one(doc)
    return token


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_session(session: MongoSession, token: str) -> dict | None:
    token_hash = _hash_token(token)
    sess = session["sessions"].find_one({"token_hash": token_hash, "is_active": True})
    if sess is None:
        return None
    expires_at = sess.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            expiry = None
        if expiry is not None and expiry < datetime.now(timezone.utc):
            session["sessions"].update_one(
                {"_id": sess["_id"]}, {"$set": {"is_active": False}}
            )
            return None
    user = session["users"].find_one({"_id": sess.get("user_id")})
    if user is None:
        return None
    user["_session"] = sess
    return user


def revoke_session(session: MongoSession, token: str) -> None:
    token_hash = _hash_token(token)
    session["sessions"].update_many(
        {"token_hash": token_hash, "is_active": True},
        {"$set": {"is_active": False}},
    )


# ---------------------------------------------------------------------------
# Product helpers
# ---------------------------------------------------------------------------

def get_product(session: MongoSession, product_id: int) -> dict | None:
    return session["products"].find_one({"_id": product_id})


def list_products(
    session: MongoSession,
    *,
    seller_id: int | None = None,
    category: str | None = None,
    query_text: str | None = None,
    limit: int = 100,
) -> list[dict]:
    query: dict = {}
    if seller_id is not None:
        query["seller_id"] = seller_id
    if category:
        query["category"] = category
    if query_text:
        query["name"] = {"$regex": query_text, "$options": "i"}
    return list(session["products"].find(query).limit(limit))


def create_product(session: MongoSession, doc: dict) -> dict:
    doc.setdefault("rating", 0.0)
    doc.setdefault("description", "")
    doc.setdefault("category", "Vegetable")
    return _insert_one("products", doc, session.db)


def update_product(session: MongoSession, product_id: int, updates: dict) -> dict | None:
    session["products"].update_one({"_id": product_id}, {"$set": updates})
    return session["products"].find_one({"_id": product_id})


# ---------------------------------------------------------------------------
# LiveStream helpers
# ---------------------------------------------------------------------------

def get_stream(session: MongoSession, stream_id: int) -> dict | None:
    return session["live_streams"].find_one({"_id": stream_id})


def list_streams(
    session: MongoSession,
    *,
    seller_id: int | None = None,
    active_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    query: dict = {}
    if seller_id is not None:
        query["seller_id"] = seller_id
    if active_only:
        query["is_active"] = True
    cursor = session["live_streams"].find(query)
    if not active_only:
        cursor = cursor.sort("started_at", -1)
    return list(cursor.limit(limit))


def create_stream(session: MongoSession, doc: dict) -> dict:
    return _insert_one("live_streams", doc, session.db)


def update_stream(session: MongoSession, stream_id: int, updates: dict) -> dict | None:
    session["live_streams"].update_one({"_id": stream_id}, {"$set": updates})
    return session["live_streams"].find_one({"_id": stream_id})


# ---------------------------------------------------------------------------
# ChatMessage helpers
# ---------------------------------------------------------------------------

def get_messages(
    session: MongoSession,
    stream_id: int,
    limit: int = 100,
) -> list[dict]:
    return list(
        session["chat_messages"]
        .find({"stream_id": stream_id})
        .sort("timestamp", -1)
        .limit(limit)
    )


def create_message(session: MongoSession, doc: dict) -> dict:
    return _insert_one("chat_messages", doc, session.db)


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------

def get_reviews(session: MongoSession, product_id: int, limit: int = 100) -> list[dict]:
    return list(
        session["reviews"]
        .find({"product_id": product_id})
        .sort("created_at", -1)
        .limit(limit)
    )


def create_review(session: MongoSession, doc: dict) -> dict:
    return _insert_one("reviews", doc, session.db)


def update_product_rating(session: MongoSession, product_id: int) -> float | None:
    reviews = list(session["reviews"].find({"product_id": product_id}))
    if not reviews:
        return None
    avg = sum(r.get("rating", 0) for r in reviews) / len(reviews)
    session["products"].update_one({"_id": product_id}, {"$set": {"rating": round(avg, 2)}})
    return avg


# ---------------------------------------------------------------------------
# Order helpers
# ---------------------------------------------------------------------------

def get_order(session: MongoSession, order_id: int) -> dict | None:
    return session["orders"].find_one({"_id": order_id})


def list_orders(
    session: MongoSession,
    *,
    buyer_id: int | None = None,
    product_id: int | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict]:
    query: dict = {}
    if buyer_id is not None:
        query["buyer_id"] = buyer_id
    if product_id is not None:
        query["product_id"] = product_id
    if status:
        query["status"] = status
    return list(
        session["orders"].find(query).sort("created_at", -1).limit(limit)
    )


def create_order_doc(session: MongoSession, doc: dict) -> dict:
    return _insert_one("orders", doc, session.db)


def decrement_stock(session: MongoSession, product_id: int, amount: float) -> dict | None:
    return session["products"].find_one_and_update(
        {"_id": product_id},
        {"$inc": {"stock_kg": -amount}},
        return_document=pymongo.ReturnDocument.AFTER,
    )


# ---------------------------------------------------------------------------
# PriceLog helpers
# ---------------------------------------------------------------------------

def create_price_log(session: MongoSession, doc: dict) -> dict:
    return _insert_one("price_logs", doc, session.db)


def list_price_logs(session: MongoSession, product_id: int, limit: int = 100) -> list[dict]:
    return list(
        session["price_logs"]
        .find({"product_id": product_id})
        .sort("created_at", 1)
        .limit(limit)
    )
