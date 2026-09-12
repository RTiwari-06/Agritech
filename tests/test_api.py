"""REST API integration tests (in-memory SQLite, no external dependencies)."""

import pytest

from api.app import create_app
from config import Config
from database.connection import dispose_engines


@pytest.fixture()
def app():
    original_uri = Config.DATABASE_URI
    original_pg = Config.PG_DATABASE_URI
    Config.DATABASE_URI = "sqlite:///:memory:"
    Config.PG_DATABASE_URI = ""
    dispose_engines()
    application, _ = create_app()
    yield application
    dispose_engines()
    Config.DATABASE_URI = original_uri
    Config.PG_DATABASE_URI = original_pg


@pytest.fixture()
def client(app):
    return app.test_client()


# --- Health / basic ---------------------------------------------------------------
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["status"] == "ok"


# --- Product CRUD ------------------------------------------------------------------
def test_product_crud_flow(client):
    create_response = client.post(
        "/api/products",
        json={
            "name": "Cherry Tomatoes",
            "category": "vegetables",
            "price": 4.50,
            "quantity": 120.0,
            "unit": "kg",
            "keywords": "tomatoes, fresh",
            "seller": {"name": "Ana Farms", "email": "ana@example.com"},
        },
    )
    assert create_response.status_code == 201
    product_id = create_response.get_json()["data"]["id"]
    assert product_id > 0

    listing = client.get("/api/products")
    assert listing.status_code == 200
    assert any(item["id"] == product_id for item in listing.get_json()["data"])

    detail = client.get(f"/api/products/{product_id}")
    assert detail.status_code == 200
    assert detail.get_json()["data"]["name"] == "Cherry Tomatoes"

    not_found = client.get("/api/products/999999")
    assert not_found.status_code == 404


def test_create_product_requires_name(client):
    response = client.post("/api/products", json={"price": 10})
    assert response.status_code == 400


# --- Transactions ------------------------------------------------------------------
def test_create_transaction(client):
    product_id = _create_product(client, name="Sweet Corn", price=1.00, quantity=50.0)
    response = client.post(
        "/api/transactions",
        json={
            "product_id": product_id,
            "quantity": 10.0,
            "buyer_name": "Bob",
            "buyer_phone": "+15550001111",
        },
    )
    assert response.status_code == 201
    txn = response.get_json()["data"]["transaction"]
    assert txn["quantity"] == 10.0
    assert txn["unit_price"] == 1.00

    detail = client.get(f"/api/products/{product_id}").get_json()["data"]
    assert detail["quantity"] == 40.0


def test_insufficient_stock_rejected(client):
    product_id = _create_product(client, name="Kale", price=2.0, quantity=1.0)
    response = client.post(
        "/api/transactions",
        json={"product_id": product_id, "quantity": 5.0, "buyer_name": "Test"},
    )
    assert response.status_code == 409
    assert "insufficient stock" in response.get_json()["error"].lower()


def test_missing_product_rejected(client):
    response = client.post(
        "/api/transactions",
        json={"product_id": 999999, "quantity": 1.0},
    )
    assert response.status_code == 404


def test_list_transactions_filter(client):
    product_id = _create_product(client, name="Bananas", price=0.5, quantity=20.0)
    client.post(
        "/api/transactions",
        json={"product_id": product_id, "quantity": 2.0, "buyer_name": "Dana"},
    )
    response = client.get("/api/transactions", query_string={"product_id": product_id})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert all(txn["product_id"] == product_id for txn in data)


# --- Reviews -----------------------------------------------------------------------
def test_create_review(client):
    product_id = _create_product(client, name="Organic Eggs", price=5.5, quantity=40.0)
    response = client.post(
        f"/api/products/{product_id}/reviews",
        json={
            "rating": 5,
            "comment": "Absolutely fresh and delicious!",
            "buyer_name": "Eva",
        },
    )
    assert response.status_code == 201
    review = response.get_json()["data"]
    assert review["sentiment_label"] == "positive"
    assert review["sentiment_score"] > 0
    assert review["rating"] == 5


def test_list_reviews(client):
    product_id = _create_product(client, name="Bell Peppers", price=2.8, quantity=30.0)
    client.post(
        f"/api/products/{product_id}/reviews",
        json={"rating": 3, "comment": "Arrived on time.", "buyer_name": "Frank"},
    )
    response = client.get(f"/api/products/{product_id}/reviews")
    assert response.status_code == 200
    assert len(response.get_json()["data"]) == 1


def test_invalid_rating_rejected(client):
    product_id = _create_product(client, name="Mint", price=1.6, quantity=10.0)
    response = client.post(
        f"/api/products/{product_id}/reviews",
        json={"rating": 9, "comment": "Too spicy."},
    )
    assert response.status_code == 400


# --- Optimisation -------------------------------------------------------------------
def test_recommendations(client):
    product_id = _create_product(
        client, name="Fresh Tomatoes", category="vegetables", price=3.5, quantity=60.0,
        keywords="tomatoes, fresh, organic",
    )
    response = client.get("/api/recommendations", query_string={"q": "fresh ripe tomatoes"})
    assert response.status_code == 200
    results = response.get_json()["data"]
    assert results and results[0]["id"] == product_id


def test_pricing_suggestion(client):
    product_id = _create_product(client, name="Avocados", price=1.8, quantity=300.0)
    response = client.get(f"/api/pricing/{product_id}")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["product_id"] == product_id
    assert "suggested_price" in data


def test_apply_price(client):
    product_id = _create_product(client, name="Kale", price=2.0, quantity=5.0)
    response = client.post(f"/api/products/{product_id}/price/apply")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["new_price"] != 2.0
    detail = client.get(f"/api/products/{product_id}").get_json()["data"]
    assert detail["price"] == data["new_price"]


def test_analytics(client):
    product_id = _create_product(client, name="Mangoes", price=3.2, quantity=80.0)
    client.post(
        "/api/transactions",
        json={"product_id": product_id, "quantity": 5.0, "buyer_name": "Grace"},
    )
    client.post(
        f"/api/products/{product_id}/reviews",
        json={"rating": 4, "comment": "Pretty good taste.", "buyer_name": "Grace"},
    )
    response = client.get(f"/api/products/{product_id}/analytics")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["summary"]["order_count"] == 1
    assert data["ratings"]["count"] == 1
    assert "suggested_price" in data


def test_forecast(client):
    product_id = _create_product(client, name="Bell Peppers", price=2.8, quantity=50.0)
    response = client.get(
        f"/api/products/{product_id}/forecast", query_string={"horizon": 7}
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert len(data["values"]) == 7
    assert data["history"] == []  # no transactions yet


# --- Helpers -----------------------------------------------------------------------
def _create_product(
    client,
    name: str = "Test Product",
    category: str = "general",
    price: float = 1.0,
    quantity: float = 10.0,
    keywords: str = "",
) -> int:
    response = client.post(
        "/api/products",
        json={
            "name": name,
            "category": category,
            "price": price,
            "quantity": quantity,
            "keywords": keywords,
            "seller": {"name": "Test Seller"},
        },
    )
    return response.get_json()["data"]["id"]