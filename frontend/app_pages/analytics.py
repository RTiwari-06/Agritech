"""Sales analytics & optimization dashboard page."""

import streamlit as st

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .api_helpers import load_analytics, load_inventory


def app():
    st.header("Sales analytics & optimization dashboard", icon=":material/analytics:")

    if "seller_id" not in st.session_state:
        st.session_state.seller_id = 1

    seller_id = st.sidebar.number_input(
        "Seller ID for analytics",
        min_value=1,
        value=st.session_state.seller_id,
        step=1,
        key="analytics_seller_id_input",
    )
    st.session_state.seller_id = seller_id

    analytics_data = load_analytics(seller_id)
    if not analytics_data:
        st.warning("No analytics data available. Please check seller ID or start selling!")
        return

    seller = analytics_data.get("seller", {})
    sales = analytics_data.get("sales_summary", {})
    sentiment = analytics_data.get("sentiment_summary", {})
    forecasts = analytics_data.get("demand_forecasts", [])
    low_stock_alerts = analytics_data.get("low_stock_alerts", [])

    # Key metrics row — with skeletons
    st.subheader("Key performance metrics", icon=":material/trending_up:")

    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)

    with kpi_col1:
        with st.container(border=True):
            st.subheader("Total revenue")
            with st.skeleton(height=60):
                st.metric("Total revenue", f"Rs.{sales.get('total_revenue', 0):,.2f}")

    with kpi_col2:
        with st.container(border=True):
            st.subheader("Total quantity")
            with st.skeleton(height=60):
                st.metric("Total quantity", f"{sales.get('total_quantity_kg', 0):,.1f} kg")

    with kpi_col3:
        with st.container(border=True):
            st.subheader("Total orders")
            with st.skeleton(height=60):
                st.metric("Total orders", sales.get("total_orders", 0))

    with kpi_col4:
        with st.container(border=True):
            st.subheader("Avg order value")
            with st.skeleton(height=60):
                st.metric("Avg order value", f"Rs.{sales.get('avg_order_value', 0):,.2f}")

    st.divider()

    # Demand forecasting — parallel fragments
    st.subheader("Demand forecasting (next 7 days)", icon=":material/calendar_month:")

    if forecasts:
        forecast_cols = st.columns(2)
        with forecast_cols[0]:
            _render_forecast_chart(forecasts)
        with forecast_cols[1]:
            _render_trend_summary(forecasts)

    st.divider()

    # Sentiment analysis
    st.subheader("Buyer sentiment analysis", icon=":material/sentiment_satisfied:")

    sent_col1, sent_col2 = st.columns([1, 1])

    with sent_col1:
        sent_counts = sentiment.get("counts", {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0})
        if sum(sent_counts.values()) > 0:
            _render_sentiment_pie(sent_counts)
        else:
            st.caption("No reviews yet")

    with sent_col2:
        with st.container(border=True):
            st.subheader("Overall sentiment")
            avg_sent = sentiment.get("average_score", 0)
            avg_sent_scaled = avg_sent * 100
            st.metric("Average sentiment score", f"{avg_sent:.3f}")
            st.metric("Total reviews", sentiment.get("total_reviews", 0))
            _render_sentiment_gauge(avg_sent_scaled)

    st.divider()

    # Inventory status
    st.subheader("Inventory status", icon=":material/inventory_2:")

    inventory = load_inventory(seller_id)
    inventory_data = []
    low_stock = []

    for item in inventory:
        prod = item.get("product", {})
        discount = item.get("discount_percent", 0)
        status_text = item.get("status", "Good")
        status_display = f"{discount:.0f}% off - {status_text}" if discount else status_text

        inventory_data.append({
            "Product": prod.get("name", "N/A"),
            "Category": prod.get("category", "N/A"),
            "Current stock (kg)": f"{prod.get('stock_kg', 0):.1f}",
            "Base price": f"Rs.{prod.get('price', 0):.2f}",
            "Dynamic price": f"Rs.{item.get('dynamic_price', prod.get('price', 0)):.2f}",
            "Price Change": f"{item.get('price_change_pct', 0):+.1%}",
            "Rating": f"{prod.get('rating', 0):.1f} stars",
            "Status": status_display,
        })
        if "Low" in status_text or "low" in status_text.lower():
            low_stock.append({
                "product_name": prod.get("name", ""),
                "stock_kg": prod.get("stock_kg", 0),
            })

    if inventory_data:
        df_inv = pd.DataFrame(inventory_data)
        st.dataframe(
            df_inv,
            width="stretch",
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn("Status", width="medium"),
            },
        )

        if low_stock:
            st.error("Low stock alerts")
            for alert in low_stock:
                st.write(f"{alert['product_name']}: Only {alert['stock_kg']:.1f} kg remaining!")
    else:
        st.caption("No products listed yet.")

    st.divider()

    # Revenue trend - placeholder
    st.subheader("Revenue trend", icon=":material/account_balance:")
    st.caption("Revenue tracking requires historical order data. As orders accumulate, this chart will populate.")

    # Dynamic pricing recommendations
    st.subheader("Dynamic pricing recommendations", icon=":material/bolt:")

    if inventory_data:
        pricing_cols = st.columns(min(len(inventory_data), 4))
        for i, inv in enumerate(inventory_data[:4]):
            with pricing_cols[i]:
                with st.container(border=True):
                    change_str = inv["Price Change"]
                    # Parse percentage change like "+5.3%" or "-2.1%"
                    change_num = float(change_str.rstrip("%").replace("+", ""))
                    st.metric(
                        inv["Product"][:20],
                        inv["Dynamic price"],
                        delta=f"{change_num:+.1f}%",
                        delta_color="inverse" if change_num < 0 else "normal",
                    )


# --- Fragmented chart loads -------------------------------------------------

def _render_forecast_chart(forecasts):
    """Render the forecast chart in a parallel fragment with skeleton."""

    @st.fragment(parallel=True)
    def _load():
        forecast_data = []
        for fc in forecasts:
            for pred in fc.get("predictions", []):
                trend = "Rising" if fc["trend_slope"] > 0.1 else ("Falling" if fc["trend_slope"] < -0.1 else "Stable")
                forecast_data.append({
                    "Product": fc["product_name"],
                    "Date": pred["date"],
                    "Predicted Quantity (kg)": pred["predicted_quantity"],
                    "Trend": trend,
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
                title="7-day demand forecast by product",
                labels={"Predicted Quantity (kg)": "Quantity (kg)", "Date": "Date"},
            )
            fig.update_layout(hovermode="x unified", height=400)
            with st.container(border=True):
                st.subheader("Forecast chart")
                st.plotly_chart(fig, width="stretch")

    _load()


def _render_trend_summary(forecasts):
    """Render the trend summary cards in a parallel fragment."""

    @st.fragment(parallel=True)
    def _load():
        with st.container(border=True):
            st.subheader("Trend summary")
            trend_cols = st.columns(min(len(forecasts), 5))
            for i, fc in enumerate(forecasts[:5]):
                with trend_cols[i]:
                    slope = fc["trend_slope"]
                    trend_word = "Rising" if slope > 0.1 else ("Falling" if slope < -0.1 else "Stable")
                    st.metric(fc["product_name"][:20], f"{slope:+.2f} kg/day", border=True)

    _load()


def _render_sentiment_pie(sent_counts):
    """Render the sentiment pie chart in a parallel fragment."""

    @st.fragment(parallel=True)
    def _load():
        fig_pie = px.pie(
            values=list(sent_counts.values()),
            names=list(sent_counts.keys()),
            title="Review sentiment distribution",
            color_discrete_map={"POSITIVE": "#28a745", "NEUTRAL": "#ffc107", "NEGATIVE": "#dc3545"},
            hole=0.4,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        with st.container(border=True):
            st.subheader("Sentiment pie")
            st.plotly_chart(fig_pie, width="stretch")

    _load()


def _render_sentiment_gauge(avg_sent_scaled):
    """Render the sentiment gauge in a parallel fragment."""

    @st.fragment(parallel=True)
    def _load():
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=avg_sent_scaled,
            title={"text": "Overall sentiment"},
            gauge={
                "axis": {"range": [-100, 100]},
                "bar": {"color": "darkblue"},
                "steps": [
                    {"range": [-100, -30], "color": "lightcoral"},
                    {"range": [-30, 30], "color": "lightyellow"},
                    {"range": [30, 100], "color": "lightgreen"},
                ],
            },
        ))
        fig_gauge.update_layout(height=250)
        st.plotly_chart(fig_gauge, width="stretch")

    _load()
