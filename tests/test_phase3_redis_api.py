"""Focused contract tests for the Redis-backed Phase 3 routes."""

from flask import Flask
import pytest

from api.routes import api_bp
from optimization.engine import DynamicPricingEngine


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app.test_client()


def _product(
    *,
    dynamic_price: float = 12.0,
    stock_kg: float = 100.0,
    day2_price: float = 10.0,
    capped: bool = False,
) -> dict:
    return {
        "id": 7,
        "name": "Tomatoes",
        "category": "Vegetable",
        "day1_price": 9.0,
        "day2_price": day2_price,
        "dynamic_price": dynamic_price,
        "price_change_pct": 20.0,
        "stock_kg": stock_kg,
        "capped": capped,
    }


class _FakeNlp:
    """Stub NL engine shared by the chat-route tests."""

    @staticmethod
    def analyze_sentiment(message: str) -> dict:
        return {"sentiment_score": 0.8, "sentiment_label": "POSITIVE"}

    @staticmethod
    def detect_intent(message: str) -> str:
        return "QUALITY_INQUIRY"


def test_order_route_defaults_to_demo_buyer(client, monkeypatch):
    """Spec §Phase 3.5 — buyer_id is optional in the required payload."""
    import api.routes as routes

    product = _product()
    persisted = {}
    monkeypatch.setattr(routes, "get_product", lambda product_id: product)
    monkeypatch.setattr(routes, "_priced_products", lambda: [product])
    monkeypatch.setattr(
        routes,
        "create_redis_order",
        lambda product_id, quantity_kg, buyer_id, price_per_kg, order_id: (
            persisted.update(
                product_id=product_id, buyer_id=buyer_id, quantity_kg=quantity_kg
            )
            or "created",
            98.0,
        ),
    )

    response = client.post(
        "/api/orders",
        json={"product_id": "7", "quantity_kg": 2.0},
    )

    assert response.status_code == 201
    assert persisted["buyer_id"] == "demo_buyer"
    assert persisted["product_id"] == "7"


def test_order_route_persists_receipt_and_remaining_stock(client, monkeypatch):
    import api.routes as routes

    product = _product()
    persisted = {}
    monkeypatch.setattr(routes, "get_product", lambda product_id: product)
    monkeypatch.setattr(routes, "_priced_products", lambda: [product])

    def create_order(product_id, quantity_kg, buyer_id, price_per_kg, order_id):
        persisted.update(
            product_id=product_id,
            quantity_kg=quantity_kg,
            buyer_id=buyer_id,
            price_per_kg=price_per_kg,
            order_id=order_id,
        )
        return "created", 96.5

    monkeypatch.setattr(routes, "create_redis_order", create_order)
    response = client.post(
        "/api/orders",
        json={"product_id": "7", "quantity_kg": 3.5, "buyer_id": "buyer-12"},
    )

    assert response.status_code == 201
    order = response.get_json()["data"]
    assert order["quantity_kg"] == 3.5
    assert order["remaining_stock_kg"] == 96.5
    assert order["price_per_kg"] == 12.0
    assert persisted["buyer_id"] == "buyer-12"
    assert persisted["product_id"] == "7"


def test_order_route_returns_insufficient_stock(client, monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(routes, "get_product", lambda product_id: _product(stock_kg=1))
    monkeypatch.setattr(routes, "_priced_products", lambda: [_product(stock_kg=1)])
    monkeypatch.setattr(
        routes, "create_redis_order", lambda *args: ("insufficient", None)
    )

    response = client.post(
        "/api/orders",
        json={"product_id": "7", "quantity_kg": 2, "buyer_id": "buyer-12"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Insufficient stock"


def test_analytics_returns_live_market_aggregates(client, monkeypatch):
    import api.routes as routes

    products = [
        _product(dynamic_price=12, stock_kg=49, day2_price=10),
        {**_product(dynamic_price=21, stock_kg=200, day2_price=20), "id": 8},
    ]
    monkeypatch.setattr(routes, "_priced_products", lambda: products)

    response = client.get("/api/analytics")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_products"] == 2
    assert payload["total_stock_kg"] == 249
    assert payload["avg_surge_pct"] == 12.5
    assert payload["low_stock_count"] == 1
    assert payload["data"] == {
        "total_products": 2,
        "total_stock_kg": 249,
        "avg_surge_pct": 12.5,
        "low_stock_count": 1,
    }


def test_health_includes_redis_database_status(client, monkeypatch):
    import api.routes as routes

    class FakeRedis:
        @staticmethod
        def ping():
            return True

    monkeypatch.setattr(routes, "get_client", lambda: FakeRedis())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["db"] is True


def test_streams_fall_back_to_default_when_redis_set_is_empty(client, monkeypatch):
    import api.routes as routes

    class FakeRedis:
        @staticmethod
        def smembers(key):
            assert key == "streams:all"
            return set()

    monkeypatch.setattr(routes, "get_client", lambda: FakeRedis())

    response = client.get("/api/streams")

    assert response.status_code == 200
    assert response.get_json()["data"] == [
        {
            "id": "1",
            "title": "Bangalore APMC Live Auction",
            "status": "active",
        }
    ]


def test_streams_return_redis_hash_details_when_present(client, monkeypatch):
    import api.routes as routes

    class FakeRedis:
        @staticmethod
        def smembers(key):
            assert key == "streams:all"
            return {"17"}

        @staticmethod
        def hgetall(key):
            assert key == "stream:17"
            return {
                "id": "17",
                "stream_title": "Nashik Harvest",
                "is_active": "1",
            }

    monkeypatch.setattr(routes, "get_client", lambda: FakeRedis())

    response = client.get("/api/streams")

    assert response.status_code == 200
    assert response.get_json()["data"] == [
        {"id": "17", "title": "Nashik Harvest", "status": "active"}
    ]


def test_products_returns_empty_list_when_redis_has_no_products(client, monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(routes, "get_all_products", lambda: [])
    monkeypatch.setattr(routes, "get_recent_chats", lambda limit: [])

    response = client.get("/api/products")

    assert response.status_code == 200
    assert response.get_json()["data"] == []


def test_products_reports_redis_unavailable_without_uncaught_error(client, monkeypatch):
    import api.routes as routes
    from redis.exceptions import RedisError

    def unavailable():
        raise RedisError("connection refused")

    monkeypatch.setattr(routes, "get_all_products", unavailable)

    response = client.get("/api/products")

    assert response.status_code == 503
    assert response.get_json()["error"] == "Redis unavailable"


def test_redis_client_uses_loopback_resp2_configuration():
    from database.redis_db import _pool

    assert _pool.connection_kwargs["host"] == "127.0.0.1"
    assert _pool.connection_kwargs["port"] == 6379
    assert _pool.connection_kwargs["db"] == 0
    assert _pool.connection_kwargs["decode_responses"] is True
    assert _pool.connection_kwargs["protocol"] == 2


def test_seller_update_writes_supported_product_fields(client, monkeypatch):
    import api.routes as routes

    updates = {}
    product = _product(dynamic_price=10, capped=True)
    monkeypatch.setattr(
        routes,
        "update_product_fields",
        lambda product_id, fields: updates.update(fields) is None,
    )
    monkeypatch.setattr(routes, "_priced_products", lambda: [product])

    response = client.put(
        "/api/products/7",
        json={"stock_kg": 250.0, "day2_price": 10.0, "capped": True},
    )

    assert response.status_code == 200
    assert updates == {"stock_kg": 250.0, "day2_price": 10.0, "capped": "true"}
    assert response.get_json()["data"]["dynamic_price"] == 10
    assert response.get_json()["data"]["capped"] is True


def test_chat_post_persists_recalculated_dynamic_price(client, monkeypatch):
    """Phase 3.4 — chat ingestion writes the recalculated price to the Hash."""
    import api.routes as routes

    persisted = {}
    product = _product(day2_price=10.0, stock_kg=500.0)
    monkeypatch.setattr(routes, "_chat_nlp", _FakeNlp())
    monkeypatch.setattr(routes, "push_chat_message", lambda message: None)
    monkeypatch.setattr(routes, "get_all_products", lambda: [product])
    monkeypatch.setattr(routes, "get_recent_chats", lambda limit: [])
    monkeypatch.setattr(
        routes,
        "update_product_fields",
        lambda product_id, fields: persisted.update(
            product_id=product_id, fields=fields
        )
        or True,
    )

    response = client.post(
        "/api/streams/chats",
        json={"product_id": "7", "message": "Need 500kg onions immediately!"},
    )

    assert response.status_code == 201
    assert persisted["product_id"] == "7"
    assert persisted["fields"]["capped"] in ("true", "false")
    assert persisted["fields"]["dynamic_price"] >= 10.0


def test_chat_signals_prefers_product_scoped_window():
    """Spec §2.2 — V is averaged over the product's own chat window."""
    import api.routes as routes

    chats = [
        {"product_id": "17", "sentiment_score": 0.8},
        {"product_id": "17", "sentiment_score": -0.4},
        {"product_id": "9", "sentiment_score": -0.9},
    ]
    avg_product, engagement_product = routes._chat_signals(chats, product_id="17")
    assert avg_product == pytest.approx(0.2)
    assert 0.0 <= engagement_product <= 1.0

    # A commodity without its own messages falls back to the shared feed.
    avg_fallback, _ = routes._chat_signals(chats, product_id="42")
    assert avg_fallback == pytest.approx((0.8 - 0.4 - 0.9) / 3)

    avg_empty, engagement_empty = routes._chat_signals([], product_id="17")
    assert avg_empty == 0.0
    assert engagement_empty is None


def test_dynamic_pricing_obeys_phase3_bounds_and_manual_clamp():
    engine = DynamicPricingEngine(min_price_ratio=0.1, max_price_ratio=3.0)

    discounted = engine.calculate_optimal_price(
        product_id=1,
        base_price=100.0,
        demand_factor=0.0,
        stock_kg=1500.0,
        live_engagement=0.0,
    )
    surged = engine.calculate_optimal_price(
        product_id=2,
        base_price=100.0,
        demand_factor=1.0,
        stock_kg=0.0,
        sentiment_score=1.0,
        live_engagement=1.0,
    )
    manually_clamped = engine.calculate_optimal_price(
        product_id=3,
        base_price=100.0,
        demand_factor=1.0,
        capped=True,
    )

    assert discounted.optimal_price >= 85.0
    assert surged.optimal_price <= 125.0
    assert manually_clamped.optimal_price == 100.0
    assert manually_clamped.capped is True
