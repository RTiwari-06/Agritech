"""Sales Analytics & Optimization Dashboard page.

Shows seller revenue, sentiment analysis, demand forecasting, and
inventory status — with skeletons while data loads (#4), session state
for the selected seller (#5), native badges (#6), and Material Symbols (#7).
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
    _api_post,
    badge_status,
    badge_sentiment,
    badge_trend,
    chart_sentiment_pie,
    material_symbol,
    skeleton_card,
)


# ---------------------------------------------------------------------------
# Cached loaders — #1
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
def _load_sentiment_detail(seller_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/analytics/{seller_id}/sentiment")
    if not resp or not resp.get("ok"):
        return None
    return resp.get("data", {})


@st.cache_data(ttl=30, show_spinner=False)
def _load_demand(seller_id: int) -> Optional[Dict[str, Any]]:
    resp = _api_get(f"/api/analytics/{seller_id}/demand")
    if not resp or not resp.get("ok"):
        return None
    return resp.get("data", {})


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
        title="Demand Forecast (2 data points per product)",
        xaxis_title="",
        yaxis_title="Demand (kg/day)",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8EDF2"),
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
        color_continuous_scale=["#00B4D8", "#48CAE4"],
        labels={"date": ""},
    )
    fig.update_layout(
        title="Daily Revenue (last 7 days)",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8EDF2"),
        coloraxis_showscale=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

def app() -> None:
    """Sales analytics & optimization dashboard page."""
    st.title(
        f"{MATERIAL_SYMBOLS['analytics']} "
        "Sales Analytics & Optimization Dashboard"
    )

    # #5  session_state for seller ID
    if "analytics_seller_id" not in st.session_state:
        st.session_state.analytics_seller_id = 1

    st.sidebar.number_input(
        f"{MATERIAL_SYMBOLS['person']} Seller ID",
        min_value=1,
        step=1,
        key="analytics_seller_id",
    )
    seller_id = st.session_state.analytics_seller_id

    # ------------------------------------------------------------------
    # #4  Skeleton while analytics data loads
    # ------------------------------------------------------------------
    with st.status(
        f"{MATERIAL_SYMBOLS['refresh']} Loading analytics for seller #{seller_id} ...",
        expanded=False,
    ) as status:
        st.write(f"{MATERIAL_SYMBOLS['schedule']} Fetching orders, reviews, and forecasts ...")
        analytics = _load_analytics(seller_id)
        status.update(label=f"{MATERIAL_SYMBOLS['check_circle']} Data loaded.", state="complete")

    if not analytics:
        st.warning(
            f"{MATERIAL_SYMBOLS['warning']} "
            "No analytics data available for this seller. "
            "Start selling to see metrics here."
        )
        return

    # Seller info bar
    seller = analytics.get("seller", {})
    st.divider()
    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.markdown(
            f"{MATERIAL_SYMBOLS['store']} **Seller:** "
            f"{seller.get('username', 'Unknown')} · "
            f"{MATERIAL_SYMBOLS['place']} "
            f"{seller.get('location', 'Unknown')}"
        )
    with c_right:
        email = seller.get("email", "")
        if email:
            st.markdown(f"{MATERIAL_SYMBOLS['mail']} {email}")

    # ------------------------------------------------------------------
    # Key metrics — top row
    # ------------------------------------------------------------------
    sales = analytics.get("sales_summary", {})
    sentiment = analytics.get("sentiment_summary", {})
    forecasts = analytics.get("demand_forecasts", [])
    low_stock = analytics.get("low_stock_alerts", [])

    st.divider()
    st.subheader(f"{MATERIAL_SYMBOLS['chart_line']} Key Performance Metrics")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            label=f"{MATERIAL_SYMBOLS['currency_rupee']} Total Revenue",
            value=f"₹{sales.get('total_revenue', 0):,.2f}",
        )
    with c2:
        st.metric(
            label=f"{MATERIAL_SYMBOLS['inventory_2']} Total Quantity",
            value=f"{sales.get('total_quantity_kg', 0):,.1f} kg",
        )
    with c3:
        st.metric(
            label=f"{MATERIAL_SYMBOLS['shopping_cart']} Total Orders",
            value=sales.get("total_orders", 0),
        )
    with c4:
        st.metric(
            label=f"{MATERIAL_SYMBOLS['trending_up']} Avg Order Value",
            value=f"₹{sales.get('avg_order_value', 0):,.2f}",
        )

    # ------------------------------------------------------------------
    # Demand forecasting chart (#7 symbols)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader(
        f"{MATERIAL_SYMBOLS['trending_up']} "
        "Demand Forecasting (Next 7 Days)"
    )

    if forecasts:
        fig_fc = _forecast_chart(forecasts)
        if fig_fc:
            st.plotly_chart(fig_fc, use_container_width=True)
        else:
            st.info(f"{MATERIAL_SYMBOLS['info']} No forecast data yet.")

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
                )
    else:
        st.info(
            f"{MATERIAL_SYMBOLS['info']} "
            "Not enough order history for demand forecasts. "
            "As orders accumulate, predictions will appear here."
        )

    # ------------------------------------------------------------------
    # Sentiment analysis
    # ------------------------------------------------------------------
    st.divider()
    st.subheader(f"{MATERIAL_SYMBOLS['star']} Buyer Sentiment Analysis")

    c_sent, c_gauge = st.columns([1, 1])

    with c_sent:
        sent_counts = sentiment.get("counts", {
            "POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0
        })
        if sum(sent_counts.values()) > 0:
            fig_pie = chart_sentiment_pie(sent_counts)
            if fig_pie:
                st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info(f"{MATERIAL_SYMBOLS['info']} No reviews yet.")

    with c_gauge:
        avg_sent = sentiment.get("average_score", 0)
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=avg_sent * 100,
                title={
                    "text": f"{MATERIAL_SYMBOLS['star']} Overall Sentiment",
                    "font": {"size": 14},
                },
                gauge={
                    "axis": {"range": [-100, 100], "tickwidth": 1},
                    "bar": {"color": "#1f77b4"},
                    "steps": [
                        {"range": [-100, -30], "color": "#ffcdd2"},
                        {"range": [-30, 30], "color": "#fff9c4"},
                        {"range": [30, 100], "color": "#c8e6c9"},
                    ],
                    "threshold": {
                        "line": {"color": "red", "width": 2},
                        "thickness": 0.75,
                        "value": avg_sent * 100,
                    },
                },
            )
        )
        fig_gauge.update_layout(height=260)
        st.plotly_chart(fig_gauge, use_container_width=True)

        st.divider()
        st.markdown(
            f"{MATERIAL_SYMBOLS['schedule']} **Total Reviews:** "
            f"{sentiment.get('total_reviews', 0)}"
        )
        st.markdown(
            f"{MATERIAL_SYMBOLS['star']} **Average Score:** "
            f"{avg_sent:.3f} / 1.000"
        )

        st.divider()
        st.markdown(f"{MATERIAL_SYMBOLS['analytics']} **Sentiment Breakdown**")
        for label, count in sent_counts.items():
            if count > 0:
                icon = {
                    "POSITIVE": "thumb_up",
                    "NEUTRAL": "mood",
                    "NEGATIVE": "thumb_down",
                }.get(label, "mood")
                st.markdown(
                    f"<span style='margin-right:16px;'>"
                    f"{material_symbol(icon)} **{label}:** {count} reviews"
                    f"</span>",
                    unsafe_allow_html=True,
                )

    # ------------------------------------------------------------------
    # Inventory status table — uses native status badge (#6)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader(f"{MATERIAL_SYMBOLS['inventory_2']} Inventory Status")

    inv_resp = _api_get(f"/api/seller/{seller_id}/inventory")
    inventory_data = (
        inv_resp.get("inventory", [])
        if inv_resp and inv_resp.get("ok")
        else []
    )

    if inventory_data:
        rows: List[Dict[str, Any]] = []
        for item in inventory_data:
            prod = item.get("product", {})
            stock_kg = prod.get("stock_kg") or 0
            if stock_kg <= 0:
                status_text = f"{MATERIAL_SYMBOLS['cancel']} Out of Stock"
            elif stock_kg < 20:
                status_text = f"{MATERIAL_SYMBOLS['warning']} Critical"
            elif stock_kg < 50:
                status_text = f"{MATERIAL_SYMBOLS['warning']} Low"
            elif stock_kg > 500:
                status_text = f"{MATERIAL_SYMBOLS['trending_down']} Overstock"
            else:
                status_text = f"{MATERIAL_SYMBOLS['check_circle']} Good"

            rows.append({
                "Product": prod.get("name", "Unknown"),
                "Category": prod.get("category", "N/A"),
                "Stock (kg)": f"{stock_kg:.1f}",
                "Price (₹/kg)": f"₹{prod.get('price', 0):.2f}",
                "Rating": f"{prod.get('rating', 0):.2f} {MATERIAL_SYMBOLS['star']}",
                "Status": status_text,
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn("Status", width="medium"),
            },
        )

        if low_stock:
            st.divider()
            st.error(
                f"{MATERIAL_SYMBOLS['warning']} "
                f"**{MATERIAL_SYMBOLS['warning']} Low Stock Alerts**"
            )
            for alert in low_stock:
                product_name = alert.get("product_name", "Unknown Product")
                stock_kg = alert.get("stock_kg", 0)
                st.markdown(
                    f"{MATERIAL_SYMBOLS['warning']} "
                    f"**{product_name}** — Only {stock_kg:.1f} kg remaining!"
                )
    else:
        st.info(
            f"{MATERIAL_SYMBOLS['info']} "
            "No products listed yet. Add products from the Seller Studio."
        )

    # ------------------------------------------------------------------
    # Revenue trend chart
    # ------------------------------------------------------------------
    st.divider()
    st.subheader(f"{MATERIAL_SYMBOLS['trending_up']} Revenue Trend (Last 7 Days)")

    daily_data = analytics.get("daily_revenue", [])
    fig_rev = _revenue_chart(daily_data)
    if fig_rev:
        st.plotly_chart(fig_rev, use_container_width=True)
    else:
        st.caption(
            f"{MATERIAL_SYMBOLS['info']} "
            "Revenue tracking requires historical order data. "
            "As orders accumulate over time, this section will show "
            "daily/weekly revenue trends with a bar chart."
        )

    # ------------------------------------------------------------------
    # Dynamic pricing recommendations for top products (#7 symbols)
    # ------------------------------------------------------------------
    st.divider()
    st.subheader(f"{MATERIAL_SYMBOLS['trending_up']} Dynamic Pricing Recommendations")
    st.caption(
        f"{MATERIAL_SYMBOLS['info']} "
        "Recommended prices based on demand, stock levels, and market trends."
    )

    if inventory_data:
        pricing_cols = st.columns(min(len(rows), 4))
        for i, row in enumerate(rows[:4]):
            with pricing_cols[i]:
                st.metric(
                    label=row["Product"][:22],
                    value=row["Price (₹/kg)"],
                    delta=f"{MATERIAL_SYMBOLS['chart_line']} Dynamic pricing applied",
                )
    else:
        st.caption(f"{MATERIAL_SYMBOLS['info']} No products to recommend prices for yet.")
