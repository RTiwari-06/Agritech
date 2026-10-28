"""Insights page — Redis market analytics for the Agritech workspace.

Everything on this page is derived from the Redis REST feed:

* ``GET /api/analytics``     — live market aggregates (fragment every 3 s)
* ``GET /api/products``      — commodity-level inventory and dynamic prices
* ``GET /api/orders``        — persistent order receipts (sales + revenue)
* ``GET /api/streams/chats`` — VADER-scored buyer chat (sentiment mix)

Order receipts are aggregated client-side with the shared optimisation
helpers (``optimization.metrics``/``DemandForecaster``), so the analytics
layer stays fully decoupled from the data store.
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

from typing import Any, Dict, List, Optional, cast

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app_pages.api_helpers import (
    BadgeColor,
    MATERIAL_SYMBOLS,
    _api_get,
    chart_sentiment_pie,
    material_symbol,
)
from app_pages.ui import page_header
from optimization.engine import create_demand_forecaster
from optimization.metrics import restock_advice, revenue_by_day, sales_summary

LOW_STOCK_KG = 50.0
REVENUE_WINDOW_DAYS = 7
CHAT_SAMPLE_LIMIT = 100
MAX_RESTOCK_CARDS = 6


# ---------------------------------------------------------------------------
# Cached loaders (Redis REST feed)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3, show_spinner=False)
def _load_products() -> List[Dict[str, Any]]:
    resp = _api_get("/api/products", ttl=3)
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data")
    return data if isinstance(data, list) else []


@st.cache_data(ttl=3, show_spinner=False)
def _load_orders() -> List[Dict[str, Any]]:
    resp = _api_get("/api/orders", ttl=3)
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data")
    return data if isinstance(data, list) else []


@st.cache_data(ttl=3, show_spinner=False)
def _load_chat_sentiment() -> List[Dict[str, Any]]:
    resp = _api_get("/api/streams/chats", {"limit": CHAT_SAMPLE_LIMIT}, ttl=3)
    if not resp or not resp.get("ok"):
        return []
    data = resp.get("data")
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Chart helpers
# --------------------------------------------------------------------------

def _revenue_chart(daily_data: list[dict]) -> Optional[go.Figure]:
    """Bar chart of daily revenue."""
    if not daily_data:
        return None
    dates = [d.get("date", "") for d in daily_data]
    amounts = [d.get("amount", 0) for d in daily_data]
    fig = px.bar(
        pd.DataFrame({"date": dates, "revenue": amounts}),
        x="date", y="revenue",
        color="revenue",
        color_continuous_scale=["#0F5F31", "#34D399"],
        labels={"date": ""},
    )
    fig.update_layout(
        title=f"Daily revenue (last {REVENUE_WINDOW_DAYS} days)",
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1F352B"),
        coloraxis_showscale=False,
    )
    return fig


@st.fragment(run_every=3)
def _live_market_metrics() -> None:
    """Render the Redis-backed market metrics independently every 3 seconds."""
    response = _api_get("/api/analytics", ttl=0)
    if not response or not response.get("ok"):
        st.info("Live market analytics are unavailable while the REST API is disconnected.")
        return
    data = response.get("data")
    metrics = data if isinstance(data, dict) else response
    required = (
        "total_products",
        "total_stock_kg",
        "avg_surge_pct",
        "low_stock_count",
    )
    if any(key not in metrics for key in required):
        st.warning("The analytics endpoint returned an incomplete metrics payload.")
        return

    with st.container(horizontal=True):
        st.metric("Total commodities", int(metrics["total_products"]), border=True)
        st.metric(
            "Market liquidity",
            f"{float(metrics['total_stock_kg']):,.1f} kg",
            border=True,
        )
        st.metric(
            "Average price surge",
            f"{float(metrics['avg_surge_pct']):+.2f}%",
            border=True,
        )
        st.metric("Low stock commodities", int(metrics["low_stock_count"]), border=True)


# ---------------------------------------------------------------------------
# Commodity-level views
# ---------------------------------------------------------------------------

def _stock_status_label(stock_kg: float) -> str:
    if stock_kg <= 0:
        return "Out of stock"
    if stock_kg < 20:
        return "Critical"
    if stock_kg < LOW_STOCK_KG:
        return "Low"
    if stock_kg > 500:
        return "Overstock"
    return "Good"


def _render_inventory_status(products: List[Dict[str, Any]]) -> None:
    """Inventory table straight from the Redis commodity feed."""
    if not products:
        st.caption(
            f"{material_symbol('info')} "
            "No commodities loaded yet — run the APMC PDF seeder and keep the "
            "REST engine running."
        )
        return

    rows: List[Dict[str, Any]] = [
        {
            "Commodity": prod.get("name", "Unknown"),
            "Category": prod.get("category", "N/A"),
            "Stock (kg)": float(prod.get("stock_kg") or 0.0),
            "APMC (₹/kg)": float(prod.get("day2_price") or 0.0),
            "Live price (₹/kg)": float(prod.get("dynamic_price") or 0.0),
            "Movement": f"{float(prod.get('price_change_pct') or 0.0):+.1f}%",
            "Status": _stock_status_label(float(prod.get("stock_kg") or 0.0)),
        }
        for prod in products
    ]
    st.dataframe(
        rows,
        hide_index=True,
        column_config={
            "Stock (kg)": st.column_config.NumberColumn("Stock (kg)", format="%.1f"),
            "APMC (₹/kg)": st.column_config.NumberColumn("APMC (₹/kg)", format="₹%.2f"),
            "Live price (₹/kg)": st.column_config.NumberColumn("Live price (₹/kg)", format="₹%.2f"),
            "Status": st.column_config.TextColumn("Status"),
        },
        width="stretch",
    )

    low_stock = [row for row in rows if row["Stock (kg)"] < LOW_STOCK_KG]
    if low_stock:
        st.space("small")
        with st.container(border=True):
            for row in low_stock:
                st.badge(
                    "LOW STOCK",
                    icon=":material/warning:",
                    color=cast(BadgeColor, "orange"),
                )
                st.markdown(
                    f"{material_symbol('warning')} "
                    f"**{row['Commodity']}** — only {row['Stock (kg)']:.1f} kg remaining."
                )


def _render_sales_snapshot(orders: List[Dict[str, Any]]) -> bool:
    """Sales aggregation over the persistent Redis order receipts."""
    summary = sales_summary(orders)
    total_orders = summary.get("order_count") or 0
    if not total_orders:
        return False

    with st.container(horizontal=True):
        st.metric(
            f"{material_symbol('currency_rupee')} Total revenue",
            value=f"₹{float(summary.get('revenue') or 0.0):,.2f}",
            border=True,
        )
        st.metric(
            f"{material_symbol('shopping_cart')} Total orders",
            value=total_orders,
            border=True,
        )
        st.metric(
            f"{material_symbol('inventory_2')} Quantity sold",
            value=f"{float(summary.get('total_units') or 0.0):,.1f} kg",
            border=True,
        )
        st.metric(
            f"{material_symbol('trending_up')} Avg order value",
            value=f"₹{float(summary.get('avg_order_value') or 0.0):,.2f}",
            border=True,
        )
    return True


def _render_revenue_trend(orders: List[Dict[str, Any]]) -> None:
    """Daily revenue roll-up from Redis order receipts."""
    daily_rows = [
        {"date": row.get("date", ""), "amount": float(row.get("revenue") or 0.0)}
        for row in revenue_by_day(orders, days=REVENUE_WINDOW_DAYS)
    ]
    fig_rev = _revenue_chart(daily_rows)
    if fig_rev:
        st.plotly_chart(fig_rev, theme=None)
    else:
        st.caption(
            f"{material_symbol('info')} "
            "Revenue tracking requires historical order data. "
            "As orders accumulate over time, this section will show "
            "daily/weekly revenue trends with a bar chart."
        )


def _render_buyer_sentiment(chats: List[Dict[str, Any]]) -> None:
    """VADER sentiment mix over the live buyer chat feed."""
    counts: Dict[str, int] = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
    score_total = 0.0
    scored = 0
    for chat in chats:
        label = str(chat.get("sentiment_label") or "NEUTRAL").upper()
        if label in counts:
            counts[label] += 1
        score_total += float(chat.get("sentiment_score", chat.get("sentiment", 0.0)) or 0.0)
        scored += 1
    avg_sent = (score_total / scored) if scored else 0.0

    c_sent, c_gauge = st.columns([1, 1])
    with c_sent:
        if sum(counts.values()) > 0:
            chart_sentiment_pie(counts)
        else:
            st.caption(f"{material_symbol('info')} No buyer chat yet.")

    with c_gauge:
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=avg_sent * 100,
                title={"text": "Overall sentiment", "font": {"size": 14}},
                gauge={
                    "axis": {"range": [-100, 100], "tickwidth": 1},
                    "bar": {"color": "#34D399"},
                    "steps": [
                        {"range": [-100, -30], "color": "#7F1D1D"},
                        {"range": [-30, 30], "color": "#78716C"},
                        {"range": [30, 100], "color": "#14532D"},
                    ],
                    "threshold": {
                        "line": {"color": "#E8F1EC", "width": 2},
                        "thickness": 0.75,
                        "value": avg_sent * 100,
                    },
                },
            )
        )
        fig_gauge.update_layout(
            height=240,
            template="plotly_white",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#1F352B"),
        )
        st.plotly_chart(fig_gauge, theme=None)


def _render_pricing_overview(products: List[Dict[str, Any]]) -> None:
    """Dynamic pricing recommendations from the Redis feed."""
    if products:
        pricing_cols = st.columns(min(len(products), 4))
        for i, prod in enumerate(products[:4]):
            with pricing_cols[i]:
                st.metric(
                    label=(prod.get("name") or "Unknown")[:22],
                    value=f"₹{float(prod.get('dynamic_price') or 0.0):.2f}/kg",
                    delta=f"{float(prod.get('price_change_pct') or 0.0):+.1f}% dynamic",
                    border=True,
                )
    else:
        st.caption(f"{material_symbol('info')} No products to recommend prices for yet.")


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Market insights & optimization dashboard — Redis-backed."""
    page_header(
        "Insights",
        "The story behind every market.",
        "Live aggregates, inventory health, revenue trends, and buyer sentiment "
        "drawn straight from the Redis feed.",
        ":material/analytics:",
    )
    st.subheader("Live market performance")
    _live_market_metrics()
    st.divider()

    products = _load_products()
    orders = _load_orders()
    chats = _load_chat_sentiment()

    # ------------------------------------------------------------------
    # Sales snapshot + revenue trend
    # ------------------------------------------------------------------
    st.subheader(f"{MATERIAL_SYMBOLS['chart_line']} Sales snapshot")
    if _render_sales_snapshot(orders):
        st.space("small")
        _render_revenue_trend(orders)
    else:
        st.caption(
            f"{material_symbol('info')} "
            "No orders recorded yet. Confirm checkout orders in the Market page "
            "and this section will aggregate them automatically."
        )

    # ------------------------------------------------------------------
    # Buyer sentiment analysis
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{MATERIAL_SYMBOLS['star']} Buyer sentiment analysis")
    _render_buyer_sentiment(chats)

    # ------------------------------------------------------------------
    # Restock recommendations (demand forecast per commodity from orders)
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{MATERIAL_SYMBOLS['inventory_2']} Restock recommendations")
    st.caption(
        f"{material_symbol('info')} "
        "Reorder advice blends each commodity's demand forecast (built from "
        "order receipts) against current Redis stock."
    )
    _render_restock_recommendations(_build_restock_rows(products, orders))

    # ------------------------------------------------------------------
    # Inventory status
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{MATERIAL_SYMBOLS['inventory_2']} Inventory status")
    _render_inventory_status(products)

    # ------------------------------------------------------------------
    # Dynamic pricing recommendations
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('trending_up')} Dynamic pricing recommendations")
    st.caption(
        f"{material_symbol('info')} "
        "Recommended prices based on inventory scarcity and chat sentiment."
    )
    _render_pricing_overview(products)


# ---------------------------------------------------------------------------
# Restock helpers
# ---------------------------------------------------------------------------

def _build_restock_rows(
    products: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Forecast per commodity from order receipts -> restock advice."""
    forecaster = create_demand_forecaster()
    orders_by_product: Dict[str, List[Dict[str, Any]]] = {}
    for order in orders:
        product_id = str(order.get("product_id") or "")
        quantity = float(order.get("quantity_kg") or 0.0)
        placed_at = order.get("created_at") or ""
        if not product_id or quantity <= 0:
            continue
        orders_by_product.setdefault(product_id, []).append(
            {"date": str(placed_at)[:10], "quantity": quantity}
        )

    rows: List[Dict[str, Any]] = []
    for product in products:
        product_id = str(product.get("id") or "")
        stock_kg = float(product.get("stock_kg") or 0.0)
        history = orders_by_product.get(product_id)
        if not history:
            continue
        forecast = forecaster.predict_demand(
            product_id=product_id,
            historical_data=history,
            forecast_days=REVENUE_WINDOW_DAYS,
        )
        forecast_values = [
            float(pred.get("predicted_quantity") or 0.0)
            for pred in forecast.predictions
        ]
        advice = restock_advice(stock_kg, forecast_values)
        rows.append(
            {
                "product_name": product.get("name", f"Product {product_id}"),
                "current_stock_kg": stock_kg,
                "priority": advice.get("priority", "none"),
                "days_until_out": advice.get("days_until_out"),
                "recommended_reorder_kg": advice.get("recommended_reorder_kg", 0.0),
            }
        )

    def _urgency(row: Dict[str, Any]) -> tuple:
        days = row.get("days_until_out")
        return (0, float(days)) if days is not None else (1, 9999.0)

    rows.sort(key=_urgency)
    return rows[:MAX_RESTOCK_CARDS]


def _render_restock_recommendations(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        st.caption(f"{material_symbol('info')} No restock recommendations yet — place orders first.")
        return
    for rec in rows:
        priority = rec.get("priority", "none")
        icon = {
            "critical": "priority_high",
            "high": "warning",
            "low_stock": "inventory_2",
            "ok": "check_circle",
        }.get(priority, "info")
        color = {
            "critical": "red",
            "high": "orange",
            "low_stock": "yellow",
            "ok": "green",
        }.get(priority, "blue")
        days_until_out = rec.get("days_until_out")
        days_text = (
            f" · {float(days_until_out):.0f} d of stock left"
            if days_until_out is not None
            else ""
        )
        with st.container(border=True):
            st.badge(
                str(priority).upper(),
                icon=f":material/{icon}:",
                color=cast(BadgeColor, color),
            )
            st.markdown(
                f"**{rec.get('product_name', 'Unknown')}** · "
                f"{float(rec.get('current_stock_kg') or 0.0):.0f} kg in stock{days_text} · "
                f"reorder {float(rec.get('recommended_reorder_kg') or 0.0):.0f} kg"
            )
