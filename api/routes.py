"""REST endpoints — Phase 1: MongoDB fully purged; products served from Redis.

The only data sources are ``database.redis_db`` (connection pool, product
Hashes, capped chat List) and ``optimization.engine dynamic_pricing_engine``
sentiment/engagement scores are computed from the live chat feed and passed
into ``DynamicPricingEngine.calculate_optimal_price()`` for every product.

No session_scope, no pymongo, no in-file mock arrays.
"""

from datetime import datetime, timezone
from io import BytesIO
import math
import uuid
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from database.redis_db import (
    DEFAULT_STREAM_ID,
    create_redis_order,
    get_all_orders,
    get_all_products,
    get_product,
    get_recent_chats,
    get_client,
    get_market_records,
    push_chat_message,
    store_market_records,
    update_product_fields,
)
from market_data.parser import parse_csv, parse_pdf
from nlp.nlp_engine import get_nlp_engine
from optimization.engine import create_pricing_engine
from redis.exceptions import RedisError

api_bp = Blueprint("api", __name__)
MAX_MARKET_REPORT_BYTES = 15 * 1024 * 1024

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

def _chat_signals(
    chats: list[Dict[str, Any]],
    product_id: Any = None,
) -> tuple[float, Optional[float]]:
    """Return ``(avg_sentiment, live_engagement)`` for product ``product_id``.

    Spec §2.2: ``V`` is the mean VADER compound score over the rolling chat
    window *of that product*. When the commodity has no messages of its own
    the shared feed is used so a silent product prices neutrally instead of
    inheriting unrelated chatter.

    * ``avg_sentiment`` — simple mean of stored VADER ``sentiment_score``
      values in [-1, 1]; ``0.0`` when the window is empty.
    * ``live_engagement`` — share of buy-signal intents
      (``PRICE_INQUIRY``/``QUALITY_INQUIRY``) in the window multiplied by its
      velocity frame; ``None`` when the window is empty so the pricing engine
      stays completely neutral instead of punishing a silent market.
    """
    if product_id is not None:
        product_window = [
            chat
            for chat in chats
            if chat.get("product_id") is not None
            and str(chat["product_id"]) == str(product_id)
        ]
        if product_window:
            chats = product_window

    if not chats:
        return 0.0, None

    sentiment_total = 0.0
    buy_signals = 0
    for chat in chats:
        sentiment_total += float(chat.get("sentiment_score", chat.get("sentiment", 0.0)) or 0.0)
        intent = (chat.get("intent_tag") or "")
        if intent in ("PRICE_INQUIRY", "QUALITY_INQUIRY"):
            buy_signals += 1

    avg_sentiment = sentiment_total / len(chats)
    live_engagement = round(min(1.0, buy_signals / len(chats) + 0.3 * min(len(chats) / 20.0, 1.0)), 4)
    return avg_sentiment, live_engagement


def _priced_products() -> list[Dict[str, Any]]:
    """Return all Redis products with current sentiment-driven prices."""
    products = get_all_products()
    chats = get_recent_chats(limit=20)

    results = []
    for product in products:
        day1 = float(product.get("day1_price") or 0.0)
        day2 = float(product.get("day2_price") or 0.0)
        growth = (day2 - day1) / day1 if day1 > 0 else 0.0
        demand_factor = max(0.0, min(1.0, 0.5 + growth * 4.0))
        avg_sentiment, live_engagement = _chat_signals(
            chats, product_id=product["id"]
        )

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
    # Spec §Phase 3.5: buyer_id is not part of the required payload; the demo
    # buyer identity is used when callers omit it.
    buyer_id = str(payload.get("buyer_id") or "").strip() or "demo_buyer"
    if not product_id.isdigit() or int(product_id) < 1:
        return jsonify({"ok": False, "error": "Valid product_id is required"}), 400

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


@api_bp.get("/api/market-prices")
def market_price_history():
    """Return imported market-report history stored in Redis."""
    limit = request.args.get("limit", 500, type=int)
    if limit is None or limit < 1:
        return jsonify({"ok": False, "error": "limit must be between 1 and 1000"}), 400
    try:
        records = get_market_records(
            commodity=request.args.get("commodity") or None,
            market=request.args.get("market") or None,
            limit=min(limit, 1000),
        )
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    return jsonify({"ok": True, "data": records})


@api_bp.post("/api/market-prices/upload")
def upload_market_report():
    """Parse an Agmarknet PDF or canonical market-data CSV into Redis history."""
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Choose a PDF or CSV report to upload"}), 400

    filename = uploaded.filename.replace("\\", "/").rsplit("/", 1)[-1]
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"pdf", "csv"}:
        return jsonify({"ok": False, "error": "Only PDF and CSV reports are supported"}), 400

    content = uploaded.read(MAX_MARKET_REPORT_BYTES + 1)
    if not content:
        return jsonify({"ok": False, "error": "The uploaded report is empty"}), 400
    if len(content) > MAX_MARKET_REPORT_BYTES:
        return jsonify({"ok": False, "error": "Report exceeds the 15 MB upload limit"}), 413

    source = filename
    stream = BytesIO(content)
    try:
        parsed = parse_pdf(stream, source=source) if extension == "pdf" else parse_csv(
            stream, source=source
        )
    except (OSError, ValueError, UnicodeError) as exc:
        return jsonify({"ok": False, "error": f"Could not parse report: {exc}"}), 400

    records = [
        row for row in parsed.records
        if row.get("market_date")
        and any(
            row.get(field) is not None
            for field in ("price_per_kg", "msp_per_kg", "arrival_metric_tonnes")
        )
    ]
    if not records:
        reasons = list(dict.fromkeys(
            str(error.get("reason") or "No usable market rows found")
            for error in parsed.errors
        ))[:5]
        message = "; ".join(reasons) or (
            "No usable dated price, MSP, or arrival rows were found. "
            "PDFs must use the Agmarknet Marketwise Price & Arrival report layout."
        )
        return jsonify({"ok": False, "error": message}), 400

    try:
        saved = store_market_records(records)
    except RedisError:
        return jsonify({"ok": False, "error": "Redis unavailable"}), 503
    return jsonify(
        {
            "ok": True,
            "data": {
                **saved,
                "rows_parsed": len(records),
                "source": source,
            },
        }
    ), 201


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
    """Buyer chat ingestion — VADER sentiment + intent -> Redis List -> reprice.

    Phase 3.4 contract: receives ``{"stream_id", "product_id", "message"}``,
    scores the message with VADER, recalculates ``P_dynamic`` for the targeted
    commodity, persists the recalculated price back into the ``product:<id>``
    Hash, and pushes the spec §3.4-shaped payload onto ``stream:<id>:chats``
    (default stream ``1``). The updated scores immediately feed
    ``_chat_signals`` on the next ``GET /api/products`` poll within one
    3-second UI cycle.
    """
    payload = request.get_json(silent=True) or {}
    message_text = (payload.get("message") or payload.get("message_text") or "").strip()
    if not message_text:
        return jsonify({"ok": False, "error": "message or message_text required"}), 400

    sentiment = _chat_nlp.analyze_sentiment(message_text)
    intent = _chat_nlp.detect_intent(message_text)
    sent_at = datetime.now(timezone.utc)

    message_doc = {
        "id": uuid.uuid4().hex[:12],
        "stream_id": str(payload.get("stream_id") or DEFAULT_STREAM_ID),
        "product_id": str(payload["product_id"]) if payload.get("product_id") is not None else None,
        "user_id": payload.get("user_id") or 0,
        "username": (payload.get("username") or "").strip() or "demo_buyer",
        "is_host": bool(payload.get("is_host")),
        # ``message``/``sentiment`` are the spec §3.4 names of the internal
        # ``message_text``/``sentiment_score`` aliases — both stored.
        "message": message_text,
        "message_text": message_text,
        "sentiment": sentiment["sentiment_score"],
        "sentiment_score": sentiment["sentiment_score"],
        "sentiment_label": sentiment["sentiment_label"],
        "intent_tag": intent,
        "timestamp": int(sent_at.timestamp()),
        "created_at": sent_at.isoformat(),
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
    # Phase 3.4: persist the recalculated P_dynamic (and clamp state) into the
    # product Hash so the stored commodity record reflects the live market.
    if updated_product is not None and message_doc["product_id"]:
        try:
            update_product_fields(
                message_doc["product_id"],
                {
                    "dynamic_price": float(updated_product["dynamic_price"]),
                    "capped": "true" if updated_product["capped"] else "false",
                },
            )
        except RedisError:
            return jsonify({"ok": False, "error": "Redis unavailable"}), 503
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
