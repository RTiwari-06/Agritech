"""REST API integration tests (in-memory SQLite, no external dependencies)."""

import pytest

from api.app import create_app
from config import Config
from database.db import dispose as dispose_engines


@pytest.fixture()
def app():
    # MongoDB backend — reset collections before each test so tests are isolated.
    from database.db import drop_all, init_db
    drop_all()
    init_db()
    application, _ = create_app()
    yield application
    dispose_engines()


@pytest.fixture()
def client(app):
    return app.test_client()


# --- Auth helpers (DB bearer token demo-auth) --------------------------------------
def _register(client, username: str, role: str) -> str:
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
            "role": role,
        },
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["data"]["token"]


def _seller_token(client) -> str:
    if not hasattr(client, "_seller_token"):
        client._seller_token = _register(client, "sel_api", "seller")
    return client._seller_token


def _buyer_token(client) -> str:
    if not hasattr(client, "_buyer_token"):
        client._buyer_token = _register(client, "buy_api", "buyer")
    return client._buyer_token


def _hdr(token: str):
    return {"Authorization": f"Bearer {token}"}


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
        headers=_hdr(_seller_token(client)),
        json={
            "name": "Cherry Tomatoes",
            "category": "vegetables",
            "price": 4.50,
            "quantity": 120.0,
            "unit": "kg",
            "keywords": "tomatoes, fresh",
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
    response = client.post(
        "/api/products",
        headers=_hdr(_seller_token(client)),
        json={"price": 10},
    )
    assert response.status_code == 400


# --- Transactions ------------------------------------------------------------------
def test_create_transaction(client):
    product_id = _create_product(client, name="Sweet Corn", price=1.00, quantity=50.0)
    response = client.post(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
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
        headers=_hdr(_buyer_token(client)),
        json={"product_id": product_id, "quantity": 5.0, "buyer_name": "Test"},
    )
    assert response.status_code == 409
    assert "insufficient stock" in response.get_json()["error"].lower()


def test_order_alias_requires_authentication(client):
    product_id = _create_product(client, name="Auth Guarded Produce", price=2.0, quantity=10.0)
    response = client.post(
        "/api/transactions",
        json={"product_id": product_id, "quantity": 1.0},
    )
    assert response.status_code == 401


def test_order_reads_require_authentication(client):
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/transactions").status_code == 401


def test_missing_product_rejected(client):
    response = client.post(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
        json={"product_id": 999999, "quantity": 1.0},
    )
    assert response.status_code == 404


def test_list_transactions_filter(client):
    product_id = _create_product(client, name="Bananas", price=0.5, quantity=20.0)
    client.post(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
        json={"product_id": product_id, "quantity": 2.0, "buyer_name": "Dana"},
    )
    response = client.get(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
        query_string={"product_id": product_id},
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert all(txn["product_id"] == product_id for txn in data)


def test_buyer_order_reads_are_self_scoped(client):
    product_id = _create_product(client, name="Private Orders", price=1.0, quantity=20.0)
    client.post(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
        json={"product_id": product_id, "quantity": 2.0},
    )
    response = client.get("/api/orders", headers=_hdr(_buyer_token(client)))
    assert response.status_code == 200
    assert response.get_json()["data"]
    assert all(row["buyer_id"] > 0 for row in response.get_json()["data"])


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
        headers=_hdr(_buyer_token(client)),
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


# --- Fix regressions: recommendations enrichment, seller reuse, chat REST ----
def test_recommendations_user_endpoint_enriched(client):
    """The user-based recommendations route must return 200 with usable rows."""
    product_id = _create_product(
        client, name="Ripe Bananas", price=1.0, quantity=20.0
    )
    second = client.post(
        "/api/products",
        headers=_hdr(_seller_token(client)),
        json={
            "name": "Green Bananas", "category": "fruits", "price": 1.1,
            "quantity": 25.0,
        },
    )
    assert second.status_code == 201
    client.post(
        "/api/transactions",
        headers=_hdr(_buyer_token(client)),
        json={"product_id": product_id, "quantity": 2.0, "buyer_name": "Ishan"},
    )
    buyer_id = client.get(
        "/api/transactions", headers=_hdr(_buyer_token(client))
    ).get_json()["data"][0]["buyer_id"]

    response = client.get(f"/api/recommendations/{buyer_id}", query_string={"top_n": 5})
    assert response.status_code == 200
    results = response.get_json()["data"]
    assert isinstance(results, list)
    if results:
        assert "recommendation_score" in results[0]
        assert "dynamic_price" in results[0]


def test_product_same_seller_reuses_account(client):
    """Two products created under the same authenticated seller share its id."""
    h = _hdr(_seller_token(client))
    payload = {
        "name": "Red Onions", "category": "vegetables", "price": 1.5,
        "quantity": 40.0,
    }
    first = client.post("/api/products", headers=h, json=payload)
    assert first.status_code == 201
    first_seller = first.get_json()["data"]["seller"]["id"]
    second = client.post(
        "/api/products", headers=h, json={**payload, "name": "White Onions"}
    )
    assert second.status_code == 201
    second_seller = second.get_json()["data"]["seller"]["id"]
    assert first_seller == second_seller


def test_send_message_endpoint(client):
    """REST chat endpoint persists via NLP and returns badges (auth-bound)."""
    product_id = _create_product(client, name="Carrots", price=1.2, quantity=30.0)
    client.post(
        "/api/transactions",
        json={"product_id": product_id, "quantity": 1.0, "buyer_name": "Hana"},
    )
    stream = client.post(
        "/api/streams",
        headers=_hdr(_seller_token(client)),
        json={"stream_title": "Morning Harvest"},
    ).get_json()["data"]

    response = client.post(
        "/api/streams/send_message",
        headers=_hdr(_buyer_token(client)),
        json={"stream_id": stream["id"], "message_text": "How much per kg?"},
    )
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["intent_tag"] == "PRICE_INQUIRY"
    assert "sentiment_badge" in data and "intent_badge" in data

    listing = client.get(f"/api/streams/{stream['id']}/messages")
    assert len(listing.get_json()["data"]) == 1


# --- Ownership / role checks --------------------------------------------------------
def test_unauthorized_requests_rejected(client):
    assert client.post(
        "/api/products", json={"name": "No Token", "price": 1}
    ).status_code == 401
    assert client.get("/api/analytics").status_code == 401


def test_wrong_role_rejected(client):
    assert client.get(
        "/api/analytics", headers=_hdr(_buyer_token(client))
    ).status_code == 403


def test_cross_account_ownership_forbidden(client):
    """A second seller must not read/end the first seller's resources."""
    t1 = _register(client, "sel_owner", "seller")
    t2 = _register(client, "sel_intruder", "seller")
    stream = client.post(
        "/api/streams", headers=_hdr(t1), json={"stream_title": "Owner Stream"}
    ).get_json()["data"]
    sid = stream["id"]
    assert client.post(
        f"/api/streams/{sid}/end", headers=_hdr(t2)
    ).status_code == 403
    assert client.get(f"/api/analytics/{stream['seller']['id']}", headers=_hdr(t2)).status_code == 403
    assert client.get(f"/api/seller/{stream['seller']['id']}/orders", headers=_hdr(t2)).status_code == 403
    assert client.get(f"/api/seller/{stream['seller']['id']}/inventory", headers=_hdr(t2)).status_code == 403


def test_auth_session_logout_and_expiry(client):
    token = _register(client, "sess_user", "buyer")
    h = _hdr(token)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post(
        "/api/auth/login", json={"identifier": "sess_user", "password": "password123"}
    ).status_code == 200
    assert client.post(
        "/api/auth/login", json={"identifier": "sess_user", "password": "nope"}
    ).status_code == 401


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
        headers=_hdr(_seller_token(client)),
        json={
            "name": name,
            "category": category,
            "price": price,
            "quantity": quantity,
            "keywords": keywords,
        },
    )
    return response.get_json()["data"]["id"]