"""REST endpoints for the marketplace, analytics and optimisation pipelines."""

from flask import Blueprint, request
from sqlalchemy import func

from database.db import session_scope
from database.models import (
    ChatMessage,
    IntentTag,
    LiveStream,
    Order,
    OrderStatus,
    PriceLog,
    Product,
    ProductCategory,
    Review,
    SentimentLabel,
    User,
    UserRole,
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
_recommendation_engine = RecommendationEngine()
_forecaster = DemandForecaster()


def _ok(data, status=200):
    return {"ok": True, "data": data}, status


def _err(message, status=400):
    return {"ok": False, "error": message}, status


def _payload():
    return request.get_json(silent=True) or {}


def _serialize_product(product, include_dynamic_price=True):
    """Serialize product with optional dynamic pricing."""
    data = product.to_dict()
    if include_dynamic_price:
        # Calculate dynamic price from live order velocity + review sentiment
        with session_scope() as session:
            reviews = session.query(Review).filter(Review.product_id == product.id).all()
            avg_sentiment = 0.0
            if reviews:
                avg_sentiment = sum(r.sentiment_score for r in reviews) / len(reviews)
            orders = session.query(Order).filter(Order.product_id == product.id).all()
            demand = demand_score(orders)

        calc = _pricing_engine.calculate_optimal_price(
            product_id=product.id,
            base_price=product.price,
            demand_factor=demand,
            stock_kg=product.stock_kg,
            sentiment_score=avg_sentiment
        )
        data["dynamic_price"] = calc.optimal_price
        data["price_change_pct"] = calc.change_pct
    return data


# --- Health ---------------------------------------------------------------
@api_bp.get("/health")
def health():
    return _ok({
        "status": "ok",
        "nlp_model": Config.NLP_MODEL,
        "database": "configured",
    })


# --- Products --------------------------------------------------------------
@api_bp.get("/api/products")
def list_products():
    query_param = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    limit = min(int(request.args.get("limit", 100)), 500)
    include_dynamic = request.args.get("dynamic_price", "true").lower() == "true"

    with session_scope() as session:
        product_query = session.query(Product)
        if category:
            try:
                cat_enum = ProductCategory(category)
                product_query = product_query.filter(Product.category == cat_enum)
            except ValueError:
                pass
        if query_param:
            product_query = product_query.filter(
                Product.name.ilike(f"%{query_param}%")
            )
        products = product_query.limit(limit).all()
        return _ok([_serialize_product(p, include_dynamic) for p in products])


@api_bp.get("/api/products/<int:product_id>")
def get_product(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)
        return _ok(_serialize_product(product))


@api_bp.post("/api/products")
def create_product():
    payload = _payload()
    name = (payload.get("name") or "").strip()
    if not name:
        return _err("name is required")

    # Support both old (quantity) and new (stock_kg) field names
    try:
        price = float(payload.get("price", 0.0))
        stock_kg = float(payload.get("stock_kg", payload.get("quantity", 0.0)))
    except (TypeError, ValueError):
        return _err("price and stock_kg/quantity must be numeric")

    # Support both seller_id and seller object
    seller_id = payload.get("seller_id")
    seller_obj = payload.get("seller")

    with session_scope() as session:
        seller = None
        if seller_id:
            seller = session.get(User, seller_id)
        elif seller_obj and seller_obj.get("email"):
            seller = session.query(User).filter(User.email == seller_obj["email"]).first()
            if not seller:
                seller = User(
                    username=(seller_obj.get("name") or "Seller").strip()[:80],
                    email=seller_obj["email"],
                    role=UserRole.SELLER,
                    location=(seller_obj.get("location") or "").strip()[:120],
                )
                session.add(seller)
                session.flush()
        elif seller_obj and seller_obj.get("name"):
            seller = (
                session.query(User)
                .filter(User.username == seller_obj["name"].strip()[:80])
                .first()
            )
            if seller is None:
                seller = User(
                    username=seller_obj["name"].strip()[:80],
                    email=seller_obj.get("email") or f"{seller_obj['name'].lower().replace(' ', '_')}@example.com",
                    role=UserRole.SELLER,
                    location=(seller_obj.get("location") or "").strip()[:120],
                )
                session.add(seller)
                session.flush()

        if seller is None or seller.role != UserRole.SELLER:
            return _err("valid seller_id or seller object required", 404)

        category_str = (payload.get("category") or "Vegetable").strip()
        try:
            category = ProductCategory(category_str)
        except ValueError:
            category = ProductCategory.VEGETABLE

        product = Product(
            seller_id=seller.id,
            name=name[:160],
            category=category,
            description=(payload.get("description") or ""),
            price=price,
            stock_kg=stock_kg,
            rating=0.0,
        )
        session.add(product)
        session.flush()
        created = _serialize_product(product)

    return _ok(created, 201)


# --- Orders ----------------------------------------------------------------
@api_bp.post("/api/orders")
def create_order():
    payload = _payload()
    buyer_id = payload.get("buyer_id")
    product_id = payload.get("product_id")

    # Support both quantity_kg (new) and quantity (old)
    try:
        quantity_kg = float(payload.get("quantity_kg", payload.get("quantity", 0)))
    except (TypeError, ValueError):
        return _err("quantity_kg/quantity must be numeric")

    if quantity_kg <= 0:
        return _err("quantity must be positive")

    # Support both buyer_id (new) and buyer_name/buyer_phone (old)
    buyer_name = payload.get("buyer_name")
    buyer_phone = payload.get("buyer_phone")

    with session_scope() as session:
        buyer = None
        if buyer_id:
            buyer = session.get(User, buyer_id)
        elif buyer_name:
            buyer = session.query(User).filter(User.username == buyer_name).first()
            if not buyer:
                buyer = User(
                    username=buyer_name.strip()[:80],
                    email=buyer_phone or f"{buyer_name.lower().replace(' ', '_')}@example.com",
                    role=UserRole.BUYER,
                    location="",
                )
                session.add(buyer)
                session.flush()

        if buyer is None or buyer.role != UserRole.BUYER:
            return _err("valid buyer_id or buyer_name required", 404)

        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        if product.stock_kg < quantity_kg:
            return _err(
                f"insufficient stock: requested {quantity_kg}kg, available {product.stock_kg}kg",
                409,
            )

        total_price = round(product.price * quantity_kg, 2)
        order = Order(
            buyer_id=buyer.id,
            product_id=product.id,
            quantity_kg=quantity_kg,
            total_price=total_price,
            status=OrderStatus.CONFIRMED,
        )
        product.stock_kg = round(product.stock_kg - quantity_kg, 2)
        session.add(order)
        session.flush()
        created = order.to_dict()

        # Capture notification recipients while the session is still open.
        buyer_contact = buyer.email
        seller_contact = product.seller.email if product.seller else buyer_contact
        remaining_stock = product.stock_kg
        product_name = product.name

    # Integration layer: fire-and-forget notifications. Graceful whenever
    # Twilio is not configured (returns an "unsent" payload, never raises).
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
            body=f"Low stock alert: {product_name} is down to {remaining_stock}kg. Consider restocking.",
        )

    return _ok({"order": created, "transaction": created}, 201)


# Backward compatibility: /api/transactions endpoint
@api_bp.post("/api/transactions")
def create_transaction():
    """Legacy endpoint for creating orders (maps to /api/orders)."""
    return create_order()


@api_bp.get("/api/transactions")
def list_transactions():
    """Legacy endpoint for listing transactions (maps to /api/orders)."""
    return list_orders()


@api_bp.get("/api/orders")
def list_orders():
    buyer_id = request.args.get("buyer_id", type=int)
    product_id = request.args.get("product_id", type=int)
    status = request.args.get("status", "").strip()

    with session_scope() as session:
        query = session.query(Order)
        if buyer_id:
            query = query.filter(Order.buyer_id == buyer_id)
        if product_id:
            query = query.filter(Order.product_id == product_id)
        if status:
            try:
                status_enum = OrderStatus(status)
                query = query.filter(Order.status == status_enum)
            except ValueError:
                pass
        orders = query.order_by(Order.created_at.desc()).limit(200).all()
        return _ok([o.to_dict() for o in orders])


# --- Live Streams ----------------------------------------------------------
@api_bp.get("/api/streams")
def list_streams():
    active_only = request.args.get("active", "false").lower() == "true"
    seller_id = request.args.get("seller_id", type=int)

    with session_scope() as session:
        query = session.query(LiveStream)
        if active_only:
            query = query.filter(LiveStream.is_active == "true")
        if seller_id:
            query = query.filter(LiveStream.seller_id == seller_id)
        streams = query.order_by(LiveStream.started_at.desc()).limit(50).all()
        return _ok([s.to_dict() for s in streams])


@api_bp.post("/api/streams")
def create_stream():
    payload = _payload()
    seller_id = payload.get("seller_id")
    title = (payload.get("stream_title") or "").strip()

    if not seller_id or not title:
        return _err("seller_id and stream_title are required")

    with session_scope() as session:
        seller = session.get(User, seller_id)
        if seller is None or seller.role != UserRole.SELLER:
            return _err("valid seller_id required", 404)

        stream = LiveStream(
            seller_id=seller.id,
            stream_title=title[:200],
            is_active="true",
        )
        session.add(stream)
        session.flush()
        created = stream.to_dict()

    return _ok(created, 201)


@api_bp.post("/api/streams/<int:stream_id>/end")
def end_stream(stream_id):
    with session_scope() as session:
        stream = session.get(LiveStream, stream_id)
        if stream is None:
            return _err("stream not found", 404)
        stream.is_active = "false"
        return _ok(stream.to_dict())


# --- Chat Messages ---------------------------------------------------------
@api_bp.get("/api/streams/<int:stream_id>/messages")
def get_chat_messages(stream_id):
    limit = min(int(request.args.get("limit", 100)), 500)

    with session_scope() as session:
        messages = (
            session.query(ChatMessage)
            .filter(ChatMessage.stream_id == stream_id)
            .order_by(ChatMessage.timestamp.desc())
            .limit(limit)
            .all()
        )
        return _ok([m.to_dict() for m in reversed(messages)])


@api_bp.post("/api/streams/send_message")
def send_stream_message():
    """REST alternative to the SocketIO ``send_message`` event.

    Runs the same NLP pipeline, persists the message, returns it with
    sentiment/intent badges, and broadcasts it to the live stream room so
    SocketIO subscribers see it in real time.
    """
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


# --- Reviews ---------------------------------------------------------------
@api_bp.get("/api/products/<int:product_id>/reviews")
def list_reviews(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)
        reviews = (
            session.query(Review)
            .filter(Review.product_id == product_id)
            .order_by(Review.created_at.desc())
            .limit(100)
            .all()
        )
        return _ok([r.to_dict() for r in reviews])


@api_bp.post("/api/products/<int:product_id>/reviews")
def create_review(product_id):
    payload = _payload()
    # Support both user_id (new) and buyer_name (old)
    user_id = payload.get("user_id")
    buyer_name = payload.get("buyer_name")
    review_text = (payload.get("review_text", payload.get("comment", "")) or "").strip()

    try:
        rating = int(payload.get("rating", 0))
    except (TypeError, ValueError):
        return _err("rating must be an integer 1..5")

    if rating < 1 or rating > 5:
        return _err("rating must be between 1 and 5")

    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        user = None
        if user_id:
            user = session.get(User, user_id)
        elif buyer_name:
            user = session.query(User).filter(User.username == buyer_name).first()
            if not user:
                user = User(
                    username=buyer_name.strip()[:80],
                    email=f"{buyer_name.lower().replace(' ', '_')}@example.com",
                    role=UserRole.BUYER,
                    location="",
                )
                session.add(user)
                session.flush()

        if user is None:
            return _err("user_id or buyer_name required", 404)

        sentiment = _nlp_engine.analyze_sentiment(review_text)

        review = Review(
            product_id=product.id,
            user_id=user.id,
            review_text=review_text,
            rating=rating,
            sentiment_score=sentiment["sentiment_score"],
        )
        session.add(review)
        session.flush()

        # Update product rating
        all_reviews = session.query(Review).filter(Review.product_id == product.id).all()
        if all_reviews:
            product.rating = sum(r.rating for r in all_reviews) / len(all_reviews)

        created = review.to_dict()

    return _ok(created, 201)


# --- Recommendations -------------------------------------------------------
@api_bp.get("/api/recommendations/<int:user_id>")
def get_recommendations(user_id):
    top_n = min(int(request.args.get("top_n", 5)), 20)

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return _err("user not found", 404)

        # Get user's purchase history
        orders = session.query(Order).filter(Order.buyer_id == user_id).all()
        purchase_history = [{"product_id": o.product_id} for o in orders]

        # Get all products with sentiments
        products = session.query(Product).limit(500).all()
        product_data = [p.to_dict() for p in products]

        # Compute average sentiment per product
        product_sentiments = {}
        for p in products:
            reviews = session.query(Review).filter(Review.product_id == p.id).all()
            if reviews:
                product_sentiments[p.id] = sum(r.sentiment_score for r in reviews) / len(reviews)
            else:
                product_sentiments[p.id] = 0.0

        # Fit and get recommendations
        _recommendation_engine.fit(product_data)
        recs = _recommendation_engine.get_recommendations(
            user_id=user_id,
            top_n=top_n,
            user_purchase_history=purchase_history,
            product_sentiments=product_sentiments
        )

        # Enrich with product details + dynamic pricing (reuses the sentiment
        # map already computed above instead of re-querying per product).
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
            enriched = dict(prod)  # shallow copy; never mutate the shared map
            enriched["dynamic_price"] = calc.optimal_price
            enriched["price_change_pct"] = calc.change_pct
            enriched["recommendation_score"] = rec["score"]
            enriched["recommendation_reason"] = rec["reason"]
            results.append(enriched)

        return _ok(results)


# Backward compatibility: /api/recommendations?q=...
@api_bp.get("/api/recommendations")
def get_recommendations_legacy():
    """Legacy endpoint for content-based recommendations via query text."""
    query_param = request.args.get("q", "").strip()
    top_k = min(int(request.args.get("top_k", 5)), 25)
    if not query_param:
        return _ok([])

    with session_scope() as session:
        products = session.query(Product).limit(500).all()
        engine = build_engine_from_products(products)
        scored = engine.recommend(query_param, top_k=top_k)
        by_id = {p.id: p for p in products}
        results = []
        for product_id, score in scored:
            product = by_id.get(product_id)
            if product is None:
                continue
            data = product.to_dict()
            data["score"] = score
            results.append(data)
        return _ok(results)


# --- Analytics -------------------------------------------------------------
@api_bp.get("/api/analytics/<int:seller_id>")
def get_seller_analytics(seller_id):
    with session_scope() as session:
        seller = session.get(User, seller_id)
        if seller is None or seller.role != UserRole.SELLER:
            return _err("seller not found", 404)

        products = session.query(Product).filter(Product.seller_id == seller_id).all()
        product_ids = [p.id for p in products]

        # Sales stats
        total_orders = session.query(Order).filter(Order.product_id.in_(product_ids)).count()
        total_revenue = session.query(func.sum(Order.total_price)).filter(Order.product_id.in_(product_ids)).scalar() or 0.0
        total_quantity = session.query(func.sum(Order.quantity_kg)).filter(Order.product_id.in_(product_ids)).scalar() or 0.0

        # Sentiment summary
        reviews = session.query(Review).filter(Review.product_id.in_(product_ids)).all()
        sentiment_counts = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
        avg_sentiment = 0.0
        if reviews:
            for r in reviews:
                label = "POSITIVE" if r.sentiment_score > 0.15 else ("NEGATIVE" if r.sentiment_score < -0.15 else "NEUTRAL")
                sentiment_counts[label] += 1
            avg_sentiment = sum(r.sentiment_score for r in reviews) / len(reviews)

        # Demand forecasts
        forecasts = []
        for product in products[:10]:  # Limit to 10 products
            orders_list = session.query(Order).filter(Order.product_id == product.id).all()
            historical_data = [
                {"date": o.created_at.date().isoformat(), "quantity": o.quantity_kg}
                for o in orders_list
            ]
            forecast = _forecaster.predict_demand(product.id, historical_data, forecast_days=7)
            forecasts.append({
                "product_id": product.id,
                "product_name": product.name,
                "predictions": forecast.predictions,
                "trend_slope": forecast.trend_slope
            })

        # Low stock alerts
        low_stock = [
            {"product_id": p.id, "product_name": p.name, "stock_kg": p.stock_kg}
            for p in products if p.stock_kg < 50
        ]

        return _ok({
            "seller": seller.to_dict(),
            "sales_summary": {
                "total_orders": total_orders,
                "total_revenue": round(total_revenue, 2),
                "total_quantity_kg": round(total_quantity, 2),
                "avg_order_value": round(total_revenue / total_orders, 2) if total_orders else 0.0
            },
            "sentiment_summary": {
                "counts": sentiment_counts,
                "average_score": round(avg_sentiment, 4),
                "total_reviews": len(reviews)
            },
            "demand_forecasts": forecasts,
            "low_stock_alerts": low_stock
        })


# --- Dynamic Pricing -------------------------------------------------------
@api_bp.get("/api/pricing/<int:product_id>")
def get_pricing_suggestion(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        reviews = session.query(Review).filter(Review.product_id == product_id).all()
        avg_sentiment = 0.0
        if reviews:
            avg_sentiment = sum(r.sentiment_score for r in reviews) / len(reviews)

        # Simple demand factor based on order velocity
        recent_orders = session.query(Order).filter(Order.product_id == product_id).count()
        demand_factor = min(0.5 + (recent_orders * 0.02), 1.0)

        calc = _pricing_engine.calculate_optimal_price(
            product_id=product.id,
            base_price=product.price,
            demand_factor=demand_factor,
            stock_kg=product.stock_kg,
            sentiment_score=avg_sentiment
        )

        return _ok({
            "product_id": product.id,
            "product_name": product.name,
            "base_price": calc.base_price,
            "optimal_price": calc.optimal_price,
            "suggested_price": calc.optimal_price,  # backward compatibility
            "change_pct": calc.change_pct,
            "demand_factor": calc.demand_factor,
            "stock_factor": calc.stock_factor,
            "sentiment_factor": calc.sentiment_factor,
            "capped": calc.capped
        })


# Backward compatibility: apply dynamic price
@api_bp.post("/api/products/<int:product_id>/price/apply")
def apply_price(product_id):
    """Recompute and persist the dynamically suggested price."""
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        transactions = session.query(Order).filter(Order.product_id == product_id).all()
        demand = demand_score(transactions)
        suggestion = _pricing_engine.calculate_optimal_price(
            product_id=product.id,
            base_price=product.price,
            demand_factor=demand,
            stock_kg=product.stock_kg,
        )

        from database.models import PriceLog
        session.add(
            PriceLog(
                product_id=product.id,
                old_price=product.price,
                new_price=suggestion.optimal_price,
                reason=suggestion.reason,
            )
        )
        product.price = suggestion.optimal_price
        result = {
            "product_id": product.id,
            "new_price": product.price,
            "base_price": suggestion.base_price,
            "suggested_price": suggestion.optimal_price,
            "change_pct": suggestion.change_pct,
            "reason": suggestion.reason,
            "demand_factor": suggestion.demand_factor,
            "stock_factor": suggestion.stock_factor,
        }

    return _ok(result)


# Product analytics endpoint
@api_bp.get("/api/products/<int:product_id>/analytics")
def product_analytics(product_id):
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        transactions = session.query(Order).filter(Order.product_id == product_id).all()
        reviews = session.query(Review).filter(Review.product_id == product_id).all()
        price_logs = session.query(PriceLog).filter(PriceLog.product_id == product_id).order_by(PriceLog.created_at).all()

        peer_prices = [
            peer.price
            for peer in session.query(Product)
            .filter(Product.category == product.category, Product.id != product.id)
            .all()
            if peer.price and peer.price > 0
        ]
        competitor_min = min(peer_prices) if peer_prices else None
        competitor_max = max(peer_prices) if peer_prices else None

        demand = demand_score(transactions)
        # Calculate sentiment from reviews
        avg_sentiment = 0.0
        if reviews:
            avg_sentiment = sum(r.sentiment_score for r in reviews) / len(reviews)

        suggestion = _pricing_engine.calculate_optimal_price(
            product_id=product.id,
            base_price=product.price,
            demand_factor=demand,
            stock_kg=product.stock_kg,
            sentiment_score=avg_sentiment
        )

        return _ok(
            {
                "product": product.to_dict(),
                "summary": sales_summary(transactions),
                "ratings": rating_summary(reviews),
                "demand_score": demand,
                "daily_revenue": revenue_by_day(transactions),
                "price_histories": [log.to_dict() for log in price_logs],
                "suggested_price": {
                    "suggested_price": suggestion.optimal_price,
                    "base_price": suggestion.base_price,
                    "change_pct": suggestion.change_pct,
                    "reason": suggestion.reason,
                    "demand_factor": suggestion.demand_factor,
                    "stock_factor": suggestion.stock_factor,
                },
            }
        )


# Product forecast endpoint
@api_bp.get("/api/products/<int:product_id>/forecast")
def product_forecast(product_id):
    try:
        horizon = int(request.args.get("horizon", 7))
    except ValueError:
        horizon = 7
    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)
        orders = session.query(Order).filter(Order.product_id == product_id).all()
        historical_data = [
            {"date": o.created_at.date().isoformat(), "quantity": o.quantity_kg}
            for o in orders
        ]
        forecast = _forecaster.predict_demand(
            product.id,
            historical_data,
            forecast_days=horizon,
        )
        return _ok(
            {
                "product_id": product.id,
                "history": [{"date": d["date"], "quantity": d["predicted_quantity"]} for d in forecast.predictions] if historical_data else [],
                "history_dates": [d["date"] for d in forecast.predictions] if historical_data else [],
                "dates": [d["date"] for d in forecast.predictions],
                "values": [d["predicted_quantity"] for d in forecast.predictions],
                "trend_slope": forecast.trend_slope,
            }
        )


# --- Demand Forecast -------------------------------------------------------
@api_bp.get("/api/forecast/<int:product_id>")
def get_demand_forecast(product_id):
    horizon = min(int(request.args.get("horizon", 7)), 30)

    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return _err("product not found", 404)

        orders = session.query(Order).filter(Order.product_id == product_id).all()
        historical_data = [
            {"date": o.created_at.date().isoformat(), "quantity": o.quantity_kg}
            for o in orders
        ]

        forecast = _forecaster.predict_demand(product.id, historical_data, forecast_days=horizon)

        return _ok({
            "product_id": product.id,
            "product_name": product.name,
            "forecast": forecast.predictions,
            "trend_slope": forecast.trend_slope,
            "model_used": forecast.model_used
        })


# Import Config for health endpoint
from config import Config