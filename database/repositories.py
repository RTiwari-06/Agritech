"""MongoDB repository layer — encapsulates all database operations.

Every function takes a :class:`MongoSession` (from ``with session_scope()``)
as its first argument so route code reads::

    with session_scope() as s:
        seller = get_user_by_id(s, seller_id)
        products = list_products(s, seller_id=seller_id)

This mirrors the old SQLAlchemy session API while talking to MongoDB behind
the scenes.  Documents use integer ``_id`` values so the REST API shapes
are unchanged.
"""

from datetime import datetime, timezone
from typing import Any

from database.db import (
    MongoSession,
    _insert_one,
    _next_int_id,
    get_db,
)
from database.models import (
    UserRole,
    ProductCategory,
    OrderStatus,
    SentimentLabel,
    IntentTag,
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_id(session: MongoSession, user_id: int) -> dict | None:
    return session["users"].find_one({"_id": user_id})


def get_user_by_email(session: MongoSession, email: str) -> dict | None:
    return session["users"].find_one({"email": email})


def get_user_by_username(session: MongoSession, username: str) -> dict | None:
    return session["users"].find_one({"username": username})


def create_user(session: MongoSession, doc: dict) -> dict:
    doc.setdefault("role", UserRole.BUYER.value)
    doc.setdefault("location", "")
    return _insert_one("users", doc, session.db)


def update_user(session: MongoSession, user_id: int, updates: dict) -> dict | None:
    session["users"].update_one({"_id": user_id}, {"$set": updates})
    return session["users"].find_one({"_id": user_id})


def list_users(session: MongoSession, limit: int = 100) -> list[dict]:
    return list(session["users"].find().sort("_id", 1).limit(limit))


def user_exists(session: MongoSession, email: str | None = None, username: str | None = None) -> bool:
    query: dict = {}
    if email:
        query["email"] = email
    if username:
        query["username"] = username
    return session["users"].find_one(query) is not None


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def get_product_by_id(session: MongoSession, product_id: int) -> dict | None:
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
    doc.setdefault("category", ProductCategory.VEGETABLE.value)
    doc.setdefault("stock_kg", 0.0)
    return _insert_one("products", doc, session.db)


def update_product(session: MongoSession, product_id: int, updates: dict) -> dict | None:
    session["products"].update_one({"_id": product_id}, {"$set": updates})
    return session["products"].find_one({"_id": product_id})


def delete_product(session: MongoSession, product_id: int) -> bool:
    result = session["products"].delete_one({"_id": product_id})
    return result.deleted_count > 0


# ---------------------------------------------------------------------------
# Live Streams
# ---------------------------------------------------------------------------

def get_stream_by_id(session: MongoSession, stream_id: int) -> dict | None:
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
    if active_only or seller_id is not None:
        pass
    else:
        cursor = cursor.sort("started_at", -1)
    return list(cursor.limit(limit))


def create_stream(session: MongoSession, doc: dict) -> dict:
    return _insert_one("live_streams", doc, session.db)


def update_stream(session: MongoSession, stream_id: int, updates: dict) -> dict | None:
    session["live_streams"].update_one({"_id": stream_id}, {"$set": updates})
    return session["live_streams"].find_one({"_id": stream_id})


# ---------------------------------------------------------------------------
# Chat Messages
# ---------------------------------------------------------------------------

def get_chat_messages(
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


def create_chat_message(session: MongoSession, doc: dict) -> dict:
    doc.setdefault("sentiment_score", 0.0)
    doc.setdefault("sentiment_label", SentimentLabel.NEUTRAL.value)
    doc.setdefault("intent_tag", IntentTag.GENERAL_CHAT.value)
    return _insert_one("chat_messages", doc, session.db)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def get_review_by_id(session: MongoSession, review_id: int) -> dict | None:
    return session["reviews"].find_one({"_id": review_id})


def list_reviews(session: MongoSession, product_id: int, limit: int = 100) -> list[dict]:
    return list(
        session["reviews"]
        .find({"product_id": product_id})
        .sort("created_at", -1)
        .limit(limit)
    )


def get_reviews_for_product(session: MongoSession, product_id: int) -> list[dict]:
    return list(session["reviews"].find({"product_id": product_id}))


def create_review(session: MongoSession, doc: dict) -> dict:
    doc.setdefault("sentiment_score", 0.0)
    return _insert_one("reviews", doc, session.db)


def update_product_rating(session: MongoSession, product_id: int) -> float | None:
    """Recalculate and update product rating from all its reviews. Returns new rating."""
    reviews = list(session["reviews"].find({"product_id": product_id}))
    if not reviews:
        return None
    avg = sum(r.get("rating", 0) for r in reviews) / len(reviews)
    session["products"].update_one({"_id": product_id}, {"$set": {"rating": round(avg, 2)}})
    return avg


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def get_order_by_id(session: MongoSession, order_id: int) -> dict | None:
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


def create_order(session: MongoSession, doc: dict) -> dict:
    return _insert_one("orders", doc, session.db)


def decrement_product_stock(session: MongoSession, product_id: int, amount: float) -> dict | None:
    result = session["products"].find_one_and_update(
        {"_id": product_id},
        {"$inc": {"stock_kg": -amount}},
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return result


# ---------------------------------------------------------------------------
# Price Logs
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


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def get_collection(session: MongoSession, name: str) -> Collection:
    """Return the raw pymongo collection for advanced queries."""
    return session.db[name]
