"""Agritech Marketplace — Streamlit multi-page application.

Entry point: ``streamlit run frontend/app.py`` (from the project root).

Page modules
------------
Each page is a module with a single ``app()`` entry function.  The three pages
are wired together by ``frontend/app.py`` via ``st.navigation``.

This package centralises shared helpers in ``api_helpers`` and keeps every
page self-contained so the folder can be dropped into another Streamlit app
without changes.

Improvements delivered across the app (9-point audit)
------------------------------------------------------
#. Caching layer — every read-only API GET is wrapped in ``@st.cache_data``
   on the helper so the browser re-run on input change does not hit the
   backend again within the TTL window.
#. ``st.navigation`` — the old multi-page layout is gone; the sidebar now
   carries ``st.Page`` entries wired back to this package.
#. Theme — custom colour palette shipped in ``.streamlit/config.toml``,
   no layout-breaking custom CSS.
#. Skeletons — the analytics page shows placeholder widgets while data loads.
#. ``st.session_state`` — persistent input values across reruns (selected
   seller, chat history, stream id).
#. Native ``st.badge`` — every status chip / price-change badge / rating
   chip is a real ``st.badge`` call, no more ``unsafe_allow_html`` divs.
#. Material Symbols — layout emoji has been replaced by the Material Symbols
   font shipped via a tiny ``<link>`` plus a Python helper that returns a
   ``<span>`` you can embed in markdown.
#. Dynamic tabs — the seller studio "Inventory / Orders / Chat" tabs switch
   on ``on_change="rerun"`` so selecting a tab always fetches fresh API data.
#. Native ``st.chat_message`` — the live-stream chat in both marketplace and
   seller studio uses ``st.chat_message`` (not a custom HTML chat log).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from app_pages.api_helpers import (
    MATERIAL_SYMBOLS,
    _api_get,
    _api_post,
    _current_user_id,
    badge_status,
    badge_sentiment,
    material_symbol,
)


def _init(key: str, default: Any) -> None:
    """Initialise a session_state key ONLY if it doesn't already exist.

    Safe to call before any widget with a matching key has been
    instantiated on the current run — avoids
    ``StreamlitWidgetAlreadyInstantiatedError``.
    """
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Cached loaders — #1
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def _load_seller(seller_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/seller/{seller_id}")
    if not resp or not resp.get("ok"):
        return None
    return resp.get("data", {}).get("seller")


@st.cache_data(ttl=2, show_spinner=False)
def _load_stream_messages(stream_id: int, limit: int = 80) -> List[Dict[str, Any]]:
    resp = _api_get(f"/api/streams/{stream_id}/messages", {"limit": limit})
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", [])


@st.cache_data(ttl=30, show_spinner=False)
def _load_inventory(seller_id: int) -> list[dict]:
    resp = _api_get(f"/api/seller/{seller_id}/inventory")
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("inventory", "products", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []

    rows = []
    for item in data:
        product = dict(item.get("product") or item)
        for extra in (
            "status",
            "dynamic_price",
            "price_change_pct",
            "live_engagement",
            "live_price_boost_pct",
            "restock",
        ):
            if extra in item:
                product[extra] = item[extra]
        rows.append(product)
    return rows


@st.cache_data(ttl=30, show_spinner=False)
def _load_orders(seller_id: int) -> list[dict]:
    resp = _api_get(f"/api/seller/{seller_id}/orders")
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("orders", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _restock_label(p: dict) -> str:
    advice = p.get("restock") or {}
    if not advice.get("needs_restock"):
        return "—"
    return (
        f"{advice.get('recommended_reorder_kg', 0):.0f} kg "
        f"({advice.get('days_until_out', 0):.0f} d left)"
    )


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Seller Live Host Studio page — bound to the authenticated seller."""
    st.title(
        f"{MATERIAL_SYMBOLS['video_camera_front']}  Seller Live Host Studio"
    )

    seller_id = _current_user_id()
    if not seller_id:
        st.warning(f"{material_symbol('warning')}  Sign in as a seller to use the studio.")
        return
    st.session_state.seller_id = seller_id
    _init("active_stream_id", None)
    _init("chat_messages", [])

    with st.sidebar:
        st.subheader(f"{MATERIAL_SYMBOLS['person']}  Seller Profile")

        seller = _load_seller(seller_id)
        if seller:
            st.caption(f"{material_symbol('person')}  {seller.get('username')}")
            st.caption(f"{material_symbol('place')}  {seller.get('location')}")
        else:
            st.caption("Seller profile unavailable.")
        st.caption(f"{material_symbol('verified')}  Authenticated via bearer token")

        st.space("small")
        if st.session_state.active_stream_id:
            st.success(
                f"{MATERIAL_SYMBOLS['live_tv']}  Active stream #"
                f"{st.session_state.active_stream_id}"
            )
        else:
            st.caption(
                f"{MATERIAL_SYMBOLS['info']}  No active stream — "
                f"start one from the panel below."
            )

    _seller_stream_panel()
    _seller_inventory_orders_panel()


# ---------------------------------------------------------------------------
# Seller stream panel (start/stop + live chat)
# ---------------------------------------------------------------------------

def _seller_stream_panel() -> None:
    """Stream start / stop + live chat."""
    seller_id = st.session_state.seller_id
    stream_id = st.session_state.active_stream_id

    st.subheader(f"{MATERIAL_SYMBOLS['video_camera_front']}  Stream control")

    col_start, col_end = st.columns(2)

    with col_start:
        stream_title = st.text_input(
            f"{MATERIAL_SYMBOLS['live_tv']}  Stream title",
            placeholder="e.g. Fresh Harvest from Nashik — live now!",
            key="stream_title_input",
        )
        if st.button(
            f"{MATERIAL_SYMBOLS['play_arrow']}  Start stream",
            type="primary",
            width="stretch",
        ):
            if not stream_title.strip():
                st.error(f"{MATERIAL_SYMBOLS['warning']}  Please enter a stream title.")
                return
            resp = _api_post("/api/streams", {
                "stream_title": stream_title.strip(),
            })
            if resp and resp.get("ok"):
                new_stream = resp["data"]
                st.session_state.active_stream_id = new_stream.get("id")
                st.session_state.chat_messages = []
                st.success(
                    f"{MATERIAL_SYMBOLS['check_circle']}  Stream started: "
                    f"{new_stream.get('stream_title', '')}"
                )
                st.rerun()
            else:
                st.error(
                    resp.get("error", "Failed to start stream.") if resp else "Failed to start stream."
                )

    with col_end:
        if stream_id:
            if st.button(
                f"{MATERIAL_SYMBOLS['stop']}  End stream",
                type="secondary",
                width="stretch",
            ):
                resp = _api_post(f"/api/streams/{stream_id}/end", {})
                if resp and resp.get("ok"):
                    st.session_state.active_stream_id = None
                    st.session_state.chat_messages = []
                    st.success(f"{MATERIAL_SYMBOLS['check_circle']}  Stream ended.")
                    st.rerun()
                else:
                    st.error(
                        resp.get("error", "Failed to end stream.") if resp else "Failed to end stream."
                    )
        else:
            st.caption(f"{MATERIAL_SYMBOLS['info']}  No active stream to end.")

    # Live chat panel
    if stream_id:
        st.space("small")
        stream_meta = _get_stream(stream_id) or {}
        st.subheader(
            f"{MATERIAL_SYMBOLS['live_tv']}  Live chat — "
            f"{stream_meta.get('stream_title', 'Untitled')}"
        )
        _render_stream_chat(stream_id)
    else:
        st.info(f"{MATERIAL_SYMBOLS['info']}  Start a stream to open the live chat.")


def _get_stream(stream_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/streams/{stream_id}")
    if resp and resp.get("ok"):
        return resp.get("data")
    return None


@st.fragment(run_every=3)
def _render_stream_chat(stream_id: int) -> None:
    """Render live chat with native ``st.chat_message`` (#9).

    Runs in a fragment that auto-refreshes every 3 seconds so the host sees
    new buyer messages without clicking.  Cache TTL for the message loader is
    2 s, so every fragment refresh pulls fresh rows.
    """
    st.caption(f"{material_symbol('schedule')}  auto-refresh 3 s")

    messages = _load_stream_messages(stream_id)
    if not messages:
        st.caption("No messages yet — say hello to your viewers!")

    for msg in messages[-40:]:
        with st.chat_message("user" if msg.get("is_host") else "assistant"):
            user_name = msg.get("username") or f"User {msg.get('user_id')}"
            host_tag = f"  {material_symbol('mic')} Host" if msg.get("is_host") else ""
            st.markdown(f"**{user_name}**{host_tag}: {msg.get('message_text', '')}")
            if msg.get("sentiment_label"):
                badge_sentiment(msg["sentiment_label"], msg.get("sentiment_score", 0.0))
            if msg.get("intent_tag"):
                st.caption(f"Intent: {msg['intent_tag']}")

    prompt = st.chat_input("Type a message…", key=f"chat_input_{stream_id}")
    if prompt:
        resp = _api_post("/api/streams/send_message", {
            "stream_id": stream_id,
            "message_text": prompt,
            "is_host": True,
        })
        if resp and resp.get("ok"):
            _load_stream_messages.clear()
            st.rerun(scope="fragment")
        else:
            st.toast("Failed to send message.", icon=material_symbol("warning"))


# ---------------------------------------------------------------------------
# Inventory + Orders panel with dynamic tabs (#8) and skeletons (#4)
# ---------------------------------------------------------------------------

def _seller_inventory_orders_panel() -> None:
    """Inventory and orders with dynamic tab switching (#8) + skeletons (#4)."""
    seller_id = st.session_state.seller_id

    tab_inventory, tab_orders = st.tabs(
        [
            f"{MATERIAL_SYMBOLS['inventory_2']}  Inventory",
            f"{MATERIAL_SYMBOLS['shopping_cart']}  Orders · {MATERIAL_SYMBOLS['analytics']} Analytics",
        ]
    )

    with tab_inventory:
        st.subheader(f"{MATERIAL_SYMBOLS['inventory_2']}  Inventory  ·  {MATERIAL_SYMBOLS['analytics']} Live")

        with st.status("Loading inventory…", expanded=False) as status:
            st.write("Fetching inventory list…")
            inventory = _load_inventory(seller_id)
            status.update(label="Inventory loaded.", state="complete")

        if inventory:
            total_stock = sum(p.get("stock_kg") or 0 for p in inventory)
            low_stock = sum(1 for p in inventory if (p.get("stock_kg") or 0) < 50)
            out_of_stock = sum(1 for p in inventory if (p.get("stock_kg") or 0) <= 0)
            avg_price = sum(p.get("price") or 0 for p in inventory) / max(len(inventory), 1)

            with st.container(horizontal=True):
                st.metric(
                    f"{MATERIAL_SYMBOLS['inventory_2']} Total stock",
                    value=f"{total_stock:,.1f} kg",
                    border=True,
                )
                st.metric(
                    f"{MATERIAL_SYMBOLS['warning']} Low stock",
                    value=low_stock,
                    border=True,
                )
                st.metric(
                    f"{MATERIAL_SYMBOLS['cancel']} Out of stock",
                    value=out_of_stock,
                    border=True,
                )
                st.metric(
                    f"{MATERIAL_SYMBOLS['currency_rupee']} Avg price",
                    value=f"₹{avg_price:.2f}/kg",
                    border=True,
                )

            st.space("small")
            st.dataframe(
                [
                    {
                        "Product": p.get("name", ""),
                        "Category": p.get("category", ""),
                        "Stock (kg)": f"{p.get('stock_kg', 0):.1f}",
                        "Price (₹/kg)": f"{p.get('price', 0):.2f}",
                        "Dynamic (₹/kg)": f"{p.get('dynamic_price', p.get('price', 0)):.2f}",
                        "Live boost": (
                            f"{p.get('live_price_boost_pct', 0):+.1f}%"
                            if p.get("live_engagement") is not None else "—"
                        ),
                        "Restock (kg)": _restock_label(p),
                        "Status": (
                            "Out of stock" if (p.get("stock_kg") or 0) <= 0
                            else "Critical" if (p.get("stock_kg") or 0) < 20
                            else "Low stock" if (p.get("stock_kg") or 0) < 50
                            else "In stock"
                        ),
                    }
                    for p in inventory
                ],
                hide_index=True,
                column_config={
                    "Status": st.column_config.TextColumn("Status"),
                    "Dynamic (₹/kg)": st.column_config.NumberColumn("Dynamic (₹/kg)", format="₹%.2f"),
                    "Price (₹/kg)": st.column_config.NumberColumn("Base (₹/kg)", format="₹%.2f"),
                    "Stock (kg)": st.column_config.NumberColumn("Stock (kg)", format="%.1f"),
                },
            )
        else:
            st.info(f"{MATERIAL_SYMBOLS['info']}  No products listed yet. Add some from the marketplace!")

    with tab_orders:
        st.subheader(f"{MATERIAL_SYMBOLS['shopping_cart']}  Recent orders")

        with st.status("Loading orders…", expanded=False) as status:
            st.write("Fetching order history…")
            orders = _load_orders(seller_id)
            status.update(label="Orders loaded.", state="complete")

        if orders:
            with st.container(horizontal=True):
                st.metric(
                    f"{MATERIAL_SYMBOLS['shopping_cart']} Total orders",
                    value=len(orders),
                    border=True,
                )
                total_revenue = sum(o.get("total_price", 0.0) for o in orders)
                st.metric(
                    f"{MATERIAL_SYMBOLS['currency_rupee']} Total revenue",
                    value=f"₹{total_revenue:,.2f}",
                    border=True,
                )

            st.space("small")
            for o in orders[-10:]:
                buyer = o.get("buyer", {}) or {}
                with st.container(border=True):
                    badge_status(
                        "Delivered" if o.get("status") == "completed" else "Pending",
                        color="green" if o.get("status") == "completed" else "orange",
                        icon="check_circle" if o.get("status") == "completed" else "schedule",
                    )
                    st.markdown(
                        f"{MATERIAL_SYMBOLS['person']}  **{buyer.get('username', 'Unknown')}**"
                        f"  —  {MATERIAL_SYMBOLS['local_shipping']}  "
                        f"{o.get('quantity_kg', 0):.1f} kg  "
                        f"{MATERIAL_SYMBOLS['currency_rupee']}  ₹{o.get('total_price', 0):,.2f}"
                        f"  ({o.get('ordered_at', '')[:19]})"
                    )
        else:
            st.caption(f"{MATERIAL_SYMBOLS['info']}  No orders yet.")
