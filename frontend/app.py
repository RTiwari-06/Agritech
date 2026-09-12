"""Agricultural Marketplace Streamlit Application.

Multi-page interactive dashboard with:
- Tab 1: Buyer Marketplace & Live Shopping
- Tab 2: Seller Live Host Studio
- Tab 3: Sales Analytics & Optimization Dashboard

Run from project root:
    streamlit run frontend/app.py
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config

API = Config.API_BASE_URL.rstrip("/")

# Page identifiers
TAB_MARKETPLACE = "🌾 Buyer Marketplace & Live Shopping"
TAB_SELLER_STUDIO = "📹 Seller Live Host Studio"
TAB_ANALYTICS = "📊 Sales Analytics & Optimization Dashboard"

# Sentiment emoji mapping
SENTIMENT_EMOJI = {"POSITIVE": "🟢", "NEUTRAL": "🟡", "NEGATIVE": "🔴"}
INTENT_EMOJI = {
    "PRICE_INQUIRY": "💰",
    "QUALITY_INQUIRY": "🌿",
    "DELIVERY_INQUIRY": "🚚",
    "GENERAL_CHAT": "💬"
}


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


# --- Rendering Helpers ------------------------------------------------------
def render_product_card(product: Dict, show_buy: bool = True, buyer_id: Optional[int] = None) -> None:
    """Render a product card with dynamic price, stock, rating, and buy option."""
    seller = product.get("seller") or {}
    seller_name = seller.get("username", "Seller")
    seller_location = seller.get("location", "")

    dynamic_price = product.get("dynamic_price", product.get("price", 0))
    price_change = product.get("price_change_pct", 0)

    with st.container():
        col_img, col_info, col_action = st.columns([1, 3, 1.5])

        with col_img:
            # Product icon based on category
            category_icons = {
                "Grain": "🌾", "Vegetable": "🥬", "Fruit": "🍎", "Organic": "🌱"
            }
            icon = category_icons.get(product.get("category", ""), "📦")
            st.markdown(f"<div style='font-size: 48px; text-align: center;'>{icon}</div>", unsafe_allow_html=True)

        with col_info:
            st.markdown(f"### {product['name']}")
            st.caption(f"🏪 {seller_name} · 📍 {seller_location} · 🏷️ {product.get('category', 'N/A')}")

            # Price display with dynamic pricing
            price_col1, price_col2 = st.columns(2)
            with price_col1:
                st.metric(
                    "Price",
                    f"₹{dynamic_price:.2f}/kg",
                    delta=f"{price_change:+.1%}" if price_change else None,
                    delta_color="inverse" if price_change and price_change < 0 else "normal"
                )
            with price_col2:
                st.metric("Stock", f"{product.get('stock_kg', 0):.1f} kg")

            st.caption(f"⭐ Rating: {product.get('rating', 0):.1f}/5.0")
            st.write(product.get("description", "")[:200] + ("..." if len(product.get("description", "")) > 200 else ""))

        with col_action:
            if show_buy and buyer_id:
                with st.form(f"buy_{product['id']}", border=True):
                    st.write("**Place Order**")
                    max_qty = max(float(product.get("stock_kg", 1)), 0.5)
                    quantity = st.number_input(
                        "Quantity (kg)",
                        min_value=0.5,
                        max_value=max_qty,
                        value=min(1.0, max_qty),
                        step=0.5,
                        key=f"qty_{product['id']}"
                    )
                    submitted = st.form_submit_button("🛒 Buy Now", type="primary", use_container_width=True)

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
    """Render a chat message with sentiment and intent badges."""
    sentiment = msg.get("sentiment_label", "NEUTRAL")
    intent = msg.get("intent_tag", "GENERAL_CHAT")
    username = msg.get("username", "User")
    text = msg.get("message_text", "")
    timestamp = msg.get("timestamp", "")

    sent_emoji = SENTIMENT_EMOJI.get(sentiment, "🟡")
    intent_emoji = INTENT_EMOJI.get(intent, "💬")

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S")
    except Exception:
        time_str = ""

    with st.container():
        cols = st.columns([0.5, 4, 1, 1])
        cols[0].markdown(f"<div style='text-align: center; padding-top: 8px;'>{sent_emoji}</div>", unsafe_allow_html=True)
        cols[1].markdown(f"**{username}** · {time_str}")
        cols[1].write(text)
        cols[2].markdown(f"<div style='text-align: center; padding-top: 8px;'>{intent_emoji} {intent}</div>", unsafe_allow_html=True)
        cols[3].markdown(f"<div style='text-align: center; padding-top: 8px;'>{sent_emoji} {sentiment}</div>", unsafe_allow_html=True)


def sentiment_badge(sentiment: str) -> str:
    """Return HTML for sentiment badge."""
    colors = {"POSITIVE": "#28a745", "NEUTRAL": "#ffc107", "NEGATIVE": "#dc3545"}
    color = colors.get(sentiment, "#6c757d")
    return f'<span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;">{sentiment}</span>'


def intent_badge(intent: str) -> str:
    """Return HTML for intent badge."""
    colors = {
        "PRICE_INQUIRY": "#007bff",
        "QUALITY_INQUIRY": "#28a745",
        "DELIVERY_INQUIRY": "#fd7e14",
        "GENERAL_CHAT": "#6c757d"
    }
    color = colors.get(intent, "#6c757d")
    return f'<span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;">{intent}</span>'


# --- Tab 1: Buyer Marketplace & Live Shopping -------------------------------
def tab_marketplace():
    st.header("🌾 Buyer Marketplace & Live Shopping")

    # Buyer selection (in real app, this would be auth)
    buyer_id = st.sidebar.number_input("Your Buyer ID", min_value=1, value=1, step=1)

    # Search and filter
    col_search, col_cat, col_sort = st.columns([3, 1, 1])
    with col_search:
        search_query = st.text_input("🔍 Search products", placeholder="e.g., organic tomatoes, basmati rice...")
    with col_cat:
        category = st.selectbox("Category", ["All", "Grain", "Vegetable", "Fruit", "Organic"])
    with col_sort:
        sort_by = st.selectbox("Sort by", ["Relevance", "Price: Low to High", "Price: High to Low", "Rating", "Newest"])

    # Fetch products
    params = {"limit": 50, "dynamic_price": "true"}
    if search_query:
        params["q"] = search_query
    if category != "All":
        params["category"] = category

    products_response = safe_call(lambda: api_get("/api/products", params))
    products = products_response.get("data", []) if products_response and products_response.get("ok") else []

    # Sort
    if sort_by == "Price: Low to High":
        products.sort(key=lambda p: p.get("dynamic_price", p.get("price", 0)))
    elif sort_by == "Price: High to Low":
        products.sort(key=lambda p: p.get("dynamic_price", p.get("price", 0)), reverse=True)
    elif sort_by == "Rating":
        products.sort(key=lambda p: p.get("rating", 0), reverse=True)
    elif sort_by == "Newest":
        products.sort(key=lambda p: p.get("created_at", ""), reverse=True)

    st.divider()

    # Live Stream Section
    st.subheader("📺 Live Streams")
    streams_response = safe_call(lambda: api_get("/api/streams", {"active": "true"}))
    active_streams = streams_response.get("data", []) if streams_response and streams_response.get("ok") else []

    if active_streams:
        stream_tabs = st.tabs([f"🎥 {s['stream_title'][:30]}" for s in active_streams])
        for i, stream in enumerate(active_streams):
            with stream_tabs[i]:
                render_live_stream_view(stream, buyer_id)
    else:
        st.info("No active live streams at the moment.")

    st.divider()

    # Product Catalog
    st.subheader("🛍️ Product Catalog")
    if not products:
        st.info("No products found matching your criteria.")
    else:
        # Grid layout
        cols_per_row = 2
        for i in range(0, len(products), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                if i + j < len(products):
                    with col:
                        render_product_card(products[i + j], show_buy=True, buyer_id=buyer_id)

    st.divider()

    # Recommendations
    st.subheader("🎯 Recommended For You")
    recs_response = safe_call(lambda: api_get(f"/api/recommendations/{buyer_id}", {"top_n": 5}))
    recommendations = recs_response.get("data", []) if recs_response and recs_response.get("ok") else []

    if recommendations:
        rec_cols = st.columns(min(len(recommendations), 5))
        for i, rec in enumerate(recommendations[:5]):
            with rec_cols[i]:
                with st.container():
                    st.markdown(f"**{rec['name']}**")
                    st.caption(f"₹{rec.get('dynamic_price', rec.get('price', 0)):.2f}/kg · ⭐ {rec.get('rating', 0):.1f}")
                    st.progress(min(rec.get("recommendation_score", 0), 1.0))
                    st.caption(f"Match: {rec.get('recommendation_score', 0):.0%}")
    else:
        st.caption("Browse products to get personalized recommendations!")


def render_live_stream_view(stream: Dict, buyer_id: int):
    """Render live stream view with chat."""
    stream_id = stream["id"]

    # Video placeholder
    st.markdown(
        f"""
        <div style="background: #1a1a1a; border-radius: 8px; height: 300px; display: flex; align-items: center; justify-content: center; color: #888; margin-bottom: 16px;">
            📹 Live Stream: {stream['stream_title']}<br>
            <small>Seller: {stream.get('seller', {}).get('username', 'Unknown')}</small>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Chat area
    chat_container = st.container()
    with chat_container:
        # Auto-refresh for live chat
        st_autorefresh(interval=3000, key=f"chat_refresh_{stream_id}")

        messages_response = safe_call(lambda: api_get(f"/api/streams/{stream_id}/messages", {"limit": 50}))
        messages = messages_response.get("data", []) if messages_response and messages_response.get("ok") else []

        if messages:
            for msg in messages[-20:]:  # Show last 20
                render_chat_message(msg)
        else:
            st.caption("No messages yet. Be the first to chat!")

    # Chat input
    with st.form(f"chat_form_{stream_id}", clear_on_submit=True):
        col_input, col_send = st.columns([5, 1])
        message_text = col_input.text_input("Message", placeholder="Type your message...", label_visibility="collapsed")
        send_clicked = col_send.form_submit_button("Send", use_container_width=True)

        if send_clicked and message_text.strip():
            result = safe_call(
                lambda: api_post(
                    "/api/streams/send_message",
                    {
                        "stream_id": stream_id,
                        "user_id": buyer_id,
                        "message_text": message_text.strip(),
                    },
                )
            )
            if result and result.get("ok"):
                st.rerun()


# --- Tab 2: Seller Live Host Studio -----------------------------------------
def tab_seller_studio():
    st.header("📹 Seller Live Host Studio")

    # Seller selection
    seller_id = st.sidebar.number_input("Your Seller ID", min_value=1, value=1, step=1)

    with session_scope() as session:
        seller = session.get(User, seller_id)
        if not seller or seller.role != UserRole.SELLER:
            st.error("Invalid seller ID. Please select a valid seller account.")
            return

    st.subheader("🎬 Stream Control Panel")

    col_stream1, col_stream2 = st.columns(2)

    with col_stream1:
        st.write("**Start New Live Stream**")
        stream_title = st.text_input("Stream Title", placeholder="e.g., Fresh Morning Harvest from Nashik")
        if st.button("🔴 Go Live", type="primary", use_container_width=True):
            if stream_title.strip():
                result = safe_call(
                    lambda: api_post("/api/streams", {"seller_id": seller_id, "stream_title": stream_title.strip()})
                )
                if result and result.get("ok"):
                    st.success(f"Stream started: {result['data']['stream_title']}")
                    st.rerun()
            else:
                st.warning("Please enter a stream title")

    with col_stream2:
        st.write("**Active Streams**")
        streams_response = safe_call(lambda: api_get("/api/streams", {"seller_id": seller_id, "active": "true"}))
        active_streams = streams_response.get("data", []) if streams_response and streams_response.get("ok") else []

        if active_streams:
            for stream in active_streams:
                with st.container():
                    st.write(f"🔴 **{stream['stream_title']}**")
                    if st.button("⏹️ End Stream", key=f"end_{stream['id']}"):
                        result = safe_call(lambda: api_post(f"/api/streams/{stream['id']}/end", {}))
                        if result and result.get("ok"):
                            st.success("Stream ended")
                            st.rerun()
        else:
            st.info("No active streams")

    st.divider()

    # Live Chat Monitor
    st.subheader("💬 Live Chat & Sentiment Monitor")

    all_streams_response = safe_call(lambda: api_get("/api/streams", {"seller_id": seller_id}))
    all_streams = all_streams_response.get("data", []) if all_streams_response and all_streams_response.get("ok") else []

    if all_streams:
        selected_stream = st.selectbox(
            "Select Stream to Monitor",
            options=all_streams,
            format_func=lambda s: f"{'🔴' if s['is_active'] else '⚪'} {s['stream_title']}"
        )

        if selected_stream:
            stream_id = selected_stream["id"]
            st_autorefresh(interval=3000, key=f"seller_chat_refresh_{stream_id}")

            # Get messages
            messages_response = safe_call(lambda: api_get(f"/api/streams/{stream_id}/messages", {"limit": 100}))
            messages = messages_response.get("data", []) if messages_response and messages_response.get("ok") else []

            # Sentiment distribution
            col_sent1, col_sent2, col_sent3 = st.columns(3)
            pos_count = sum(1 for m in messages if m.get("sentiment_label") == "POSITIVE")
            neu_count = sum(1 for m in messages if m.get("sentiment_label") == "NEUTRAL")
            neg_count = sum(1 for m in messages if m.get("sentiment_label") == "NEGATIVE")
            total = len(messages)

            col_sent1.metric("🟢 Positive", pos_count, f"{pos_count/total*100:.0f}%" if total else "0%")
            col_sent2.metric("🟡 Neutral", neu_count, f"{neu_count/total*100:.0f}%" if total else "0%")
            col_sent3.metric("🔴 Negative", neg_count, f"{neg_count/total*100:.0f}%" if total else "0%")

            # Sentiment gauge
            if total > 0:
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=(pos_count - neg_count) / total * 100,
                    title={'text': "Sentiment Score"},
                    gauge={
                        'axis': {'range': [-100, 100]},
                        'bar': {'color': "darkblue"},
                        'steps': [
                            {'range': [-100, -30], 'color': "lightcoral"},
                            {'range': [-30, 30], 'color': "lightyellow"},
                            {'range': [30, 100], 'color': "lightgreen"}
                        ],
                    }
                ))
                fig.update_layout(height=200)
                st.plotly_chart(fig, use_container_width=True)

            # Chat feed
            st.write("**Recent Messages**")
            for msg in messages[-15:]:
                render_chat_message(msg)

            # Product Highlight
            st.divider()
            st.write("**🚀 Quick Product Highlight**")
            products_response = safe_call(lambda: api_get("/api/products", {"limit": 100}))
            seller_products = [
                p for p in (products_response.get("data", []) if products_response else [])
                if p.get("seller", {}).get("id") == seller_id
            ]

            if seller_products:
                highlight_product = st.selectbox(
                    "Select product to highlight in stream",
                    options=seller_products,
                    format_func=lambda p: f"{p['name']} - ₹{p.get('dynamic_price', p.get('price', 0)):.2f}/kg"
                )
                if st.button("✨ Highlight Product", type="primary"):
                    st.success(f"Highlighted **{highlight_product['name']}** to live viewers!")
                    # In real implementation, this would send a WebSocket event to all viewers
            else:
                st.info("No products listed yet.")
    else:
        st.info("No streams found. Start a new stream above.")


# --- Tab 3: Sales Analytics & Optimization Dashboard ------------------------
def tab_analytics():
    st.header("📊 Sales Analytics & Optimization Dashboard")

    # Seller selection
    seller_id = st.sidebar.number_input("Seller ID for Analytics", min_value=1, value=1, step=1)

    analytics_response = safe_call(lambda: api_get(f"/api/analytics/{seller_id}"))
    analytics = analytics_response.get("data", {}) if analytics_response and analytics_response.get("ok") else {}

    if not analytics:
        st.warning("No analytics data available. Please check seller ID or start selling!")
        return

    seller = analytics.get("seller", {})
    sales = analytics.get("sales_summary", {})
    sentiment = analytics.get("sentiment_summary", {})
    forecasts = analytics.get("demand_forecasts", [])
    low_stock = analytics.get("low_stock_alerts", [])

    # Key Metrics Row
    st.subheader("📈 Key Performance Metrics")
    metric_cols = st.columns(4)
    metric_cols[0].metric("💰 Total Revenue", f"₹{sales.get('total_revenue', 0):,.2f}")
    metric_cols[1].metric("📦 Total Quantity", f"{sales.get('total_quantity_kg', 0):,.1f} kg")
    metric_cols[2].metric("🧾 Total Orders", sales.get('total_orders', 0))
    metric_cols[3].metric("💵 Avg Order Value", f"₹{sales.get('avg_order_value', 0):,.2f}")

    st.divider()

    # Demand Forecasting Charts
    st.subheader("🔮 Demand Forecasting (Next 7 Days)")

    if forecasts:
        # Create combined forecast chart
        forecast_data = []
        for fc in forecasts:
            for pred in fc.get("predictions", []):
                forecast_data.append({
                    "Product": fc["product_name"],
                    "Date": pred["date"],
                    "Predicted Quantity (kg)": pred["predicted_quantity"],
                    "Trend": "📈 Rising" if fc["trend_slope"] > 0.1 else ("📉 Falling" if fc["trend_slope"] < -0.1 else "➡️ Stable")
                })

        if forecast_data:
            df_forecast = pd.DataFrame(forecast_data)
            df_forecast["Date"] = pd.to_datetime(df_forecast["Date"])

            fig = px.line(
                df_forecast,
                x="Date",
                y="Predicted Quantity (kg)",
                color="Product",
                markers=True,
                title="7-Day Demand Forecast by Product",
                labels={"Predicted Quantity (kg)": "Quantity (kg)", "Date": "Date"}
            )
            fig.update_layout(hovermode="x unified", height=400)
            st.plotly_chart(fig, use_container_width=True)

            # Trend indicators
            st.write("**Trend Summary**")
            trend_cols = st.columns(min(len(forecasts), 5))
            for i, fc in enumerate(forecasts[:5]):
                with trend_cols[i]:
                    slope = fc["trend_slope"]
                    trend_emoji = "📈" if slope > 0.1 else ("📉" if slope < -0.1 else "➡️")
                    st.metric(fc["product_name"][:20], f"{slope:+.2f} kg/day", trend_emoji)

    st.divider()

    # Sentiment Analysis Pie Chart
    st.subheader("😊 Buyer Sentiment Analysis")
    sent_col1, sent_col2 = st.columns([1, 1])

    with sent_col1:
        sent_counts = sentiment.get("counts", {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0})
        if sum(sent_counts.values()) > 0:
            fig_pie = px.pie(
                values=list(sent_counts.values()),
                names=list(sent_counts.keys()),
                title="Review Sentiment Distribution",
                color_discrete_map={"POSITIVE": "#28a745", "NEUTRAL": "#ffc107", "NEGATIVE": "#dc3545"},
                hole=0.4
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No reviews yet")

    with sent_col2:
        st.metric("Average Sentiment Score", f"{sentiment.get('average_score', 0):.3f}")
        st.metric("Total Reviews", sentiment.get("total_reviews", 0))

        # Sentiment gauge
        avg_sent = sentiment.get("average_score", 0)
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=avg_sent * 100,
            title={'text': "Overall Sentiment"},
            gauge={
                'axis': {'range': [-100, 100]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [-100, -30], 'color': "lightcoral"},
                    {'range': [-30, 30], 'color': "lightyellow"},
                    {'range': [30, 100], 'color': "lightgreen"}
                ],
            }
        ))
        fig_gauge.update_layout(height=250)
        st.plotly_chart(fig_gauge, use_container_width=True)

    st.divider()

    # Inventory Status Table with Alerts
    st.subheader("📦 Inventory Status")

    with session_scope() as session:
        products = session.query(Product).filter(Product.seller_id == seller_id).all()
        inventory_data = []
        for p in products:
            # Get dynamic pricing
            pricing_response = safe_call(lambda: api_get(f"/api/pricing/{p.id}"))
            pricing = pricing_response.get("data", {}) if pricing_response and pricing_response.get("ok") else {}

            status = "🟢 Good"
            if p.stock_kg <= 0:
                status = "🔴 Out of Stock"
            elif p.stock_kg < 20:
                status = "🔴 Critical"
            elif p.stock_kg < 50:
                status = "🟠 Low"
            elif p.stock_kg > 500:
                status = "🔵 Overstock"

            inventory_data.append({
                "Product": p.name,
                "Category": p.category.value if p.category else "N/A",
                "Current Stock (kg)": f"{p.stock_kg:.1f}",
                "Base Price": f"₹{p.price:.2f}",
                "Dynamic Price": f"₹{pricing.get('optimal_price', p.price):.2f}",
                "Price Change": f"{pricing.get('change_pct', 0):+.1%}",
                "Rating": f"{p.rating:.1f} ⭐",
                "Status": status
            })

    if inventory_data:
        df_inv = pd.DataFrame(inventory_data)
        st.dataframe(
            df_inv,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn("Status", width="medium"),
            }
        )

        # Low stock alerts
        if low_stock:
            st.error("⚠️ **Low Stock Alerts**")
            for alert in low_stock:
                st.write(f"🔴 **{alert['product_name']}**: Only {alert['stock_kg']:.1f} kg remaining!")
    else:
        st.info("No products listed yet.")

    st.divider()

    # Revenue Trend (placeholder - would need historical data)
    st.subheader("💰 Revenue Trend")
    st.caption("Revenue tracking requires historical order data. As orders accumulate, this chart will populate.")

    # Dynamic Pricing Recommendations
    st.subheader("⚡ Dynamic Pricing Recommendations")
    pricing_cols = st.columns(min(len(inventory_data), 4))
    for i, inv in enumerate(inventory_data[:4]):
        with pricing_cols[i]:
            change = float(inv["Price Change"].replace("%", "").replace("+", ""))
            st.metric(
                inv["Product"][:20],
                inv["Dynamic Price"],
                delta=f"{change:+.1f}%",
                delta_color="inverse" if change < 0 else "normal"
            )


# Import for analytics tab
from database.db import session_scope
from database.models import User, UserRole, Product


# --- Main App ---------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="Agritech Marketplace",
        page_icon="🌾",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS
    st.markdown("""
    <style>
    .stMetric { background-color: #f8f9fa; padding: 10px; border-radius: 8px; }
    .stForm { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; }
    div[data-testid="stFormSubmitButton"] button { width: 100%; }
    </style>
    """, unsafe_allow_html=True)

    st.title("🌾 Agricultural Sales Computational Optimization Platform")

    # Sidebar
    with st.sidebar:
        st.image("https://via.placeholder.com/200x80/2E7D32/FFFFFF?text=Agritech", use_container_width=True)
        st.markdown("---")
        st.caption(f"API: {API}")
        st.caption(f"Database: {Config.effective_database_uri()}")

    # Navigation tabs
    tab1, tab2, tab3 = st.tabs([TAB_MARKETPLACE, TAB_SELLER_STUDIO, TAB_ANALYTICS])

    with tab1:
        tab_marketplace()

    with tab2:
        tab_seller_studio()

    with tab3:
        tab_analytics()


if __name__ == "__main__":
    main()