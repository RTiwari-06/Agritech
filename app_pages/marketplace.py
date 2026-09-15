"""Agritech — marketplace page.

Buyer-facing page: browse products, search, live stream chat, order placement.

Improvements covered in this file:
  #1  Caching layer — loaders use @st.cache_data
  #5  session_state for persistent filter/seller state
  #6  Native st.badge for status/price-change/rating badges
  #7  Material Symbols instead of raw emoji in labels
  #9  Native st.chat_message for stream chat
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from app_pages.api_helpers import (
    _API,
    _api_get,
    badge_status,
    badge_trend,
    badge_rating,
    chart_bar,
    chart_sentiment_pie,
    material_symbol,
    product_card_row,
    skeleton_card,
)


# ---------------------------------------------------------------------------
# Cached loaders — #1
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def _load_products() -> List[Dict[str, Any]]:
    resp = _api_get("/api/products")
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", []) if isinstance(resp.get("data"), list) else []


@st.cache_data(ttl=30, show_spinner=False)
def _load_seller(seller_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/seller/{seller_id}")
    if not resp or not resp.get("ok"):
        return None
    return resp.get("data", {}).get("seller")


@st.cache_data(ttl=15, show_spinner=False)
def _load_live_streams(active: bool = True) -> List[Dict[str, Any]]:
    resp = _api_get(f"/api/streams?active={str(active).lower()}")
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data", [])
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        streams = data.get("streams", [])
        return streams if isinstance(streams, list) else []
    return []


@st.cache_data(ttl=15, show_spinner=False)
def _load_stream_messages(stream_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    resp = _api_get(f"/api/streams/{stream_id}/messages", {"limit": limit})
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", [])


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    st.title(f"{material_symbol('store')}  Agritech Marketplace")

    # #5  Sidebar state — buyer login + filter state
    # Defaults set via session_state BEFORE widget creation; widgets
    # use `key=` only (no `value=`) so Streamlit 1.63's policy check
    # doesn't warn about mixed sources.
    if "buyer_id" not in st.session_state:
        st.session_state.buyer_id = 3  # default seeded buyer
    if "search" not in st.session_state:
        st.session_state.search = ""
    if "products" not in st.session_state:
        st.session_state.products = []

    with st.sidebar:
        st.header(f"{material_symbol('account_circle')}  Buyer")

        st.number_input(
            "Buyer ID",
            min_value=1,
            step=1,
            key="buyer_id",
        )

        buyer = _load_seller(st.session_state.buyer_id)
        if buyer:
            st.caption(f"{material_symbol('person')}  {buyer.get('username')}")
            st.caption(f"{material_symbol('mail')}  {buyer.get('email')}")

        st.divider()
        st.text_input(
            "Search products",
            value=st.session_state.search,
            key="search",
            placeholder="Tomato, spinach, Nashik...",
        )
        categories = sorted({p.get("category") for p in _load_products() if p.get("category")})
        st.selectbox(
            "Category",
            ["All"] + categories,
            key="category",
        )
        st.divider()
        st.caption(f"{material_symbol('refresh')}  Live streams update every 15 s")

    # Fetch products (cached) — read filter values from session_state
    search = st.session_state.search
    category = st.session_state.category
    products = _load_products()
    st.session_state.products = products  # store full list for access by render fns

    if search:
        needle = search.lower()
        products = [
            p for p in products
            if needle in (p.get("name", "") or "").lower()
            or needle in (p.get("category", "") or "").lower()
            or needle in (p.get("seller", {}).get("username", "") or "").lower()
        ]
    if category != "All":
        products = [
            p for p in products
            if p.get("category") == category
        ]

    # Live streams section
    streams = _load_live_streams(active=True)
    if streams:
        st.divider()
        st.subheader(f"{material_symbol('live_tv')}  Live Shopping Streams")
        for s in streams:
            _stream_card(s)

    st.divider()
    st.subheader(f"{material_symbol('grass')}  Products  ·  {len(products)} listed")
    _product_grid(products)


def _stream_card(s: Dict[str, Any]) -> None:
    seller = s.get("seller") or {}
    active = s.get("is_active", True)
    viewers = s.get("viewer_count", 0)

    with st.expander(
        f"{material_symbol('live_tv')}  {s.get('stream_title', 'Live stream')}",
        expanded=True,
    ):
        st.caption(f"{material_symbol('person')}  Seller: {seller.get('username')}")
        st.caption(f"{material_symbol('analytics')}  Viewers: {viewers}")
        badge_status("LIVE" if active else "ENDED", color="green" if active else "red")

        messages = _load_stream_messages(s["id"])
        if messages:
            _chat_window(s["id"], messages)


def _chat_window(stream_id: int, initial_messages: List[Dict[str, Any]]) -> None:
    col_log, col_chat = st.columns([1, 4])

    with col_log:
        st.markdown(f"**{material_symbol('live_tv')}  Live Chat**")
        st.caption(f"{material_symbol('schedule')}  Refresh: 15 s")

    with col_chat:
        st.divider()
        st.subheader(f"{material_symbol('chat')}  Stream Chat")
        _render_chat_messages(initial_messages)

        prompt = st.chat_input("Type a message…")
        if prompt and st.session_state.buyer_id:
            buyer_id = st.session_state.buyer_id
            resp = _api_post("/api/streams/send_message", {
                "stream_id": stream_id,
                "user_id": buyer_id,
                "message_text": prompt,
            })
            if resp and resp.get("ok"):
                st.rerun()
            else:
                st.toast("Failed to send message.", icon="⚠️")


def _render_chat_messages(messages: List[Dict[str, Any]]) -> None:
    for msg in messages[-40:]:
        with st.chat_message("user" if msg.get("is_host") else "assistant"):
            user_name = msg.get("username") or f"User {msg.get('user_id')}"
            st.markdown(f"**{user_name}**: {msg.get('message_text', '')}")
            if msg.get("sentiment_label"):
                st.markdown("---")
                st.caption(f"Sentiment: {msg['sentiment_label']}  ({msg.get('sentiment_score', 0.0):.2f})")
            if msg.get("intent_tag"):
                st.caption(f"Intent: {msg['intent_tag']}")


def _product_grid(products: List[Dict[str, Any]]) -> None:
    if not products:
        st.info("No products match your filters. Try a different search.")
        return

    cols = st.columns(2)
    for idx, p in enumerate(products):
        with cols[idx % 2]:
            st.divider()
            product_card_row(p, idx)


def _api_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import json
    import urllib.request
    url = f"{_API}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))
