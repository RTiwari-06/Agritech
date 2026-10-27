"""Agritech — Phase 2 marketplace presentation layer (Redis-backed).

Market & Shop page:
  * ``@st.fragment(run_every=3)`` product matrix — polls ``GET /api/products``
    through ``api_helpers._api_get`` so Redis Hashes repriced from VADER
    sentiment and buy-signal chatter render within one 3-second cycle.
  * Every commodity renders one bordered row of four columns: government
    APMC baseline (Day 2 metric), optimal live price (surge/discount delta),
    stock monitor (with the 50 kg reorder callout), and clamp status.
  * Live sentiment chat posting to ``POST /api/streams/chats`` — the Flask
    layer scores with VADER, tags intent, and pushes to the capped Redis
    List feeding the pricing engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Page-module bootstrap: when a page is executed directly as the Streamlit
# script (or imported with only app_pages/ on sys.path), the repo root is
# absent and `app_pages`/`config` fail with ModuleNotFoundError.  Insert the
# root the same way frontend/app.py does at entry-point level.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from typing import Any, Dict, List, cast  # noqa: E402

import streamlit as st  # noqa: E402

from app_pages.api_helpers import (  # noqa: E402
    BadgeColor,
    _api_get,
    _api_post,
    material_symbol,
)
from app_pages.ui import page_header  # noqa: E402

LOW_STOCK_KG = 50.0
CHAT_POLL_LIMIT = 20


# ---------------------------------------------------------------------------
# Product matrix — 3-second decoupled polling loop
# ---------------------------------------------------------------------------

@st.fragment(run_every=3)
def _product_matrix() -> None:
    """Redis-driven commodity matrix; one bordered 4-column row per commodity."""
    force_refresh = st.session_state.pop("force_price_refresh", False)
    resp = _api_get("/api/products", ttl=0 if force_refresh else 3)
    products: List[Dict[str, Any]] = ((resp or {}).get("data") or [])
    receipt = st.session_state.pop("last_order_receipt", None)
    if receipt:
        st.success(
            f"Order placed for {receipt['quantity_kg']:g} kg. "
            f"Remaining stock: {receipt['remaining_stock_kg']:,.1f} kg."
        )
    if not products:
        st.caption(
            f"{material_symbol('error')}  /api/products unreachable — the fragment "
            "retries every 3 s (check the Flask backend and Redis availability)."
        )
        return

    st.caption(
        f"{material_symbol('refresh')}  live · polled every 3 s · "
        f"{len(products)} commodities · engagement/sentiment from Redis chat feed"
    )

    low_stock_count = sum(
        float(product.get("stock_kg") or 0.0) < LOW_STOCK_KG
        for product in products
    )
    average_change = sum(
        float(product.get("price_change_pct") or 0.0)
        for product in products
    ) / len(products)
    with st.container(horizontal=True):
        st.metric("Commodities", len(products), border=True)
        st.metric("Average price movement", f"{average_change:+.1f}%", border=True)
        st.metric("Low stock", low_stock_count, border=True)

    categories = sorted(
        {str(product.get("category") or "Other") for product in products}
    )
    search_col, category_col, stock_col = st.columns([2, 1, 1])
    with search_col:
        search = st.text_input(
            "Find a commodity",
            placeholder="Search by name",
            key="market_search",
        ).strip().casefold()
    with category_col:
        category = st.selectbox("Category", ["All categories", *categories])
    with stock_col:
        low_stock_only = st.toggle("Low stock only", key="market_low_stock")

    visible_products = [
        product
        for product in products
        if (not search or search in str(product.get("name", "")).casefold())
        and (category == "All categories" or product.get("category") == category)
        and (
            not low_stock_only
            or float(product.get("stock_kg") or 0.0) < LOW_STOCK_KG
        )
    ]
    st.subheader("Live commodity board")
    st.caption(f"Showing {len(visible_products)} of {len(products)} commodities")
    for product in visible_products:
        with st.container(border=True):
            st.markdown(
                f"{material_symbol('storefront')}  **#{product.get('id')} — "
                f"{product.get('name', '')}**  ·  `{product.get('category', '')}`"
            )
            _product_columns(product)
            st.space("small")
    _checkout_panel(products)


def _checkout_panel(products: List[Dict[str, Any]]) -> None:
    """Submit a buyer checkout request to the persistent Redis order API."""
    with st.container(border=True):
        st.subheader("Place an order")
        product_ids = [str(product["id"]) for product in products]
        product_names = {
            str(product["id"]): str(product.get("name", product["id"]))
            for product in products
        }
        with st.form("market_checkout"):
            product_id = st.selectbox(
                "Commodity",
                product_ids,
                format_func=lambda value: product_names[value],
                key="checkout_product",
            )
            quantity_kg = st.number_input(
                "Quantity (kg)",
                min_value=0.1,
                value=1.0,
                step=0.5,
                key="checkout_quantity",
            )
            buyer_id = st.text_input(
                "Buyer ID",
                value="demo_buyer",
                key="checkout_buyer_id",
            ).strip()
            submitted = st.form_submit_button(
                "Confirm order",
                type="primary",
                width="stretch",
            )

        if not submitted:
            return
        result = _api_post(
            "/api/orders",
            {
                "product_id": product_id,
                "quantity_kg": quantity_kg,
                "buyer_id": buyer_id,
            },
        )
        if result and result.get("ok"):
            order = result.get("data") or {}
            st.session_state["last_order_receipt"] = order
            st.session_state["force_price_refresh"] = True
            st.rerun()
        st.error((result or {}).get("error", "Could not place the order."))


def _product_columns(product: Dict[str, Any]) -> None:
    """The mandated 4-column metric matrix for one commodity."""
    day1 = float(product.get("day1_price") or 0.0)
    day2 = float(product.get("day2_price") or 0.0)
    dynamic = float(product.get("dynamic_price") or 0.0)
    change_pct = float(product.get("price_change_pct") or 0.0)
    stock_kg = float(product.get("stock_kg") or 0.0)
    capped = bool(product.get("capped"))

    trend_pct = round((day2 - day1) / day1 * 100.0, 1) if day1 else 0.0
    direction = "Surge" if change_pct >= 0 else "Discount"

    c_gov, c_live, c_stock, c_clamp = st.columns(4)

    # Column 1 — government baseline (Day 2 of the APMC report)
    c_gov.metric(
        "APMC Market Price",
        f"\u20B9{day2:,.2f}/kg",
        delta=f"{trend_pct:+.1f}% vs Day 1",
        border=True,
    )

    # Column 2 — optimal live price from the pricing engine
    c_live.metric(
        "Dynamic Price",
        f"\u20B9{dynamic:,.2f}/kg",
        delta=f"{change_pct:+.1f}% {direction}",
        border=True,
    )

    # Column 3 — stock monitor with the sub-50 kg callout
    with c_stock:
        st.metric("Stock Level", f"{stock_kg:,.1f} kg", border=True)
        if stock_kg < LOW_STOCK_KG:
            st.warning(
                f"{material_symbol('warning')}  Below the {LOW_STOCK_KG:.0f} kg reorder floor"
            )

    # Column 4 — clamp status badge for the bounded dynamic price
    with c_clamp:
        if capped:
            st.badge(
                "Rate clamped at \u00B120% band",
                icon=material_symbol("warning"),
                color=cast(BadgeColor, "orange"),
            )
        else:
            st.badge(
                "Uncapped",
                icon=material_symbol("check_circle"),
                color=cast(BadgeColor, "green"),
            )


# ---------------------------------------------------------------------------
# Live sentiment chat -> POST /api/streams/chats (VADER -> Redis -> price surge)
# ---------------------------------------------------------------------------

@st.fragment(run_every=3)
def _sentiment_chat() -> None:
    """Capped chat feed (newest 20) + composer posting to the Redis pipeline."""
    products_resp = _api_get("/api/products", ttl=3)
    products: List[Dict[str, Any]] = (
        (products_resp or {}).get("data") or []
    )
    resp = _api_get("/api/streams/chats", {"limit": CHAT_POLL_LIMIT}, ttl=2)
    chats: List[Dict[str, Any]] = ((resp or {}).get("data") or [])
    with st.container(border=True):
        st.markdown("**Buyer signal**")
        st.caption("Messages are scored for sentiment and demand intent.")
        if not chats:
            st.caption("No messages yet. Start the conversation.")

        # LPUSH/LTRIM keeps newest-first; render reversed for chronology.
        for chat in reversed(chats):
            with st.chat_message("user"):
                sender = chat.get("username") or f"user_{chat.get('user_id', 0)}"
                st.markdown(f"**{sender}** · {chat.get('message_text', '')}")
                st.caption(
                    f"{chat.get('sentiment_label', 'NEUTRAL')} "
                    f"{float(chat.get('sentiment_score') or 0.0):+.2f} · "
                    f"{chat.get('intent_tag', 'GENERAL_CHAT')}"
                )

    if not products:
        st.info("No commodities available to attach to your message.")
        return

    product_ids = [str(product["id"]) for product in products]
    product_names = {
        str(product["id"]): product.get("name", f"Product {product['id']}")
        for product in products
    }
    product_id = st.selectbox(
        "Commodity",
        product_ids,
        format_func=lambda value: product_names[value],
        key="chat_product_market",
    )
    prompt = st.chat_input("Type a message…", key="chat_input_market")
    if prompt:
        result = _api_post(
            "/api/streams/chats",
            {
                "product_id": product_id,
                "message": prompt,
            },
        )
        if result and result.get("ok"):
            product = result.get("product") or {}
            dynamic_price = product.get("dynamic_price")
            if dynamic_price is not None:
                st.toast(f"Message scored — updated price ₹{float(dynamic_price):,.2f}/kg")
            else:
                st.toast("Message scored — refreshing prices now")
            st.session_state["force_price_refresh"] = True
            st.rerun()
        else:
            st.toast("Failed to send message.", icon=material_symbol("warning"))


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Marketplace — live APMC prices and buyer demand signals."""
    page_header(
        "Market",
        "Know the market. Move with it.",
        "APMC baselines meet live demand signals. Prices refresh automatically.",
        ":material/storefront:",
    )
    market_col, chat_col = st.columns([2.2, 1], gap="large")
    with market_col:
        _product_matrix()
    with chat_col:
        st.subheader("Buyer signal")
        _sentiment_chat()
