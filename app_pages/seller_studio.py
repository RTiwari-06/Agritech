"""Agritech Marketplace — seller studio page (Redis-backed).

Entry point: ``streamlit run frontend/app.py`` (from the project root);
``frontend/app.py`` wires this page module into ``st.navigation``.

Sections
--------
* Live pricing — ``@st.fragment(run_every=3)`` monitor polling
  ``GET /api/products`` so engine prices reprice within one 3 s cycle.
* Inventory — Redis stock overview with seller controls saved through
  ``PUT /api/products/<id>`` (stock, APMC baseline, clamp state).
* Orders — persistent order receipts from Redis via ``GET /api/orders``.
* Buyer messages — capped chat feed + composer posting to
  ``POST /api/streams/chats`` (VADER scored, feeds dynamic pricing).
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

from datetime import datetime
from typing import Any, Dict, List, cast

import pandas as pd
import streamlit as st

from app_pages.api_helpers import (
    BadgeColor,
    _api_get,
    _api_post,
    _api_put,
    material_symbol,
)
from app_pages.ui import page_header


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Seller workspace for live prices, inventory, and market conversation."""
    page_header(
        "Seller workspace",
        "Your harvest, at a glance.",
        "Track live pricing, review inventory, and follow buyer demand.",
        ":material/inventory_2:",
    )
    section = st.segmented_control(
        "Seller workspace",
        ["Live pricing", "Inventory", "Orders", "Buyer messages"],
        default="Live pricing",
        key="seller_workspace_view",
        label_visibility="collapsed",
    )
    if section == "Inventory":
        _render_inventory_overview()
    elif section == "Orders":
        _render_orders()
    elif section == "Buyer messages":
        _seller_chat_fragment()
    else:
        _live_pricing_monitor(
            {"username": "market-wide"},
            {"stream_title": "Live market"},
        )


def _render_inventory_overview() -> None:
    """Show Redis inventory, seller controls, and persistent order receipts."""
    saved_message = st.session_state.pop("seller_control_message", None)
    if saved_message:
        st.success(saved_message)

    products = _fetch_products_now()
    if not products:
        st.info("Inventory is unavailable until the Flask API is connected.")
        return

    low_stock = [
        product
        for product in products
        if float(product.get("stock_kg") or 0.0) < 50.0
    ]
    with st.container(horizontal=True):
        st.metric("Listed commodities", len(products), border=True)
        st.metric(
            "Total market stock",
            f"{sum(float(p.get('stock_kg') or 0.0) for p in products):,.0f} kg",
            border=True,
        )
        st.metric("Below reorder floor", len(low_stock), border=True)

    st.subheader("Stock overview")
    st.dataframe(
        [
            {
                "Commodity": product.get("name", "Unknown"),
                "Category": product.get("category", "—"),
                "Stock (kg)": float(product.get("stock_kg") or 0.0),
                "APMC (₹/kg)": float(product.get("day2_price") or 0.0),
                "Live price (₹/kg)": float(product.get("dynamic_price") or 0.0),
                "Availability": (
                    "Low stock"
                    if float(product.get("stock_kg") or 0.0) < 50.0
                    else "Available"
                ),
            }
            for product in products
        ],
        hide_index=True,
        width="stretch",
    )
    _render_inventory_controls(products)


def _render_orders() -> None:
    """Display recent order receipts persisted in Redis."""
    st.subheader("Persistent order history")
    orders_response = _api_get("/api/orders", ttl=3)
    orders = (orders_response or {}).get("data") or []
    if orders:
        st.dataframe(
            [
                {
                    "Order": order.get("id", ""),
                    "Commodity ID": order.get("product_id", ""),
                    "Buyer": order.get("buyer_id", ""),
                    "Quantity (kg)": float(order.get("quantity_kg") or 0.0),
                    "Total (₹)": float(order.get("total_price") or 0.0),
                    "Remaining stock (kg)": float(
                        order.get("remaining_stock_kg") or 0.0
                    ),
                    "Placed at": order.get("created_at", ""),
                }
                for order in orders[:20]
            ],
            hide_index=True,
            width="stretch",
        )
    elif orders_response and orders_response.get("ok"):
        st.caption("No orders have been placed yet.")
    else:
        st.info("Order history is unavailable while the REST API is disconnected.")


def _render_inventory_controls(products: List[Dict[str, Any]]) -> None:
    """Allow a seller to update stock, baseline price, and price clamping."""
    st.subheader("Seller controls")
    st.caption("Changes are saved to Redis and reflected in live market prices.")
    product_ids = [str(product["id"]) for product in products]
    product_names = {
        str(product["id"]): str(product.get("name", product["id"]))
        for product in products
    }
    selected_id = st.selectbox(
        "Commodity to update",
        product_ids,
        format_func=lambda value: product_names[value],
        key="seller_control_product",
    )
    selected = next(
        product for product in products if str(product["id"]) == selected_id
    )
    revision = int(st.session_state.get("seller_control_revision", 0))
    with st.container(border=True):
        with st.form(f"seller_controls_{selected_id}_{revision}"):
            stock_kg = st.number_input(
                "Inventory stock (kg)",
                min_value=0.0,
                value=float(selected.get("stock_kg") or 0.0),
                step=1.0,
                key=f"seller_stock_{selected_id}_{revision}",
            )
            day2_price = st.number_input(
                "APMC baseline price (₹/kg)",
                min_value=0.01,
                value=max(float(selected.get("day2_price") or 0.01), 0.01),
                step=0.5,
                key=f"seller_price_{selected_id}_{revision}",
            )
            capped = st.checkbox(
                "Clamp dynamic price to the APMC baseline",
                value=bool(selected.get("capped")),
                key=f"seller_clamp_{selected_id}_{revision}",
            )
            submitted = st.form_submit_button(
                "Save seller controls",
                type="primary",
                width="stretch",
            )

    if not submitted:
        return
    response = _api_put(
        f"/api/products/{selected_id}",
        {
            "stock_kg": stock_kg,
            "day2_price": day2_price,
            "capped": capped,
        },
    )
    if response and response.get("ok"):
        st.session_state["seller_control_message"] = (
            f"Saved controls for {product_names[selected_id]}."
        )
        st.session_state["seller_control_revision"] = revision + 1
        st.session_state["force_price_refresh"] = True
        st.rerun()
    st.error(
        (response or {}).get("error", "Could not save seller controls.")
    )


@st.fragment(run_every=3)
def _seller_chat_fragment() -> None:
    """Buyer-demand chat composer, updated from the shared Redis feed."""
    products = _fetch_products_now()
    response = _api_get("/api/streams/chats", {"limit": 20}, ttl=2)
    messages = (response or {}).get("data") or []
    with st.container(border=True):
        st.markdown("**Live buyer conversation**")
        st.caption("Chat is scored for sentiment and demand intent.")
        if not messages:
            st.caption("No messages yet.")
        for message in reversed(messages):
            with st.chat_message("user"):
                st.markdown(
                    f"**{message.get('username', 'Buyer')}** · "
                    f"{message.get('message_text', '')}"
                )
                st.caption(
                    f"{message.get('sentiment_label', 'NEUTRAL')} · "
                    f"{message.get('intent_tag', 'GENERAL_CHAT')}"
                )

        if not products:
            st.info("Select and message commodities when the API is available.")
            return

        product_ids = [str(product["id"]) for product in products]
        labels = {
            str(product["id"]): str(product.get("name", product["id"]))
            for product in products
        }
        product_id = st.selectbox(
            "Commodity",
            product_ids,
            format_func=lambda value: labels[value],
            key="seller_chat_product",
        )
        prompt = st.chat_input("Write a buyer-facing update…", key="seller_chat_input")
        if prompt:
            result = _api_post(
                "/api/streams/chats",
                {"product_id": product_id, "message": prompt},
            )
            if result and result.get("ok"):
                st.session_state["force_price_refresh"] = True
                st.toast("Update shared with the market.")
                st.rerun()
            else:
                st.toast("Could not send this update.", icon=":material/error:")


# ---------------------------------------------------------------------------
# Live pricing monitor — decoupled HTTP polling view (@st.fragment)
# ---------------------------------------------------------------------------

def _fetch_products_now() -> List[Dict[str, Any]]:
    """Fetch the Redis-backed product feed with a 3-second cache TTL."""
    force_refresh = st.session_state.pop("force_price_refresh", False)
    body = _api_get("/api/products", ttl=0 if force_refresh else 3)
    if not body or not body.get("ok"):
        return []
    data = body.get("data")
    return data if isinstance(data, list) else []


def _live_pricing_monitor(seller: Dict[str, Any], stream_box: Dict[str, Any]) -> None:
    """Mount the auto-refreshing pricing panel (called once per full rerun)."""
    st.space("small")
    _live_pricing_fragment(seller, stream_box.get("stream_title", "Untitled"))


@st.fragment(run_every=3)
def _live_pricing_fragment(seller: Dict[str, Any], stream_title: str) -> None:
    """Active-broadcast pricing monitor.

    Visual blueprint per fragment run —
      * header: active broadcast + host + target product
      * three metrics: optimal price (with %-change badge), live engagement
        index, stock level monitor
      * ``st.line_chart`` of the dynamic-price curve sampled over time

    Data source: ``GET /api/products`` with a 3 s TTL each fragment run. Each sample
    is appended to a rolling 60-point buffer in ``st.session_state``; the
    backend stays fully decoupled (no shared state, no direct DB access).
    """
    rows = _fetch_products_now()
    if not rows:
        st.caption(f"{material_symbol('error')}  Backend unreachable — fragment retries every 3 s.")
        return

    by_id = {int(p["id"]): p for p in rows}
    if st.session_state.get("monitor_product_id") not in by_id:
        st.session_state.pop("monitor_product_id", None)

    pid = st.selectbox(
        f"{material_symbol('checkroom')}  Target product",
        sorted(by_id),
        format_func=lambda i: f"#{i}  {by_id[i].get('name', '')}",
        key="monitor_product_id",
    )
    product = by_id.get(pid)
    if product is None:
        st.info("No products to monitor yet.")
        return

    st.markdown(
        f"{material_symbol('live_tv')}  **Active broadcast**  ·  `{stream_title}`  —  "
        f"{material_symbol('person')}  host: **{seller.get('username', '—')}**"
    )

    low_stock_count = sum(
        float(row.get("stock_kg") or 0.0) < 50.0 for row in rows
    )
    with st.container(horizontal=True):
        st.metric("Commodities", len(rows), border=True)
        st.metric(
            "Average live movement",
            f"{sum(float(row.get('price_change_pct') or 0.0) for row in rows) / len(rows):+.1f}%",
            border=True,
        )
        st.metric("Low stock", low_stock_count, border=True)

    filter_col, stock_col = st.columns([2, 1])
    with filter_col:
        search = st.text_input(
            "Find a commodity",
            placeholder="Search live prices",
            key="seller_market_search",
        ).strip().casefold()
    with stock_col:
        low_stock_only = st.toggle(
            "Low stock only",
            key="seller_market_low_stock",
        )

    st.subheader("Live commodity prices")
    visible_rows = [
        row
        for row in rows
        if (not search or search in str(row.get("name", "")).casefold())
        and (
            not low_stock_only
            or float(row.get("stock_kg") or 0.0) < 50.0
        )
    ]
    st.caption(f"Showing {len(visible_rows)} of {len(rows)} commodities")
    for row in visible_rows:
        day1 = float(row.get("day1_price") or 0.0)
        day2 = float(row.get("day2_price") or 0.0)
        dynamic = float(row.get("dynamic_price") or 0.0)
        change_pct = float(row.get("price_change_pct") or 0.0)
        stock_kg = float(row.get("stock_kg") or 0.0)
        trend_pct = (day2 - day1) / day1 * 100.0 if day1 else 0.0
        with st.container(border=True):
            st.markdown(f"**{row.get('name', 'Unknown')}**")
            col_market, col_dynamic, col_stock, col_clamp = st.columns(4)
            col_market.metric(
                "APMC Market Price",
                f"₹{day2:,.2f}/kg",
                delta=f"{trend_pct:+.1f}% vs Day 1",
                border=True,
            )
            col_dynamic.metric(
                "Dynamic Price",
                f"₹{dynamic:,.2f}/kg",
                delta=f"{change_pct:+.1f}% {'Surge' if change_pct >= 0 else 'Discount'}",
                border=True,
            )
            with col_stock:
                st.metric("Stock Level", f"{stock_kg:,.1f} kg", border=True)
                if stock_kg < 50.0:
                    st.warning("Below the 50 kg reorder floor")
            with col_clamp:
                capped = bool(row.get("capped"))
                st.badge(
                    "Rate clamped" if capped else "Uncapped",
                    icon=material_symbol("warning" if capped else "check_circle"),
                    color=cast(BadgeColor, "orange" if capped else "green"),
                )

    curve: List[Dict[str, Any]] = (
        st.session_state.setdefault("live_price_curve", {})
        .setdefault(str(pid), [])
    )
    sample = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "Optimal (₹/kg)": float(
            product.get("dynamic_price") or product.get("day2_price") or 0.0
        ),
        "APMC baseline (₹/kg)": float(
            product.get("day2_price") or 0.0
        ),
    }
    curve.append(sample)
    del curve[:-60]  # rolling 60-point window (~3 minutes at 3 s cadence)

    m_price, m_engagement, m_stock = st.columns(3)
    m_price.metric(
        "💰 Optimal market price",
        f"₹{sample['Optimal (₹/kg)']:.2f}",
        delta=f"{(product.get('price_change_pct') or 0.0):+.1f}%",
        border=True,
    )
    engagement = product.get("live_engagement")
    m_engagement.metric(
        "🔥 Live engagement index",
        "—" if engagement is None else f"{float(engagement) * 100:.1f}%",
        border=True,
    )
    m_stock.metric(
        "📦 Stock level monitor",
        f"{float(product.get('stock_kg') or 0.0):,.1f} kg",
        border=True,
    )

    st.space("small")
    st.line_chart(pd.DataFrame(curve).set_index("time"), height=220)
    st.caption("Sampled every 3 s via `GET /api/products` · rolling 60-point window")
