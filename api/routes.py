"""REST endpoints — Phase 1: MongoDB fully purged; products served from Redis.

The only data sources are ``database.redis_db`` (connection pool, product
Hashes, capped chat List) and ``optimization.engine dynamic_pricing_engine``
sentiment/engagement scores are computed from the live chat feed and passed
into ``DynamicPricingEngine.calculate_optimal_price()`` for every product.

No session_scope, no pymongo, no in-file mock arrays.
"""

from datetime import datetime, timezone
import math
import uuid
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from database.redis_db import (
    create_redis_order,
    get_all_orders,
    get_all_products,
    get_product,
    get_recent_chats,
    get_client,
    push_chat_message,
    update_product_fields,
)
from nlp.nlp_engine import get_nlp_engine
from optimization.engine import create_pricing_engine
from redis.exceptions import RedisError

api_bp = Blueprint("api", __name__)

_pricing_engine = create_pricing_engine()
_chat_nlp = get_nlp_engine()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@api_bp.get("/health")
def health():
    try:
        redis_available = bool(get_client().ping())
    except RedisError:
        redis_available = False
    database_status = {
        "backend": "Redis",
        "name": "127.0.0.1:6379/0",
        "connected": redis_available,
    }
    return jsonify(
        {
            "ok": True,
            "status": "ok",
            "db": redis_available,
            "redis": redis_available,
            "database": database_status,
            "data": {
                "status": "ok",
                "db": redis_available,
                "redis": redis_available,
                "database": database_status,
            },
        }
    )


# ---------------------------------------------------------------------------
# Chat-derived signals
# ---------------------------------------------------------------------------

def _chat_signals(chats: list[Dict[str, Any]]) -> tuple[float, Optional[float]]:
    """Return ``(avg_sentiment, live_engagement)`` from the recent chat feed.

    * ``avg_sentiment`` — simple mean of stored VADER ``sentiment_score``
      values in [-1, 1]; ``0.0`` when the feed is empty (neutral pricing).
    * ``live_engagement`` — share of buy-signal intents
      (``PRICE_INQUIRY``/``QUALITY_INQUIRY``) in the feed multiplied by its
      velocity frame; ``None`` when the feed is empty so the pricing engine
      stays completely neutral instead of punishing a silent market.
    """
    if not chats:
        return 0.0, None

    sentiment_total = 0.0
    buy_signals = 0
    for chat in chats:
        sentiment_total += float(chat.get("sentiment_score", 0.0) or 0.0)
        intent = (chat.get("intent_tag") or "")
        if intent in ("PRICE_INQUIRY", "QUALITY_INQUIRY"):
            buy_signals += 1

    avg_sentiment = sentiment_total / len(chats)
    live_engagement = round(min(1.0, buy_signals / len(chats) + 0.3 * min(len(chats) / 20.0, 1.0)), 4)
    return avg_sentiment, live_engagement


def _priced_products() -> list[Dict[str, Any]]:
    """Return all Redis products with current sentiment-driven prices."""
    products = get_all_products()
    avg_sentiment, live_engagement = _chat_signals(get_recent_chats(limit=20))

    results = []
    for product in products:
        day1 = float(product.get("day1_price") or 0.0)
        day2 = float(product.get("day2_price") or 0.0)
        growth = (day2 - day1) / day1 if day1 > 0 else 0.0
        demand_factor = max(0.0, min(1.0, 0.5 + growth * 4.0))

        calc = _pricing_engine.calculate_optimal_price(
            product_id=product["id"],
            base_price=day2,
            demand_factor=demand_factor,
            stock_kg=float(product.get("stock_kg") or 0.0),
            sentiment_score=avg_sentiment,
            live_engagement=live_engagement,
            capped=bool(product.get("capped")),
        )
        results.append(
            {
                "id": product["id"],
                "name": product["name"],
                "category": product["category"],
                "day1_price": round(day1, 2),
                "day2_price": round(day2, 2),
                "dynamic_price": calc.optimal_price,
                "price_change_pct": round(calc.change_pct * 100, 2),
                "stock_kg": float(product.get("stock_kg") or 0.0),
                "capped": calc.capped,
                "avg_sentiment": round(avg_sentiment, 4),
                "live_engagement": live_engagement,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Products (Redis Hashes -> dynamic pricing -> JSON)
# ---------------------------------------------------------------------------

@api_bp.get("/api/products")
def list_products():
    """All APMC products with Redis-driven dynamic prices and chat signals."""
    try:
        products = _priced_products()
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    return jsonify({"ok": True, "data": products})


@api_bp.get("/api/streams")
def list_streams():
    """List streams from Redis, with a default live stream for an empty set."""
    try:
        client = get_client()
        stream_ids = client.smembers("streams:all")
        if not stream_ids:
            return jsonify(
                {
                    "ok": True,
                    "data": [
                        {
                            "id": "1",
                            "title": "Bangalore APMC Live Auction",
                            "status": "active",
                        }
                    ],
                }
            )

        streams: list[Dict[str, Any]] = []
        for stream_id in sorted(stream_ids):
            stream_key = (
                stream_id if str(stream_id).startswith("stream:")
                else f"stream:{stream_id}"
            )
            stream = client.hgetall(stream_key)
            if not stream:
                continue
            active_value = stream.get("is_active", "true")
            status = stream.get(
                "status",
                "active" if str(active_value).lower() in ("1", "true", "yes") else "ended",
            )
            streams.append(
                {
                    "id": str(stream.get("id") or str(stream_id).removeprefix("stream:")),
                    "title": stream.get("title") or stream.get("stream_title") or "Untitled stream",
                    "status": status,
                }
            )
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503

    return jsonify({"ok": True, "data": streams})


@api_bp.put("/api/products/<product_id>")
def update_product(product_id: str):
    """Update seller-controlled stock, baseline price, or clamp state."""
    if not product_id.isdigit() or int(product_id) < 1:
        return jsonify({"ok": False, "error": "Invalid product_id"}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return jsonify({"ok": False, "error": "At least one product field is required"}), 400
    allowed_fields = {"stock_kg", "day2_price", "capped"}
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        return jsonify(
            {"ok": False, "error": f"Unsupported fields: {', '.join(sorted(unknown_fields))}"}
        ), 400

    fields: Dict[str, Any] = {}
    for field in ("stock_kg", "day2_price"):
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, bool):
            return jsonify({"ok": False, "error": f"{field} must be a number"}), 400
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return jsonify({"ok": False, "error": f"{field} must be a number"}), 400
        if not math.isfinite(numeric_value) or numeric_value < 0:
            return jsonify({"ok": False, "error": f"{field} must be a finite non-negative number"}), 400
        if field == "day2_price" and numeric_value == 0:
            return jsonify({"ok": False, "error": "day2_price must be greater than zero"}), 400
        fields[field] = numeric_value

    if "capped" in payload:
        if not isinstance(payload["capped"], bool):
            return jsonify({"ok": False, "error": "capped must be a boolean"}), 400
        fields["capped"] = "true" if payload["capped"] else "false"

    try:
        if not update_product_fields(product_id, fields):
            return jsonify({"ok": False, "error": "Product not found"}), 404
        updated = next(
            (
                product
                for product in _priced_products()
                if str(product["id"]) == product_id
            ),
            None,
        )
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503

    if updated is None:
        return jsonify({"ok": False, "error": "Product not found"}), 404
    return jsonify({"ok": True, "data": updated})


@api_bp.post("/api/orders")
def create_order():
    """Persist an order and reserve its inventory atomically in Redis."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "JSON request body required"}), 400

    raw_product_id = payload.get("product_id")
    product_id = str(raw_product_id).strip() if raw_product_id is not None else ""
    buyer_id = payload.get("buyer_id")
    buyer_id = buyer_id.strip() if isinstance(buyer_id, str) else ""
    if not product_id.isdigit() or int(product_id) < 1:
        return jsonify({"ok": False, "error": "Valid product_id is required"}), 400
    if not buyer_id:
        return jsonify({"ok": False, "error": "buyer_id is required"}), 400

    quantity = payload.get("quantity_kg")
    if isinstance(quantity, bool):
        return jsonify({"ok": False, "error": "quantity_kg must be a positive number"}), 400
    try:
        quantity_kg = float(quantity)
    except (TypeError, ValueError, OverflowError):
        return jsonify({"ok": False, "error": "quantity_kg must be a positive number"}), 400
    if not math.isfinite(quantity_kg) or quantity_kg <= 0:
        return jsonify({"ok": False, "error": "quantity_kg must be a positive number"}), 400

    try:
        product = get_product(product_id)
        if product is None:
            return jsonify({"ok": False, "error": "Product not found"}), 404
        priced_product = next(
            (
                item
                for item in _priced_products()
                if str(item["id"]) == product_id
            ),
            None,
        )
        if priced_product is None:
            return jsonify({"ok": False, "error": "Product not found"}), 404

        order_id = str(uuid.uuid4())
        price_per_kg = float(priced_product["dynamic_price"])
        order_status, remaining_stock = create_redis_order(
            product_id,
            quantity_kg,
            buyer_id,
            price_per_kg,
            order_id,
        )
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503

    if order_status == "not_found":
        return jsonify({"ok": False, "error": "Product not found"}), 404
    if order_status == "insufficient":
        return jsonify({"ok": False, "error": "Insufficient stock"}), 400
    if remaining_stock is None:
        return jsonify({"ok": False, "error": "Order could not be completed"}), 500

    order = {
        "id": order_id,
        "product_id": product_id,
        "quantity_kg": quantity_kg,
        "buyer_id": buyer_id,
        "price_per_kg": price_per_kg,
        "total_price": round(quantity_kg * price_per_kg, 2),
        "remaining_stock_kg": remaining_stock,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify({"ok": True, "data": order}), 201


@api_bp.get("/api/orders")
def list_orders():
    """List persistent order receipts from Redis."""
    try:
        orders = get_all_orders()
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    return jsonify({"ok": True, "data": orders})


@api_bp.get("/api/analytics")
def market_analytics():
    """Aggregate live inventory and pricing metrics directly from Redis."""
    try:
        products = _priced_products()
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503

    total_stock_kg = sum(float(product.get("stock_kg") or 0.0) for product in products)
    surge_values = [
        (float(product.get("dynamic_price") or 0.0) - float(product["day2_price"]))
        / float(product["day2_price"])
        * 100.0
        for product in products
        if float(product.get("day2_price") or 0.0) > 0
    ]
    metrics = {
        "total_products": len(products),
        "total_stock_kg": round(total_stock_kg, 2),
        "avg_surge_pct": round(sum(surge_values) / len(surge_values), 2)
        if surge_values
        else 0.0,
        "low_stock_count": sum(
            float(product.get("stock_kg") or 0.0) < 50.0 for product in products
        ),
    }
    return jsonify({"ok": True, **metrics, "data": metrics})


# ---------------------------------------------------------------------------
# Chat feed (Redis List) — REST mirror of the SocketIO live feed
# ---------------------------------------------------------------------------

@api_bp.get("/api/streams/chats")
def recent_chats():
    """Newest analysed chat messages (default 20) for polling clients."""
    limit = request.args.get("limit", 20, type=int)
    try:
        chats = get_recent_chats(limit=limit)
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    return jsonify({"ok": True, "data": chats})


@api_bp.post("/api/streams/chats")
def post_chat():
    """Buyer chat ingestion — VADER sentiment + keyword intent -> Redis List.

    The pushed scores immediately feed ``_chat_signals`` on the next
    ``GET /api/products`` poll, so buy-signal intent chatter visibly nudges
    the dynamic prices within one 3-second UI cycle.
    """
    payload = request.get_json(silent=True) or {}
    message_text = (payload.get("message") or payload.get("message_text") or "").strip()
    if not message_text:
        return jsonify({"ok": False, "error": "message or message_text required"}), 400

    sentiment = _chat_nlp.analyze_sentiment(message_text)
    intent = _chat_nlp.detect_intent(message_text)

    message_doc = {
        "stream_id": payload.get("stream_id") or "active",
        "product_id": str(payload["product_id"]) if payload.get("product_id") is not None else None,
        "user_id": payload.get("user_id") or 0,
        "username": (payload.get("username") or "").strip() or "demo_buyer",
        "is_host": bool(payload.get("is_host")),
        "message_text": message_text,
        "sentiment_score": sentiment["sentiment_score"],
        "sentiment_label": sentiment["sentiment_label"],
        "intent_tag": intent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        push_chat_message(message_doc)
        priced_products = _priced_products()
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    updated_product = next(
        (
            product
            for product in priced_products
            if str(product["id"]) == str(message_doc["product_id"])
        ),
        None,
    )
    return jsonify(
        {
            "ok": True,
            "data": message_doc,
            "product": updated_product,
            "dynamic_price": (
                updated_product["dynamic_price"] if updated_product else None
            ),
        }
    ), 201
