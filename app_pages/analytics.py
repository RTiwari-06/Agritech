"""Sales analytics & optimization dashboard page.

Shows seller revenue, sentiment analysis, demand forecasting, live shopping
signal and inventory status.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app_pages.api_helpers import (
    MATERIAL_SYMBOLS,
    _api_get,
    chart_sentiment_pie,
    material_symbol,
)


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def _load_analytics(seller_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/analytics/{seller_id}")
    if not resp or not resp.get("ok"):
        return None
    data = resp.get("data", {})
    if isinstance(data, dict):
        return data
    return None


@st.cache_data(ttl=30, show_spinner=False)
def _load_inventory(seller_id: int) -> List[Dict[str, Any]]:
    resp = _api_get(f"/api/seller/{seller_id}/inventory")
    if not resp or not resp.get("ok"):
        return []
    inv_data = resp.get("data") or []
    if isinstance(inv_data, dict):
        inv_data = inv_data.get("inventory") or inv_data.get("products") or []
    rows = []
    for item in (inv_data if isinstance(inv_data, list) else []):
        product = dict(item.get("product") or item)
        for key in ("status", "live_engagement", "live_price_boost_pct", "restock"):
            if key in item:
                product[key] = item[key]
        rows.append(product)
    return rows


# ---------------------------------------------------------------------------
# Chart helpers
# --------------------------------------------------------------------------

def _forecast_chart(forecasts: list[dict]) -> Optional[go.Figure]:
    """Plotly line chart of demand forecasts per product."""
    if not forecasts:
        return None
    products = [fc.get("product_name", f"Product-{i}")
                for i, fc in enumerate(forecasts)]
    current = [fc.get("current_demand_kg", 0) for fc in forecasts]
    predicted = [fc.get("predicted_demand_kg", 0) for fc in forecasts]
    df = pd.DataFrame({
        "day": ["Current", "7-day forecast"],
        **{
            name: [cur, pred]
            for name, cur, pred in zip(products, current, predicted)
        }
    })
    fig = go.Figure()
    for col in df.columns:
        if col == "day":
            continue
        fig.add_trace(go.Scatter(
            x=df["day"],
            y=df[col],
            mode="lines+markers",
            name=col,
        ))
    fig.update_layout(
        title="Demand forecast per product",
        xaxis_title="",
        yaxis_title="Demand (kg/day)",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8F1EC"),
    )
    return fig


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
        title="Daily revenue (last 7 days)",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8F1EC"),
        coloraxis_showscale=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Sales analytics & optimization dashboard page."""
    st.title(f"{material_symbol('analytics')} Sales analytics & optimization dashboard")

    if "analytics_seller_id" not in st.session_state:
        st.session_state.analytics_seller_id = 1

    st.sidebar.number_input(
        f"{material_symbol('person')} Seller ID",
        min_value=1,
        step=1,
        key="analytics_seller_id",
    )
    seller_id = st.session_state.analytics_seller_id

    with st.status(
        f"{material_symbol('refresh')} Loading analytics for seller #{seller_id} ...",
        expanded=False,
    ) as status:
        st.write(f"{material_symbol('schedule')} Fetching orders, reviews, and forecasts ...")
        analytics = _load_analytics(seller_id)
        status.update(label=f"{material_symbol('check_circle')} Data loaded.", state="complete")

    if not analytics:
        st.warning(
            f"{material_symbol('warning')} "
            "No analytics data available for this seller. "
            "Start selling to see metrics here."
        )
        return

    # Seller info bar
    seller = analytics.get("seller", {})
    with st.container(border=True):
        c_left, c_right = st.columns([2, 1])
        with c_left:
            st.markdown(
                f"{material_symbol('store')} **Seller:** "
                f"{seller.get('username', 'Unknown')} · "
                f"{material_symbol('place')} "
                f"{seller.get('location', 'Unknown')}"
            )
        with c_right:
            email = seller.get("email", "")
            if email:
                st.markdown(f"{material_symbol('mail')} {email}")

    # Sections data
    sales = analytics.get("sales_summary", {})
    sentiment = analytics.get("sentiment_summary", {})
    forecasts = analytics.get("demand_forecasts", [])
    low_stock = analytics.get("low_stock_alerts", [])
    live_engagement = analytics.get("live_engagement")
    restock_recommendations = analytics.get("restock_recommendations", [])

    st.space("small")
    st.subheader(f"{material_symbol('chart_line')} Key performance metrics")

    with st.container(horizontal=True):
        st.metric(
            label=f"{material_symbol('currency_rupee')} Total revenue",
            value=f"₹{sales.get('total_revenue', 0):,.2f}",
            border=True,
        )
        st.metric(
            label=f"{material_symbol('inventory_2')} Total quantity",
            value=f"{sales.get('total_quantity_kg', 0):,.1f} kg",
            border=True,
        )
        st.metric(
            label=f"{material_symbol('shopping_cart')} Total orders",
            value=sales.get("total_orders", 0),
            border=True,
        )
        st.metric(
            label=f"{material_symbol('trending_up')} Avg order value",
            value=f"₹{sales.get('avg_order_value', 0):,.2f}",
            border=True,
        )
        if live_engagement is not None:
            st.metric(
                label=f"{material_symbol('live_tv')} Live engagement",
                value=f"{live_engagement:.1%}",
                delta=f"{material_symbol('sentiment_satisfied')} buyer intention",
                border=True,
            )

    # ------------------------------------------------------------------
    # Live shopping signal + restock recommendations
    # ------------------------------------------------------------------
    if live_engagement is not None or restock_recommendations:
        st.space("small")
        with st.container(border=True):
            st.markdown(f"**{material_symbol('live_tv')} Live shopping signal**")
            if live_engagement is None:
                st.caption(
                    f"{material_symbol('info')} No active stream yet — "
                    f"start a stream to surface a live purchasing-intent signal."
                )
            else:
                st.caption(
                    f"{material_symbol('chat')} Derived from chat velocity, "
                    f"buying intent (price/quality asks), and sentiment in "
                    f"your active stream."
                )

            st.space("small")
            st.markdown(f"**{material_symbol('add_box')} Restock recommendations**")
            if restock_recommendations:
                for rec in restock_recommendations:
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
                    line = (
                        f"{rec.get('product_name', 'Unknown')} · "
                        f"{rec.get('current_stock_kg', 0):.0f} kg in stock · "
                        f"reorder {rec.get('recommended_reorder_kg', 0):.0f} kg"
                    )
                    st.badge(priority.upper(), icon=f":material/{icon}:", color=color)
                    st.markdown(line)
            else:
                st.caption(f"{material_symbol('info')} No restock recommendations yet.")

    # ------------------------------------------------------------------
    # Demand forecasting
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('trending_up')} 7-day demand forecast")

    if forecasts:
        fig_fc = _forecast_chart(forecasts)
        if fig_fc:
            st.plotly_chart(fig_fc, theme=None)

        n_cols = min(len(forecasts), 5)
        trend_cols = st.columns(n_cols)
        for i, fc in enumerate(forecasts[:n_cols]):
            with trend_cols[i]:
                slope = fc.get("trend_slope", 0)
                if slope > 0.1:
                    icon = MATERIAL_SYMBOLS["trending_up"]
                    label = "Rising"
                elif slope < -0.1:
                    icon = MATERIAL_SYMBOLS["trending_down"]
                    label = "Falling"
                else:
                    icon = MATERIAL_SYMBOLS["arrow_upward"]
                    label = "Stable"
                st.metric(
                    label=fc.get("product_name", "Unknown")[:22],
                    value=f"{slope:+.2f} kg/day",
                    delta=f"{icon} {label}",
                    border=True,
                )
    else:
        st.caption(
            f"{material_symbol('info')} "
            "Not enough order history for demand forecasts. "
            "As orders accumulate, predictions will appear here."
        )

    # ------------------------------------------------------------------
    # Buyer sentiment analysis
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('star')} Buyer sentiment analysis")

    sent_counts = sentiment.get("counts", {
        "POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0
    })
    avg_sent = sentiment.get("average_score", 0)

    c_sent, c_gauge = st.columns([1, 1])

    with c_sent:
        if sum(sent_counts.values()) > 0:
            fig_pie = chart_sentiment_pie(sent_counts)
            if fig_pie:
                st.plotly_chart(fig_pie, theme=None)
        else:
            st.caption(f"{material_symbol('info')} No reviews yet.")

    with c_gauge:
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=avg_sent * 100,
                title={
                    "text": "Overall sentiment",
                    "font": {"size": 14},
                },
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
        fig_gauge.update_layout(height=240, template="plotly_dark",
                               paper_bgcolor="rgba(0,0,0,0)",
                               font=dict(color="#E8F1EC"))
        st.plotly_chart(fig_gauge, theme=None)

    sent_icon = {
        "POSITIVE": "thumb_up",
        "NEUTRAL": "mood",
        "NEGATIVE": "thumb_down",
    }
    sent_color = {"POSITIVE": "green", "NEUTRAL": "yellow", "NEGATIVE": "red"}
    with st.container(horizontal=True):
        st.metric(f"{material_symbol('schedule')} Total reviews",
                  value=sentiment.get("total_reviews", 0), border=True)
        st.metric(f"{material_symbol('star')} Average score",
                  value=f"{avg_sent:+.3f}", border=True)
        for label, count in sent_counts.items():
            if count > 0:
                st.metric(
                    label=f"{material_symbol(sent_icon[label])} {label.title()}",
                    value=count,
                    border=True,
                )

    # ------------------------------------------------------------------
    # Inventory status
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('inventory_2')} Inventory status")

    inventory_data = _load_inventory(seller_id)

    if inventory_data:
        rows: List[Dict[str, Any]] = []
        for prod in inventory_data:
            stock_kg = prod.get("stock_kg") or 0
            if stock_kg <= 0:
                status_text = "Out of stock"
            elif stock_kg < 20:
                status_text = "Critical"
            elif stock_kg < 50:
                status_text = "Low"
            elif stock_kg > 500:
                status_text = "Overstock"
            else:
                status_text = "Good"

            advice = prod.get("restock") or {}
            restock_text = (
                f"{advice.get('recommended_reorder_kg', 0):.0f} kg reorder"
                if advice.get("needs_restock")
                else "—"
            )

            rows.append({
                "Product": prod.get("name", "Unknown"),
                "Category": prod.get("category", "N/A"),
                "Stock (kg)": f"{stock_kg:.1f}",
                "Price (₹/kg)": f"₹{prod.get('price', 0):.2f}",
                "Live boost": (
                    f"{prod.get('live_price_boost_pct', 0):+.1f}%"
                    if prod.get("live_engagement") is not None
                    else "—"
                ),
                "Restock (kg)": restock_text,
                "Rating": f"{prod.get('rating', 0):.2f}",
                "Status": status_text,
            })

        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            column_config={
                "Stock (kg)": st.column_config.NumberColumn("Stock (kg)", format="%.1f"),
                "Price (₹/kg)": st.column_config.NumberColumn("Price (₹/kg)", format="₹%.2f"),
            },
        )

        if low_stock:
            st.space("small")
            with st.container(border=True):
                for alert in low_stock:
                    product_name = alert.get("product_name", "Unknown Product")
                    stock_kg = alert.get("stock_kg", 0)
                    st.badge("LOW STOCK", icon=":material/warning:", color="orange")
                    st.markdown(
                        f"{material_symbol('warning')} "
                        f"**{product_name}** — only {stock_kg:.1f} kg remaining."
                    )
    else:
        st.caption(
            f"{material_symbol('info')} "
            "No products listed yet. Add products from the Seller Studio."
        )

    # ------------------------------------------------------------------
    # Revenue trend chart
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('trending_up')} Revenue trend (last 7 days)")

    daily_data = analytics.get("daily_revenue", [])
    fig_rev = _revenue_chart(daily_data)
    if fig_rev:
        st.plotly_chart(fig_rev, theme=None)
    else:
        st.caption(
            f"{material_symbol('info')} "
            "Revenue tracking requires historical order data. "
            "As orders accumulate over time, this section will show "
            "daily/weekly revenue trends with a bar chart."
        )

    # ------------------------------------------------------------------
    # Dynamic pricing recommendations
    # ------------------------------------------------------------------
    st.space("small")
    st.subheader(f"{material_symbol('trending_up')} Dynamic pricing recommendations")
    st.caption(
        f"{material_symbol('info')} "
        "Recommended prices based on demand, stock levels, and live engagement."
    )

    if inventory_data:
        pricing_cols = st.columns(min(len(inventory_data), 4))
        for i, prod in enumerate(inventory_data[:4]):
            with pricing_cols[i]:
                st.metric(
                    label=(prod.get("name") or "Unknown")[:22],
                    value=f"₹{prod.get('price', 0):.2f}/kg",
                    delta=(
                        f"{prod.get('live_price_boost_pct', 0):+.1f}% live"
                        if prod.get("live_engagement") is not None
                        else f"{prod.get('price_change_pct', 0):+.1f}% dynamic"
                    ),
                    border=True,
                )
    else:
        st.caption(f"{material_symbol('info')} No products to recommend prices for yet.")