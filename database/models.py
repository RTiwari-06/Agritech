"""MongoDB document helpers — replaces SQLAlchemy ORM models.

Keeps the same to_dict() shapes the REST API and frontend expect, plus
the enum definitions.  No ORM — collections are accessed directly via
``session['collection_name']`` inside ``with session_scope():``.
"""

from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Enums (unchanged from original — stored as string values in MongoDB)
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    SELLER = "seller"
    BUYER = "buyer"


class ProductCategory(str, Enum):
    GRAIN = "Grain"
    VEGETABLE = "Vegetable"
    FRUIT = "Fruit"
    ORGANIC = "Organic"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class IntentTag(str, Enum):
    PRICE_INQUIRY = "PRICE_INQUIRY"
    QUALITY_INQUIRY = "QUALITY_INQUIRY"
    DELIVERY_INQUIRY = "DELIVERY_INQUIRY"
    GENERAL_CHAT = "GENERAL_CHAT"


# ---------------------------------------------------------------------------
# Document factories — create MongoDB documents from API payloads
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_user_doc(
    *,
    username: str,
    email: str,
    role: str = "buyer",
    location: str = "",
) -> dict:
    return {
        "username": username[:80],
        "email": email,
        "role": role,
        "location": location[:120],
        "created_at": _now_iso(),
    }


def make_product_doc(
    *,
    seller_id: int,
    name: str,
    category: str = "Vegetable",
    description: str = "",
    price: float = 0.0,
    stock_kg: float = 0.0,
    rating: float = 0.0,
) -> dict:
    return {
        "seller_id": seller_id,
        "name": name[:160],
        "category": category,
        "description": description,
        "price": float(price),
        "stock_kg": float(stock_kg),
        "quantity": float(stock_kg),  # backward compat
        "rating": float(rating),
        "created_at": _now_iso(),
    }


def make_live_stream_doc(
    *,
    seller_id: int,
    stream_title: str,
    is_active: bool = True,
) -> dict:
    return {
        "seller_id": seller_id,
        "stream_title": stream_title[:200],
        "is_active": is_active,
        "started_at": _now_iso(),
    }


def make_chat_message_doc(
    *,
    stream_id: int,
    user_id: int,
    message_text: str,
    sentiment_score: float = 0.0,
    sentiment_label: str = "NEUTRAL",
    intent_tag: str = "GENERAL_CHAT",
) -> dict:
    return {
        "stream_id": stream_id,
        "user_id": user_id,
        "message_text": message_text,
        "sentiment_score": float(sentiment_score),
        "sentiment_label": sentiment_label,
        "intent_tag": intent_tag,
        "timestamp": _now_iso(),
    }


def make_review_doc(
    *,
    product_id: int,
    user_id: int,
    review_text: str = "",
    rating: int = 0,
    sentiment_score: float = 0.0,
) -> dict:
    return {
        "product_id": product_id,
        "user_id": user_id,
        "review_text": review_text,
        "rating": int(rating),
        "sentiment_score": float(sentiment_score),
        "created_at": _now_iso(),
    }


def make_order_doc(
    *,
    buyer_id: int,
    product_id: int,
    quantity_kg: float,
    total_price: float,
    status: str = "confirmed",
) -> dict:
    return {
        "buyer_id": buyer_id,
        "product_id": product_id,
        "quantity_kg": float(quantity_kg),
        "quantity": float(quantity_kg),  # backward compat
        "total_price": float(total_price),
        "status": status,
        "created_at": _now_iso(),
    }


def make_price_log_doc(
    *,
    product_id: int,
    old_price: float,
    new_price: float,
    reason: str = "",
) -> dict:
    return {
        "product_id": product_id,
        "old_price": float(old_price),
        "new_price": float(new_price),
        "reason": reason,
        "created_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# to_dict() helpers — shape MongoDB documents for the API response
# ---------------------------------------------------------------------------

def user_to_dict(doc: dict, include_seller: bool = False) -> dict:
    return {
        "id": doc.get("_id"),
        "username": doc.get("username", ""),
        "email": doc.get("email", ""),
        "role": doc.get("role", "buyer"),
        "location": doc.get("location", ""),
        "created_at": doc.get("created_at"),
    }


def product_to_dict(doc: dict, include_seller: bool = True) -> dict:
    data = {
        "id": doc.get("_id"),
        "seller_id": doc.get("seller_id"),
        "name": doc.get("name", ""),
        "category": doc.get("category", "Vegetable"),
        "description": doc.get("description", ""),
        "price": doc.get("price", 0.0),
        "stock_kg": doc.get("stock_kg", 0.0),
        "quantity": doc.get("stock_kg", 0.0),  # backward compat
        "rating": doc.get("rating", 0.0),
        "created_at": doc.get("created_at"),
    }
    if include_seller and doc.get("seller_id"):
        # seller info is usually attached by the caller via lookup
        seller = doc.get("seller")
        if seller:
            data["seller"] = {
                "id": seller.get("_id"),
                "username": seller.get("username", ""),
                "location": seller.get("location", ""),
            }
    return data


def stream_to_dict(doc: dict) -> dict:
    return {
        "id": doc.get("_id"),
        "seller_id": doc.get("seller_id"),
        "stream_title": doc.get("stream_title", ""),
        "is_active": doc.get("is_active", False),
        "started_at": doc.get("started_at"),
    }


def chat_message_to_dict(doc: dict, username: str | None = None) -> dict:
    return {
        "id": doc.get("_id"),
        "stream_id": doc.get("stream_id"),
        "user_id": doc.get("user_id"),
        "username": username or doc.get("username", ""),
        "message_text": doc.get("message_text", ""),
        "sentiment_score": doc.get("sentiment_score", 0.0),
        "sentiment_label": doc.get("sentiment_label", "NEUTRAL"),
        "intent_tag": doc.get("intent_tag", "GENERAL_CHAT"),
        "timestamp": doc.get("timestamp"),
    }


def review_to_dict(doc: dict, product_name: str | None = None, username: str | None = None) -> dict:
    score = doc.get("sentiment_score", 0.0)
    label = "POSITIVE" if score > 0.15 else ("NEGATIVE" if score < -0.15 else "NEUTRAL")
    return {
        "id": doc.get("_id"),
        "product_id": doc.get("product_id"),
        "product_name": product_name or doc.get("product_name", ""),
        "user_id": doc.get("user_id"),
        "username": username or doc.get("username", ""),
        "review_text": doc.get("review_text", ""),
        "rating": doc.get("rating", 0),
        "sentiment_score": score,
        "sentiment_label": label.lower(),
        "created_at": doc.get("created_at"),
    }


def order_to_dict(doc: dict, buyer_username: str | None = None, product_name: str | None = None) -> dict:
    qty = doc.get("quantity_kg", doc.get("quantity", 0))
    unit_price = round(doc.get("total_price", 0) / qty, 2) if qty else 0
    return {
        "id": doc.get("_id"),
        "buyer_id": doc.get("buyer_id"),
        "buyer_username": buyer_username or doc.get("buyer_username", ""),
        "product_id": doc.get("product_id"),
        "product_name": product_name or doc.get("product_name", ""),
        "quantity_kg": qty,
        "quantity": qty,  # backward compat
        "unit_price": unit_price,
        "total_price": doc.get("total_price", 0.0),
        "status": doc.get("status", "pending"),
        "created_at": doc.get("created_at"),
    }


def price_log_to_dict(doc: dict, product_name: str | None = None) -> dict:
    return {
        "id": doc.get("_id"),
        "product_id": doc.get("product_id"),
        "product_name": product_name or doc.get("product_name", ""),
        "old_price": doc.get("old_price", 0.0),
        "new_price": doc.get("new_price", 0.0),
        "reason": doc.get("reason", ""),
        "created_at": doc.get("created_at"),
    }
