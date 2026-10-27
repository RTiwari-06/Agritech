"""Focused tests for the active Redis-backed REST API."""

import pytest
from flask import Flask

from api.routes import api_bp


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app.test_client()


def test_health(client, monkeypatch):
    import api.routes as routes

    class FakeRedis:
        @staticmethod
        def ping():
            return True

    monkeypatch.setattr(routes, "get_client", lambda: FakeRedis())

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["redis"] is True
    assert payload["data"]["status"] == "ok"


def test_redis_products_returns_dynamic_price_fields(client, monkeypatch):
    import api.routes as routes

    class PriceResult:
        optimal_price = 12.5
        change_pct = 0.1
        capped = False

    class FakePricingEngine:
        @staticmethod
        def calculate_optimal_price(**kwargs):
            return PriceResult()

    products = [
        {
            "id": product_id,
            "name": f"Commodity {product_id}",
            "category": "Vegetable",
            "day1_price": 10.0,
            "day2_price": 11.0,
            "stock_kg": 100.0,
        }
        for product_id in range(1, 32)
    ]
    monkeypatch.setattr(routes, "get_all_products", lambda: products)
    monkeypatch.setattr(routes, "get_recent_chats", lambda limit: [])
    monkeypatch.setattr(routes, "_pricing_engine", FakePricingEngine())

    response = client.get("/api/products")

    assert response.status_code == 200
    rows = response.get_json()["data"]
    assert len(rows) == 31
    assert {
        "day1_price",
        "day2_price",
        "dynamic_price",
        "stock_kg",
        "capped",
    }.issubset(rows[0])


def test_redis_chat_accepts_product_message_payload(client, monkeypatch):
    """The migrated REST chat contract accepts message and legacy payloads."""
    import api.routes as routes

    pushed = []

    class FakeNlp:
        @staticmethod
        def analyze_sentiment(message):
            return {"sentiment_score": 0.8, "sentiment_label": "positive"}

        @staticmethod
        def detect_intent(message):
            return "QUALITY_INQUIRY"

    class PriceResult:
        def __init__(self, base_price, sentiment_score, live_engagement):
            self.optimal_price = round(
                base_price
                * (1 + sentiment_score * 0.05 + (live_engagement - 0.5) * 0.1),
                2,
            )
            self.change_pct = round(
                (self.optimal_price - base_price) / base_price,
                4,
            )
            self.capped = False

    class FakePricingEngine:
        @staticmethod
        def calculate_optimal_price(**kwargs):
            return PriceResult(
                kwargs["base_price"],
                kwargs["sentiment_score"],
                kwargs["live_engagement"],
            )

    monkeypatch.setattr(routes, "_chat_nlp", FakeNlp())
    monkeypatch.setattr(routes, "push_chat_message", pushed.append)
    monkeypatch.setattr(
        routes,
        "get_all_products",
        lambda: [
            {
                "id": 17,
                "name": "Tomatoes",
                "category": "Vegetable",
                "day1_price": 10.0,
                "day2_price": 11.0,
                "stock_kg": 25.0,
            }
        ],
    )
    monkeypatch.setattr(routes, "get_recent_chats", lambda limit: pushed)
    monkeypatch.setattr(routes, "_pricing_engine", FakePricingEngine())

    response = client.post(
        "/api/streams/chats",
        json={"product_id": "17", "message": "These tomatoes are fresh"},
    )

    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["product_id"] == "17"
    assert data["message_text"] == "These tomatoes are fresh"
    assert pushed == [data]
    assert response.get_json()["dynamic_price"] > 11.0

    legacy = client.post(
        "/api/streams/chats",
        json={"message_text": "How much per kg?"},
    )
    assert legacy.status_code == 201
    assert legacy.get_json()["data"]["message_text"] == "How much per kg?"
