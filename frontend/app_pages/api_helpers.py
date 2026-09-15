"""Shared API helpers, cached data loaders, and rendering functions for Agritech."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import streamlit as st

from config import Config

API = Config.API_BASE_URL.rstrip("/")

# --- Path setup -------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# --- HTTP helpers -----------------------------------------------------------

def _request(path: str, method: str = "GET", payload: Optional[Dict] = None) -> Dict:
    url = f"{API}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def api_get(path: str, params: Optional[Dict] = None) -> Dict:
    url = path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _request(url)


def api_post(path: str, payload: Dict) -> Dict:
    return _request(path, method="POST", payload=payload)


def safe_call(operation: Callable[[], Any], fallback: Any = None):
    try:
        return operation()
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        st.error(
            f"Could not reach the API at {API}. Is the server running? "
            f"Start it with `python main.py`. ({error})"
        )
        return fallback


# --- Cached data loaders ----------------------------------------------------

@st.cache_data(ttl="5m", max_entries=20)
def load_products(search_query=None, category="All", limit=50):
    params = {"limit": limit, "dynamic_price": "true"}
    if search_query:
        params["q"] = search_query
    if category != "All":
        params["category"] = category
    resp = safe_call(lambda: api_get("/api/products", params))
    return resp.get("data", []) if resp and resp.get("ok") else []


@st.cache_data(ttl="2m", max_entries=10)
def load_active_streams():
    resp = safe_call(lambda: api_get("/api/streams", {"active": "true"}))
    return resp.get("data", []) if resp and resp.get("ok") else []


@st.cache_data(ttl="5m", max_entries=10)
def load_recommendations(buyer_id):
    resp = safe_call(lambda: api_get(f"/api/recommendations/{buyer_id}", {"top_n": 5}))
    return resp.get("data", []) if resp and resp.get("ok") else []


@st.cache_data(ttl="5m", max_entries=10)
def load_analytics(seller_id):
    resp = safe_call(lambda: api_get(f"/api/analytics/{seller_id}"))
    return resp.get("data", {}) if resp and resp.get("ok") else {}


@st.cache_data(ttl="2m", max_entries=10)
def load_inventory(seller_id):
    resp = safe_call(lambda: api_get(f"/api/seller/{seller_id}/inventory"))
    return resp.get("data", []) if resp and resp.get("ok") else []


@st.cache_data(ttl="1m", max_entries=10)
def load_seller_streams(seller_id, active_only=False):
    params = {"seller_id": seller_id}
    if active_only:
        params["active"] = "true"
    resp = safe_call(lambda: api_get("/api/streams", params))
    return resp.get("data", []) if resp and resp.get("ok") else []


@st.cache_data(ttl="2m", max_entries=10)
def load_seller(seller_id):
    resp = safe_call(lambda: api_get(f"/api/seller/{seller_id}"))
    return resp.get("data", {}) if resp and resp.get("ok") else {}


# --- Rendering helpers ------------------------------------------------------

SENTIMENT_COLOR = {"POSITIVE": "green", "NEUTRAL": "orange", "NEGATIVE": "red"}
INTENT_COLOR = {
    "PRICE_INQUIRY": "blue",
    "QUALITY_INQUIRY": "green",
    "DELIVERY_INQUIRY": "orange",
    "GENERAL_CHAT": "gray",
}

INTENT_MATERIAL_ICON = {
    "PRICE_INQUIRY": "attach_money",
    "QUALITY_INQUIRY": "verified",
    "DELIVERY_INQUIRY": "local_shipping",
    "GENERAL_CHAT": "chat",
}

SENTIMENT_MATERIAL_ICON = {
    "POSITIVE": "thumb_up",
    "NEUTRAL": "minus",
    "NEGATIVE": "thumb_down",
}


def render_product_card(product: Dict, show_buy: bool = True, buyer_id: Optional[int] = None) -> None:
    seller = product.get("seller") or {}
    seller_name = seller.get("username", "Seller")
    seller_location = seller.get("location", "")

    dynamic_price = product.get("dynamic_price", product.get("price", 0))
    price_change = product.get("price_change_pct", 0)

    with st.container():
        col_img, col_info, col_action = st.columns([1, 3, 1.5])

        with col_img:
            category_icons = {
                "Grain": ":material/grass:",
                "Vegetable": ":material/vegetables:",
                "Fruit": ":material/fruit:",
                "Organic": ":material/organic_shop:",
            }
            icon = category_icons.get(product.get("category", ""), ":material/package:")
            st.markdown(f"<div style='font-size: 48px; text-align: center;'>{icon}</div>", unsafe_allow_html=True)

        with col_info:
            st.markdown(f"### {product['name']}")
            st.caption(f"{seller_name} · {seller_location} · {product.get('category', 'N/A')}")

            price_col1, price_col2 = st.columns(2)
            with price_col1:
                st.metric(
                    "Price",
                    f"₹{dynamic_price:.2f}/kg",
                    delta=f"{price_change:+.1%}" if price_change else None,
                    delta_color="inverse" if price_change and price_change < 0 else "normal",
                )
            with price_col2:
                st.metric("Stock", f"{product.get('stock_kg', 0):.1f} kg")

            st.caption(f"⭐ Rating: {product.get('rating', 0):.1f}/5.0")
            desc = product.get("description", "")
            st.write(desc[:200] + ("..." if len(desc) > 200 else ""))

        with col_action:
            if show_buy and buyer_id:
                with st.form(f"buy_{product['id']}", border=True):
                    st.write("**Place order**")
                    max_qty = max(float(product.get("stock_kg", 1)), 0.5)
                    quantity = st.number_input(
                        "Quantity (kg)",
                        min_value=0.5,
                        max_value=max_qty,
                        value=min(1.0, max_qty),
                        step=0.5,
                        key=f"qty_{product['id']}",
                    )
                    submitted = st.form_submit_button("Buy now", type="primary", width="stretch")

                    if submitted:
                        result = safe_call(
                            lambda: api_post(
                                "/api/orders",
                                {
                                    "buyer_id": buyer_id,
                                    "product_id": product["id"],
                                    "quantity_kg": float(quantity),
                                },
                            )
                        )
                        if result and result.get("ok"):
                            st.success(f"Order placed! {quantity}kg of {product['name']} for ₹{result['data']['order']['total_price']:.2f}")
                            st.rerun()
                        elif result and "error" in result:
                            st.error(result["error"])
            else:
                st.write(f"**Seller:** {seller_name}")
                st.write(f"**Location:** {seller_location}")


def render_chat_message(msg: Dict) -> None:
    sentiment = msg.get("sentiment_label", "NEUTRAL")
    intent = msg.get("intent_tag", "GENERAL_CHAT")
    username = msg.get("username", "User")
    text = msg.get("message_text", "")
    timestamp = msg.get("timestamp", "")

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S")
    except Exception:
        time_str = ""

    avatar = {"POSITIVE": "🟢", "NEUTRAL": "🟡", "NEGATIVE": "🔴"}.get(sentiment, "🟡")

    with st.chat_message(name=username, avatar=avatar):
        st.markdown(text)
        cols = st.columns([1, 1])
        with cols[0]:
            st.badge(
                intent,
                icon=f":material/{INTENT_MATERIAL_ICON.get(intent, 'chat')}",
                color=INTENT_COLOR.get(intent, "gray"),
            )
        with cols[1]:
            st.badge(
                sentiment,
                icon=f":material/{SENTIMENT_MATERIAL_ICON.get(sentiment, 'minus')}",
                color=SENTIMENT_COLOR.get(sentiment, "gray"),
            )
        st.caption(time_str)


def render_live_stream_video(stream: Dict) -> None:
    """Render a live stream video placeholder using st.html."""
    st.html(f"""
    <div style="background: #1a1a1a; border-radius: 8px; height: 300px; display: flex; align-items: center; justify-content: center; color: #888; margin-bottom: 16px;">
        Live Stream: {stream['stream_title']}<br>
        <small>Seller: {stream.get('seller', {}).get('username', 'Unknown')}</small>
    </div>
    """)
