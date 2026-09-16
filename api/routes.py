"""REST endpoints for the marketplace, analytics and optimisation pipelines.

All database access goes through ``session_scope()`` which yields a
:class:`MongoSession` whose ``session['collection']`` gives a
pymongo collection.  Documents use integer ``_id`` values so the REST
API and frontend continue to work with the same shapes as before.
"""

from flask import Blueprint, request
import pymongo

from database.db import session_scope
from database.models import (
    IntentTag,
    OrderStatus,
    ProductCategory,
    SentimentLabel,
    UserRole,
    make_user_doc,
    make_product_doc,
    make_live_stream_doc,
    make_chat_message_doc,
    make_review_doc,
    make_order_doc,
    make_price_log_doc,
    user_to_dict,
    product_to_dict,
    stream_to_dict,
    chat_message_to_dict,
    review_to_dict,
    order_to_dict,
    price_log_to_dict,
)
from nlp.nlp_engine import get_nlp_engine
from optimization.engine import (
    DynamicPricingEngine,
    RecommendationEngine,
    DemandForecaster,
)
from optimization.metrics import demand_score, rating_summary, revenue_by_day, sales_summary
from optimization.recommendation import build_engine_from_products
from api.socketio_events import STREAM_ROOM_PREFIX, build_stream_message_data

api_bp = Blueprint("api", __name__)

_nlp_engine = get_nlp_engine()
_pricing_engine = DynamicPricingEngine()
_recommendation_engine = RecommendationEngine()
_forecaster = DemandForecaster()


def _ok(data, status=200):
    return {"ok": True, "data": data}, status


def _err(message, status=400):
    return {"ok": False, "error": message}, status


def _payload():
    return request.get_json(silent=True) or {}


def _serialize_product(session, product, include_dynamic_price=True):
    """Serialize a product document with optional dynamic pricing."""
    data = product_to_dict(product)
    if include_dynamic_price:
        # Gather reviews + orders for this product
        reviews = list(session["reviews"].find({"product_id": product["_id"]}))
        avg_sentiment = (
            sum(r.get("sentiment_score", 0.0) for r in reviews) / len(reviews)
            if reviews
            else 0.0
        )
        orders = list(session["orders"].find({"product_id": product["_id"]}))
        demand = demand_score(orders)

        calc = _pricing_engine.calculate_optimal_price(
            product_id=product["_id"],
            base_price=product.get("price", 0.0),
            demand_factor=demand,
            stock_kg=product.get("stock_kg", 0.0),
            sentiment_score=avg_sentiment,
        )
        data["dynamic_price"] = calc.optimal_price
        data["price_change_pct"] = calc.change_pct
    return data


def _lookup_seller(session, seller_id: int):
    """Return seller document, or None."""
    return session["users"].find_one({"_id": seller_id})


def _lookup_product(session, product_id: int):
    return session["products"].find_one({"_id": product_id})


def _lookup_user(session, user_id: int):
    return session["users"].find_one({"_id": user_id})


def _resolve_seller(
    session,
    seller_id: int | None = None,
    seller_obj: dict | None = None,
):
    """Resolve a seller from seller_id or seller_obj (dict with email/name).

    Returns the seller document, or None.
    Creates the seller if seller_obj is provided and no matching user exists.
    """
    if seller_id:
        seller = session["users"].find_one({"_id": seller_id})
        if seller and seller.get("role") == UserRole.SELLER.value:
            return seller
        return None

    if seller_obj:
        email = seller_obj.get("email", "").strip()
        name = seller_obj.get("name", "").strip()[:80]
        location = (seller_obj.get("location") or "").strip()[:120]

        # Try email first
        if email:
            seller = session["users"].find_one({"email": email})
            if seller:
                return seller
            # Create new seller with this email
            seller = make_user_doc(
                username=name or "Seller",
                email=email,
                role=UserRole.SELLER.value,
                location=location,
            )
            seller = _upsert_user_doc(session, seller)
            return seller

        # No email — try username
        if name:
            seller = session["users"].find_one({"username": name})
            if seller:
                return seller
            email = f"{name.lower().replace(' ', '_')}@example.com"
            seller = make_user_doc(
                username=name,
                email=email,
                role=UserRole.SELLER.value,
                location=location,
            )
            seller = _upsert_user_doc(session, seller)
            return seller

    return None


def _resolve_buyer(
    session,
    buyer_id: int | None = None,
    buyer_name: str | None = None,
    buyer_phone: str | None = None,
):
    """Resolve a buyer from buyer_id or buyer_name.

    Creates the buyer if buyer_name is provided and no matching user exists.
    Returns the buyer document, or None.
    """
    if buyer_id:
        buyer = session["users"].find_one({"_id": buyer_id})
        if buyer and buyer.get("role") == UserRole.BUYER.value:
            return buyer
        return None

    if buyer_name:
        buyer = session["users"].find_one({"username": buyer_name.strip()[:80]})
        if buyer:
            return buyer
        email = buyer_phone or f"{buyer_name.lower().replace(' ', '_')}@example.com"
        buyer = make_user_doc(
            username=buyer_name.strip()[:80],
            email=email,
            role=UserRole.BUYER.value,
            location="",
        )
        buyer = _upsert_user_doc(session, buyer)
        return buyer

    return None


def _upsert_user_doc(session, doc: dict):
    """Insert a user or return existing one matched by email/username."""
    email = doc.get("email", "").strip()
    username = doc.get("username", "").strip()

    if email:
        existing = session["users"].find_one({"email": email})
        if existing:
            return existing
    if username:
        existing = session["users"].find_one({"username": username})
        if existing:
            return existing

    doc["_id"] = _next_id(session.db)
    doc.setdefault("created_at", _now_iso())
    session["users"].insert_one(doc)
    return doc


def _next_id(db):
    counters = db["_counters"]
    result = counters.find_one_and_update(
        {"_id": "next_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return int(result["seq"])


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@api_bp.get("/health")
def health():
    return _ok({
        "status": "ok",
        "nlp_model": Config.NLP_MODEL,
        "database": "mongodb",
    })


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@api_bp.get("/api/products")
def list_products():
    query_param = (request.args.get("q", "") or "").strip()
    category = (request.args.get("category", "") or "").strip()
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (TypeError, ValueError):
        limit = 100
    include_dynamic = request.args.get("dynamic_price", "true").lower() == "true"

    with session_scope() as s:
        query: dict = {}
        if category:
            query["category"] = category
        if query_param:
            query["name"] = {"$regex": query_param, "$options": "i"}

        products = list(s["products"].find(query).limit(limit))
        # Attach seller info to each product
        for p in products:
            seller = _lookup_seller(s, p.get("seller_id"))
            if seller:
                p["seller"] = {
                    "id": seller["_id"],
                    "username": seller.get("username", ""),
                    "location": seller.get("location", ""),
                }
        return _ok([_serialize_product(s, p, include_dynamic) for p in products])


@api_bp.get("/api/products/<int:product_id>")
def get_product(product_id):
    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)
        seller = _lookup_seller(s, product.get("seller_id"))
        if seller:
            product["seller"] = {
                "id": seller["_id"],
                "username": seller.get("username", ""),
                "location": seller.get("location", ""),
            }
        return _ok(_serialize_product(s, product))


@api_bp.post("/api/products")
def create_product():
    payload = _payload()
    name = (payload.get("name") or "").strip()
    if not name:
        return _err("name is required")

    try:
        price = float(payload.get("price", 0.0))
        stock_kg = float(payload.get("stock_kg", payload.get("quantity", 0.0)))
    except (TypeError, ValueError):
        return _err("price and stock_kg/quantity must be numeric")

    category_str = (payload.get("category") or "Vegetable").strip()
    try:
        category = ProductCategory(category_str).value
    except ValueError:
        category = ProductCategory.VEGETABLE.value

    with session_scope() as s:
        seller = _resolve_seller(s, seller_id=payload.get("seller_id"), seller_obj=payload.get("seller"))
        if seller is None or seller.get("role") != UserRole.SELLER.value:
            return _err("valid seller_id or seller object required", 404)

        doc = make_product_doc(
            seller_id=seller["_id"],
            name=name,
            category=category,
            description=payload.get("description") or "",
            price=price,
            stock_kg=stock_kg,
            rating=0.0,
        )
        doc["_id"] = _next_id(s.db)
        doc.setdefault("created_at", _now_iso())
        s["products"].insert_one(doc)

        result = product_to_dict(doc)
        seller_info = _lookup_seller(s, seller["_id"])
        if seller_info:
            result["seller"] = {
                "id": seller_info["_id"],
                "username": seller_info.get("username", ""),
                "location": seller_info.get("location", ""),
            }
        return _ok(result, 201)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@api_bp.post("/api/orders")
def create_order():
    payload = _payload()
    buyer_id = payload.get("buyer_id")
    product_id = payload.get("product_id")

    try:
        quantity_kg = float(payload.get("quantity_kg", payload.get("quantity", 0)))
    except (TypeError, ValueError):
        return _err("quantity_kg/quantity must be numeric")

    if quantity_kg <= 0:
        return _err("quantity must be positive")

    buyer_name = payload.get("buyer_name")
    buyer_phone = payload.get("buyer_phone")

    with session_scope() as s:
        buyer = _resolve_buyer(s, buyer_id=buyer_id, buyer_name=buyer_name, buyer_phone=buyer_phone)
        if buyer is None or buyer.get("role") != UserRole.BUYER.value:
            return _err("valid buyer_id or buyer_name required", 404)

        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        if product.get("stock_kg", 0) < quantity_kg:
            return _err(
                f"insufficient stock: requested {quantity_kg}kg, available {product.get('stock_kg')}kg",
                409,
            )

        total_price = round(product.get("price", 0.0) * quantity_kg, 2)

        # Create order
        order_doc = make_order_doc(
            buyer_id=buyer["_id"],
            product_id=product["_id"],
            quantity_kg=quantity_kg,
            total_price=total_price,
            status=OrderStatus.CONFIRMED.value,
        )
        order_doc["_id"] = _next_id(s.db)
        order_doc.setdefault("created_at", _now_iso())
        s["orders"].insert_one(order_doc)

        # Decrement stock
        s["products"].update_one(
            {"_id": product["_id"]},
            {"$inc": {"stock_kg": -quantity_kg}},
        )
        remaining_stock = product.get("stock_kg", 0) - quantity_kg

        # Capture contacts for notifications (outside session)
        buyer_contact = buyer.get("email", "")
        seller = _lookup_seller(s, product.get("seller_id"))
        seller_contact = seller.get("email", buyer_contact) if seller else buyer_contact
        product_name = product.get("name", "")

    # Fire-and-forget notifications
    from services import get_twilio_service
    _twilio = get_twilio_service()
    _twilio.send_order_confirmation(
        to=buyer_contact, product_name=product_name,
        quantity=quantity_kg, total_price=total_price,
    )
    _twilio.notify_seller_sale(
        to=seller_contact, product_name=product_name,
        quantity=quantity_kg, total_price=total_price,
    )
    if remaining_stock < 50:
        _twilio.send_sms(
            to=seller_contact,
            body=f"Low stock alert: {product_name} is down to {remaining_stock:.1f}kg. Consider restocking.",
        )

    created = order_to_dict(order_doc)
    created["buyer_username"] = buyer.get("username", "")
    created["product_name"] = product_name
    return _ok({"order": created, "transaction": created}, 201)


@api_bp.post("/api/transactions")
def create_transaction():
    return create_order()


@api_bp.get("/api/transactions")
def list_transactions():
    return list_orders()


@api_bp.get("/api/orders")
def list_orders():
    try:
        buyer_id = int(request.args.get("buyer_id", 0) or 0)
    except (TypeError, ValueError):
        buyer_id = 0
    try:
        product_id = int(request.args.get("product_id", 0) or 0)
    except (TypeError, ValueError):
        product_id = 0
    status = (request.args.get("status", "") or "").strip()

    with session_scope() as s:
        query: dict = {}
        if buyer_id:
            query["buyer_id"] = buyer_id
        if product_id:
            query["product_id"] = product_id
        if status:
            try:
                status_enum = OrderStatus(status)
                query["status"] = status_enum.value
            except ValueError:
                pass

        orders = list(s["orders"].find(query).sort("created_at", -1).limit(200))

        # Attach buyer + product names
        for o in orders:
            buyer = _lookup_user(s, o.get("buyer_id"))
            o["buyer_username"] = buyer.get("username", "") if buyer else ""
            prod = _lookup_product(s, o.get("product_id"))
            o["product_name"] = prod.get("name", "") if prod else ""

        return _ok([order_to_dict(o) for o in orders])


# ---------------------------------------------------------------------------
# Live Streams
# ---------------------------------------------------------------------------

@api_bp.get("/api/streams")
def list_streams():
    active_only = request.args.get("active", "false").lower() == "true"
    try:
        seller_id = int(request.args.get("seller_id", 0) or 0)
    except (TypeError, ValueError):
        seller_id = 0

    with session_scope() as s:
        query: dict = {}
        if seller_id:
            query["seller_id"] = seller_id
        if active_only:
            query["is_active"] = True

        streams = list(s["live_streams"].find(query).sort("started_at", -1).limit(50))
        return _ok([stream_to_dict(s) for s in streams])


@api_bp.get("/api/streams/<int:stream_id>")
def get_stream(stream_id):
    """Fetch a single live stream (used by the seller studio panel)."""
    with session_scope() as s:
        stream = s["live_streams"].find_one({"_id": stream_id})
        if stream is None:
            return _err("stream not found", 404)
        result = stream_to_dict(stream)
        seller = _lookup_seller(s, stream.get("seller_id"))
        if seller:
            result["seller"] = {
                "id": seller["_id"],
                "username": seller.get("username", ""),
                "location": seller.get("location", ""),
            }
        return _ok(result)


@api_bp.post("/api/streams")
def create_stream():
    payload = _payload()
    seller_id = payload.get("seller_id")
    title = (payload.get("stream_title") or "").strip()

    if not seller_id or not title:
        return _err("seller_id and stream_title are required")

    with session_scope() as s:
        seller = _lookup_seller(s, seller_id)
        if seller is None or seller.get("role") != UserRole.SELLER.value:
            return _err("valid seller_id required", 404)

        doc = make_live_stream_doc(
            seller_id=seller_id,
            stream_title=title[:200],
            is_active=True,
        )
        doc["_id"] = _next_id(s.db)
        doc.setdefault("started_at", _now_iso())
        s["live_streams"].insert_one(doc)
        created = stream_to_dict(doc)
        result = created
        seller_info = _lookup_seller(s, seller_id)
        if seller_info:
            result["seller"] = {
                "id": seller_info["_id"],
                "username": seller_info.get("username", ""),
                "location": seller_info.get("location", ""),
            }
        return _ok(result, 201)


@api_bp.post("/api/streams/<int:stream_id>/end")
def end_stream(stream_id):
    with session_scope() as s:
        stream = s["live_streams"].find_one({"_id": stream_id})
        if stream is None:
            return _err("stream not found", 404)
        s["live_streams"].update_one({"_id": stream_id}, {"$set": {"is_active": False}})
        stream["is_active"] = False
        return _ok(stream_to_dict(stream))


# ---------------------------------------------------------------------------
# Chat Messages
# ---------------------------------------------------------------------------

@api_bp.get("/api/streams/<int:stream_id>/messages")
def get_chat_messages(stream_id):
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (TypeError, ValueError):
        limit = 100

    with session_scope() as s:
        messages = list(
            s["chat_messages"]
            .find({"stream_id": stream_id})
            .sort("timestamp", -1)
            .limit(limit)
        )
        # Attach usernames
        result = []
        for m in messages:
            user = _lookup_user(s, m.get("user_id"))
            result.append(chat_message_to_dict(m, username=user.get("username") if user else None))
        return _ok(list(reversed(result)))


@api_bp.post("/api/streams/send_message")
def send_stream_message():
    """REST alternative to the SocketIO ``send_message`` event."""
    payload = _payload()
    stream_id = payload.get("stream_id")
    user_id = payload.get("user_id")
    message_text = (payload.get("message_text") or "").strip()

    if not stream_id or not user_id or not message_text:
        return _err("stream_id, user_id, and message_text required")

    message_data = build_stream_message_data(stream_id, user_id, message_text)
    if message_data is None:
        return _err("invalid stream or user", 404)

    from api.app import socketio as _socketio  # lazy: avoid circular import at module load
    _socketio.emit("new_message", message_data, to=f"{STREAM_ROOM_PREFIX}{stream_id}")
    return _ok(message_data, 201)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

@api_bp.get("/api/products/<int:product_id>/reviews")
def list_reviews(product_id):
    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)
        reviews = list(s["reviews"].find({"product_id": product_id}).sort("created_at", -1).limit(100))
        result = []
        for r in reviews:
            user = _lookup_user(s, r.get("user_id"))
            result.append(review_to_dict(r, product_name=product.get("name", ""), username=user.get("username") if user else None))
        return _ok(result)


@api_bp.post("/api/products/<int:product_id>/reviews")
def create_review(product_id):
    payload = _payload()
    user_id = payload.get("user_id")
    buyer_name = payload.get("buyer_name")
    review_text = (payload.get("review_text", payload.get("comment", "")) or "").strip()

    try:
        rating = int(payload.get("rating", 0))
    except (TypeError, ValueError):
        return _err("rating must be an integer 1..5")

    if rating < 1 or rating > 5:
        return _err("rating must be between 1 and 5")

    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        user = _resolve_buyer(s, buyer_id=user_id, buyer_name=buyer_name)
        if user is None:
            return _err("user_id or buyer_name required", 404)

        sentiment = _nlp_engine.analyze_sentiment(review_text)

        review_doc = make_review_doc(
            product_id=product["_id"],
            user_id=user["_id"],
            review_text=review_text,
            rating=rating,
            sentiment_score=sentiment["sentiment_score"],
        )
        review_doc["_id"] = _next_id(s.db)
        review_doc.setdefault("created_at", _now_iso())
        s["reviews"].insert_one(review_doc)

        # Update product rating
        all_reviews = list(s["reviews"].find({"product_id": product["_id"]}))
        if all_reviews:
            new_rating = sum(r.get("rating", 0) for r in all_reviews) / len(all_reviews)
            s["products"].update_one(
                {"_id": product["_id"]},
                {"$set": {"rating": round(new_rating, 2)}},
            )

        result = review_to_dict(
            review_doc,
            product_name=product.get("name", ""),
            username=user.get("username", ""),
        )
        return _ok(result, 201)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

@api_bp.get("/api/recommendations/<int:user_id>")
def get_recommendations(user_id):
    top_n = min(int(request.args.get("top_n", 5)), 20)

    with session_scope() as s:
        user = _lookup_user(s, user_id)
        if user is None:
            return _err("user not found", 404)

        orders = list(s["orders"].find({"buyer_id": user_id}))
        purchase_history = [{"product_id": o["product_id"]} for o in orders]

        products = list(s["products"].find().limit(500))
        product_data = [product_to_dict(p) for p in products]

        # Attach seller info
        for p in product_data:
            seller = _lookup_seller(s, p.get("seller_id"))
            if seller:
                p["seller"] = {
                    "id": seller["_id"],
                    "username": seller.get("username", ""),
                    "location": seller.get("location", ""),
                }

        # Compute product sentiments
        product_sentiments = {}
        for p in products:
            pid = p["_id"]
            reviews = list(s["reviews"].find({"product_id": pid}))
            if reviews:
                product_sentiments[pid] = (
                    sum(r.get("sentiment_score", 0.0) for r in reviews) / len(reviews)
                )
            else:
                product_sentiments[pid] = 0.0

        _recommendation_engine.fit(product_data)
        recs = _recommendation_engine.get_recommendations(
            user_id=user_id,
            top_n=top_n,
            user_purchase_history=purchase_history,
            product_sentiments=product_sentiments,
        )

        product_map = {p["id"]: p for p in product_data}
        results = []
        for rec in recs:
            prod = product_map.get(rec["product_id"])
            if not prod:
                continue
            calc = _pricing_engine.calculate_optimal_price(
                product_id=rec["product_id"],
                base_price=prod.get("price", 0.0),
                demand_factor=0.5,
                stock_kg=prod.get("stock_kg", 0.0),
                sentiment_score=product_sentiments.get(rec["product_id"], 0.0),
            )
            enriched = dict(prod)
            enriched["dynamic_price"] = calc.optimal_price
            enriched["price_change_pct"] = calc.change_pct
            enriched["recommendation_score"] = rec["score"]
            enriched["recommendation_reason"] = rec["reason"]
            results.append(enriched)

        return _ok(results)


@api_bp.get("/api/recommendations")
def get_recommendations_legacy():
    """Legacy endpoint for content-based recommendations via query text."""
    query_param = (request.args.get("q", "") or "").strip()
    try:
        top_k = min(int(request.args.get("top_k", 5)), 25)
    except (TypeError, ValueError):
        top_k = 5
    if not query_param:
        return _ok([])

    with session_scope() as s:
        products = list(s["products"].find().limit(500))
        engine = build_engine_from_products(products)
        scored = engine.recommend(query_param, top_k=top_k)
        by_id = {p["_id"]: p for p in products}
        results = []
        for product_id, score in scored:
            product = by_id.get(product_id)
            if product is None:
                continue
            data = product_to_dict(product)
            data["score"] = score
            results.append(data)
        return _ok(results)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@api_bp.get("/api/analytics/<int:seller_id>")
def get_seller_analytics(seller_id):
    with session_scope() as s:
        seller = _lookup_seller(s, seller_id)
        if seller is None or seller.get("role") != UserRole.SELLER.value:
            return _err("seller not found", 404)

        products = list(s["products"].find({"seller_id": seller_id}))
        product_ids = [p["_id"] for p in products]

        # Sales stats
        total_orders = s["orders"].count_documents({"product_id": {"$in": product_ids}})
        pipeline = [
            {"$match": {"product_id": {"$in": product_ids}}},
            {"$group": {"_id": None, "total": {"$sum": "$total_price"}}},
        ]
        revenue_doc = list(s["orders"].aggregate(pipeline))
        total_revenue = revenue_doc[0]["total"] if revenue_doc else 0.0

        pipeline2 = [
            {"$match": {"product_id": {"$in": product_ids}}},
            {"$group": {"_id": None, "total": {"$sum": "$quantity_kg"}}},
        ]
        qty_doc = list(s["orders"].aggregate(pipeline2))
        total_quantity = qty_doc[0]["total"] if qty_doc else 0.0

        # Sentiment summary
        reviews = list(s["reviews"].find({"product_id": {"$in": product_ids}}))
        sentiment_counts = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
        avg_sentiment = 0.0
        if reviews:
            for r in reviews:
                label = (
                    "POSITIVE" if r.get("sentiment_score", 0) > 0.15
                    else ("NEGATIVE" if r.get("sentiment_score", 0) < -0.15 else "NEUTRAL")
                )
                sentiment_counts[label] += 1
            avg_sentiment = sum(r.get("sentiment_score", 0) for r in reviews) / len(reviews)

        # Demand forecasts
        forecasts = []
        for product in products[:10]:
            orders_list = list(s["orders"].find({"product_id": product["_id"]}))
            historical_data = [
                {"date": o.get("created_at", "")[:10], "quantity": o.get("quantity_kg", 0)}
                for o in orders_list
            ]
            forecast = _forecaster.predict_demand(product["_id"], historical_data, forecast_days=7)
            forecasts.append({
                "product_id": product["_id"],
                "product_name": product.get("name", ""),
                "predictions": forecast.predictions,
                "trend_slope": forecast.trend_slope,
            })

        # Low stock alerts
        low_stock = [
            {"product_id": p["_id"], "product_name": p.get("name", ""), "stock_kg": p.get("stock_kg", 0)}
            for p in products if p.get("stock_kg", 0) < 50
        ]

        return _ok({
            "seller": user_to_dict(seller),
            "sales_summary": {
                "total_orders": total_orders,
                "total_revenue": round(total_revenue, 2),
                "total_quantity_kg": round(total_quantity, 2),
                "avg_order_value": round(total_revenue / total_orders, 2) if total_orders else 0.0,
            },
            "sentiment_summary": {
                "counts": sentiment_counts,
                "average_score": round(avg_sentiment, 4),
                "total_reviews": len(reviews),
            },
            "demand_forecasts": forecasts,
            "low_stock_alerts": low_stock,
        })


# ---------------------------------------------------------------------------
# Dynamic Pricing
# ---------------------------------------------------------------------------

@api_bp.get("/api/pricing/<int:product_id>")
def get_pricing_suggestion(product_id):
    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        reviews = list(s["reviews"].find({"product_id": product_id}))
        avg_sentiment = (
            sum(r.get("sentiment_score", 0.0) for r in reviews) / len(reviews)
            if reviews else 0.0
        )

        orders_count = s["orders"].count_documents({"product_id": product_id})
        demand_factor = min(0.5 + (orders_count * 0.02), 1.0)

        calc = _pricing_engine.calculate_optimal_price(
            product_id=product["_id"],
            base_price=product.get("price", 0.0),
            demand_factor=demand_factor,
            stock_kg=product.get("stock_kg", 0.0),
            sentiment_score=avg_sentiment,
        )

        return _ok({
            "product_id": product["_id"],
            "product_name": product.get("name", ""),
            "base_price": calc.base_price,
            "optimal_price": calc.optimal_price,
            "suggested_price": calc.optimal_price,
            "change_pct": calc.change_pct,
            "demand_factor": calc.demand_factor,
            "stock_factor": calc.stock_factor,
            "sentiment_factor": calc.sentiment_factor,
            "capped": calc.capped,
        })


@api_bp.post("/api/products/<int:product_id>/price/apply")
def apply_price(product_id):
    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        transactions = list(s["orders"].find({"product_id": product_id}))
        demand = demand_score(transactions)
        suggestion = _pricing_engine.calculate_optimal_price(
            product_id=product["_id"],
            base_price=product.get("price", 0.0),
            demand_factor=demand,
            stock_kg=product.get("stock_kg", 0.0),
        )

        log_doc = make_price_log_doc(
            product_id=product["_id"],
            old_price=product.get("price", 0.0),
            new_price=suggestion.optimal_price,
            reason=suggestion.reason,
        )
        log_doc["_id"] = _next_id(s.db)
        log_doc.setdefault("created_at", _now_iso())
        s["price_logs"].insert_one(log_doc)

        s["products"].update_one(
            {"_id": product_id},
            {"$set": {"price": suggestion.optimal_price}},
        )
        new_price = suggestion.optimal_price

    return _ok({
        "product_id": product["_id"],
        "new_price": new_price,
        "base_price": suggestion.base_price,
        "suggested_price": suggestion.optimal_price,
        "change_pct": suggestion.change_pct,
        "reason": suggestion.reason,
        "demand_factor": suggestion.demand_factor,
        "stock_factor": suggestion.stock_factor,
    })


# ---------------------------------------------------------------------------
# Product Analytics
# ---------------------------------------------------------------------------

@api_bp.get("/api/products/<int:product_id>/analytics")
def product_analytics(product_id):
    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        transactions = list(s["orders"].find({"product_id": product_id}))
        reviews = list(s["reviews"].find({"product_id": product_id}))
        price_logs = list(s["price_logs"].find({"product_id": product_id}).sort("created_at", 1))

        # Peer prices
        peer_prices = [
            p.get("price", 0)
            for p in s["products"].find({
                "category": product.get("category"),
                "_id": {"$ne": product_id},
                "price": {"$gt": 0},
            })
            if p.get("price", 0) > 0
        ]
        competitor_min = min(peer_prices) if peer_prices else None
        competitor_max = max(peer_prices) if peer_prices else None

        demand = demand_score(transactions)
        avg_sentiment = (
            sum(r.get("sentiment_score", 0.0) for r in reviews) / len(reviews)
            if reviews else 0.0
        )

        suggestion = _pricing_engine.calculate_optimal_price(
            product_id=product["_id"],
            base_price=product.get("price", 0.0),
            demand_factor=demand,
            stock_kg=product.get("stock_kg", 0.0),
            sentiment_score=avg_sentiment,
        )

        return _ok({
            "product": product_to_dict(product),
            "summary": sales_summary(transactions),
            "ratings": rating_summary(reviews),
            "demand_score": demand,
            "daily_revenue": revenue_by_day(transactions),
            "price_histories": [price_log_to_dict(log) for log in price_logs],
            "suggested_price": {
                "suggested_price": suggestion.optimal_price,
                "base_price": suggestion.base_price,
                "change_pct": suggestion.change_pct,
                "reason": suggestion.reason,
                "demand_factor": suggestion.demand_factor,
                "stock_factor": suggestion.stock_factor,
            },
        })


# ---------------------------------------------------------------------------
# Product Forecast
# ---------------------------------------------------------------------------

@api_bp.get("/api/products/<int:product_id>/forecast")
def product_forecast(product_id):
    try:
        horizon = int(request.args.get("horizon", 7))
    except (TypeError, ValueError):
        horizon = 7

    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        orders = list(s["orders"].find({"product_id": product_id}))
        historical_data = [
            {"date": o.get("created_at", "")[:10], "quantity": o.get("quantity_kg", 0)}
            for o in orders
        ]
        forecast = _forecaster.predict_demand(
            product["_id"], historical_data, forecast_days=horizon,
        )

        return _ok({
            "product_id": product["_id"],
            "history": [{"date": d["date"], "quantity": d["predicted_quantity"]} for d in forecast.predictions] if historical_data else [],
            "history_dates": [d["date"] for d in forecast.predictions] if historical_data else [],
            "dates": [d["date"] for d in forecast.predictions],
            "values": [d["predicted_quantity"] for d in forecast.predictions],
            "trend_slope": forecast.trend_slope,
        })


# ---------------------------------------------------------------------------
# Demand Forecast
# ---------------------------------------------------------------------------

@api_bp.get("/api/forecast/<int:product_id>")
def get_demand_forecast(product_id):
    try:
        horizon = min(int(request.args.get("horizon", 7)), 30)
    except (TypeError, ValueError):
        horizon = 7

    with session_scope() as s:
        product = s["products"].find_one({"_id": product_id})
        if product is None:
            return _err("product not found", 404)

        orders = list(s["orders"].find({"product_id": product_id}))
        historical_data = [
            {"date": o.get("created_at", "")[:10], "quantity": o.get("quantity_kg", 0)}
            for o in orders
        ]
        forecast = _forecaster.predict_demand(product["_id"], historical_data, forecast_days=horizon)

        return _ok({
            "product_id": product["_id"],
            "product_name": product.get("name", ""),
            "forecast": forecast.predictions,
            "trend_slope": forecast.trend_slope,
            "model_used": forecast.model_used,
        })


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@api_bp.get("/api/users")
def list_users():
    """List platform users (id, username, role, location)."""
    with session_scope() as s:
        users = list(s["users"].find().sort("_id", 1).limit(500))
        return _ok([user_to_dict(u) for u in users])


# ---------------------------------------------------------------------------
# Seller + Inventory endpoints (added for frontend API unification)
# ---------------------------------------------------------------------------

@api_bp.get("/api/seller/<int:seller_id>")
def get_seller(seller_id):
    with session_scope() as s:
        seller = _lookup_seller(s, seller_id)
        if seller is None:
            return _err("seller not found", 404)
        return _ok(user_to_dict(seller))


@api_bp.get("/api/seller/<int:seller_id>/orders")
def get_seller_orders(seller_id):
    """Orders for a seller's products — powers the seller-studio orders tab."""
    with session_scope() as s:
        seller = _lookup_seller(s, seller_id)
        if seller is None or seller.get("role") != UserRole.SELLER.value:
            return _err("seller not found or not a seller", 404)

        product_ids = [
            p["_id"]
            for p in s["products"].find({"seller_id": seller_id}, {"_id": 1})
        ]
        orders = list(
            s["orders"]
            .find({"product_id": {"$in": product_ids}})
            .sort("created_at", -1)
            .limit(200)
        )

        result = []
        for o in orders:
            buyer = _lookup_user(s, o.get("buyer_id"))
            prod = _lookup_product(s, o.get("product_id"))
            entry = order_to_dict(
                o,
                buyer_username=buyer.get("username", "") if buyer else "",
                product_name=prod.get("name", "") if prod else "",
            )
            entry["buyer"] = (
                {
                    "id": buyer["_id"],
                    "username": buyer.get("username", ""),
                    "email": buyer.get("email", ""),
                    "location": buyer.get("location", ""),
                }
                if buyer
                else {}
            )
            entry["ordered_at"] = o.get("created_at", "")
            result.append(entry)

        return _ok(result)


@api_bp.get("/api/seller/<int:seller_id>/inventory")
def get_seller_inventory(seller_id):
    with session_scope() as s:
        seller = _lookup_seller(s, seller_id)
        if seller is None or seller.get("role") != UserRole.SELLER.value:
            return _err("seller not found or not a seller", 404)

        products = list(s["products"].find({"seller_id": seller_id}))
        results = []
        for p in products:
            reviews = list(s["reviews"].find({"product_id": p["_id"]}))
            avg_sentiment = (
                sum(r.get("sentiment_score", 0.0) for r in reviews) / len(reviews)
                if reviews else 0.0
            )
            orders = list(s["orders"].find({"product_id": p["_id"]}))
            demand = demand_score(orders)

            calc = _pricing_engine.calculate_optimal_price(
                product_id=p["_id"],
                base_price=p.get("price", 0.0),
                demand_factor=demand,
                stock_kg=p.get("stock_kg", 0.0),
                sentiment_score=avg_sentiment,
            )

            stock = p.get("stock_kg", 0)
            if stock <= 0:
                status = "🔴 Out of Stock"
            elif stock < 20:
                status = "🔴 Critical"
            elif stock < 50:
                status = "🟠 Low"
            elif stock > 500:
                status = "🔵 Overstock"
            else:
                status = "🟢 Good"

            results.append({
                "product": product_to_dict(p),
                "dynamic_price": calc.optimal_price,
                "price_change_pct": calc.change_pct,
                "status": status,
            })

        return _ok(results)


# ---------------------------------------------------------------------------
# Import Config for health endpoint (must be at end to avoid circular import)
# ---------------------------------------------------------------------------

from config import Config
