"""Agritech — marketplace + live streams pages.

Buyer-facing: browse products, search, order placement (buyer accounts);
plus a dedicated live-shopping page with per-stream chat that auto-refreshes
via ``st.fragment(run_every=3)`` so new messages appear without a click.

Everything that mutates state is bound to the authenticated user:
authorization headers come from ``api_helpers`` and the backend refuses to
attribute orders/chat to anyone but the token's owner.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from app_pages.api_helpers import (
    _api_get,
    _api_post,
    _current_user_id,
    badge_status,
    badge_sentiment,
    material_symbol,
    product_card_row,
)


# ---------------------------------------------------------------------------
# Cached loaders
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


@st.cache_data(ttl=2, show_spinner=False)
def _load_stream_messages(stream_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    resp = _api_get(f"/api/streams/{stream_id}/messages", {"limit": limit})
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", [])


# ---------------------------------------------------------------------------
# Marketplace page
# ---------------------------------------------------------------------------

def app() -> None:
    st.title(f"{material_symbol('store')}  Market & Shop")

    if "search" not in st.session_state:
        st.session_state.search = ""

    with st.sidebar:
        st.subheader(f"{material_symbol('account_circle')}  Buyer")
        user = _current_user_id()
        if user:
            buyer = _load_seller(user)
            if buyer:
                st.caption(f"{material_symbol('person')}  {buyer.get('username')}")
                st.caption(f"{material_symbol('mail')}  {buyer.get('email')}")
            st.caption(f"{material_symbol('verified')}  Authenticated via bearer token")
        else:
            st.caption("Sign in as a buyer to place orders.")

        st.space("small")
        st.text_input(
            "Search products",
            value=st.session_state.search,
            key="search",
            placeholder="Tomato, spinach, Nashik...",
        )
        categories = sorted({p.get("category") for p in _load_products() if p.get("category")})
        st.selectbox("Category", ["All"] + categories, key="category")
        st.space("small")
        st.caption(f"{material_symbol('refresh')}  Products update every 30 s")

    search = st.session_state.search
    category = st.session_state.category
    products = _load_products()
    st.session_state.products = products

    if search:
        needle = search.lower()
        products = [
            p for p in products
            if needle in (p.get("name", "") or "").lower()
            or needle in (p.get("category", "") or "").lower()
            or needle in (p.get("seller", {}).get("username", "") or "").lower()
        ]
    if category != "All":
        products = [p for p in products if p.get("category") == category]

    st.space("small")

    _product_grid(products, hint=f"{material_symbol('grass')}  Products  ·  {len(products)} listed")


# ---------------------------------------------------------------------------
# Live streams page
# ---------------------------------------------------------------------------

def live_streams_page() -> None:
    st.title(f"{material_symbol('live_tv')}  Live shopping streams")
    streams = _load_live_streams(active=True)
    if not streams:
        st.info("No live streams right now — check back soon.")
        return

    for s in streams:
        _stream_card(s)


def _stream_card(s: Dict[str, Any]) -> None:
    seller = s.get("seller") or {}
    active = s.get("is_active", True)
    viewers = s.get("viewer_count", 0)

    with st.container(border=True):
        st.markdown(f"**{s.get('stream_title', 'Live stream')}**")
        st.caption(
            f"{material_symbol('person')} Seller: {seller.get('username', 'Unknown')}  ·  "
            f"{material_symbol('analytics')} Viewers: {viewers}"
        )
        badge_status(
            "LIVE" if active else "ENDED",
            color="green" if active else "red",
            icon="live_tv" if active else "stop",
        )

        messages = _load_stream_messages(s["id"])
        if messages or True:
            st.space("small")
            _chat_window(s["id"])


@st.fragment(run_every=3)
def _chat_window(stream_id: int) -> None:
    """Live chat panel — auto-refreshes every 3 seconds via fragment."""
    st.markdown(
        f"**{material_symbol('chat')}  Live chat**  ·  "
        f"{material_symbol('schedule')} auto-refresh 3 s"
    )
    messages = _load_stream_messages(stream_id)
    _render_chat_messages(messages)

    prompt = st.chat_input("Type a message…")
    if prompt:
        user_id = _current_user_id()
        if not user_id:
            st.toast("Sign in to join the conversation.", icon=material_symbol("warning"))
        else:
            resp = _api_post("/api/streams/send_message", {
                "stream_id": stream_id,
                "message_text": prompt,
            })
            if resp and resp.get("ok"):
                st.rerun()
            else:
                st.toast("Failed to send message.", icon=material_symbol("warning"))


def _render_chat_messages(messages: List[Dict[str, Any]]) -> None:
    for msg in messages[-40:]:
        with st.chat_message("user" if msg.get("is_host") else "assistant"):
            user_name = msg.get("username") or f"User {msg.get('user_id')}"
            host_tag = " 🎙️ Host" if msg.get("is_host") else ""
            st.markdown(f"**{user_name}**{host_tag}: {msg.get('message_text', '')}")
            if msg.get("sentiment_label"):
                badge_sentiment(msg["sentiment_label"], msg.get("sentiment_score", 0.0))
            if msg.get("intent_tag"):
                st.caption(f"Intent: {msg['intent_tag']}")


# ---------------------------------------------------------------------------
# Product grid
# ---------------------------------------------------------------------------

def _product_grid(products: List[Dict[str, Any]], hint: str = "") -> None:
    st.subheader(hint)
    if not products:
        st.info("No products match your filters. Try a different search.")
        return

    cols = st.columns(2)
    for idx, p in enumerate(products):
        with cols[idx % 2]:
            with st.container(border=True):
                product_card_row(p, idx)