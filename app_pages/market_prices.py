"""Market signals backed by the Redis commodity REST feed."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from app_pages.api_helpers import _api_get
from app_pages.ui import page_header


def _price_history(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the APMC reference days and live price for charting."""
    return [
        {"Period": "Day 1", "Price (₹/kg)": float(product.get("day1_price") or 0.0)},
        {"Period": "Day 2", "Price (₹/kg)": float(product.get("day2_price") or 0.0)},
        {
            "Period": "Live",
            "Price (₹/kg)": float(
                product.get("dynamic_price") or product.get("day2_price") or 0.0
            ),
        },
    ]


@st.fragment(run_every=3)
def _render_public() -> None:
    """Show the live market comparison from the currently supported endpoint."""
    response = _api_get("/api/products", ttl=3)
    products = (response or {}).get("data") or []
    if not products:
        st.warning(
            "Market feed unavailable. Confirm Flask is running on port 5000 "
            "and Redis is available on port 6379."
        )
        return

    st.caption(
        f"Live Redis market feed · {len(products)} commodities · refreshes every 3 seconds"
    )
    categories = sorted(
        {str(product.get("category") or "Other") for product in products}
    )
    filter_col, commodity_col = st.columns([1, 2])
    with filter_col:
        category = st.selectbox(
            "Category",
            ["All categories", *categories],
            key="market_signal_category",
        )

    filtered_products = [
        product
        for product in products
        if category == "All categories" or product.get("category") == category
    ]
    with commodity_col:
        selected_id = st.selectbox(
            "Commodity",
            [str(product["id"]) for product in filtered_products],
            format_func=lambda value: next(
                str(product.get("name", value))
                for product in filtered_products
                if str(product["id"]) == value
            ),
            key="market_signal_product",
        )

    product = next(
        item for item in filtered_products if str(item["id"]) == selected_id
    )
    day1 = float(product.get("day1_price") or 0.0)
    day2 = float(product.get("day2_price") or 0.0)
    dynamic = float(product.get("dynamic_price") or day2)
    day_trend = ((day2 - day1) / day1 * 100.0) if day1 else 0.0
    price_change = float(product.get("price_change_pct") or 0.0)

    with st.container(horizontal=True):
        st.metric(
            "APMC market price",
            f"₹{day2:,.2f}/kg",
            delta=f"{day_trend:+.1f}% vs Day 1",
            border=True,
        )
        st.metric(
            "Dynamic price",
            f"₹{dynamic:,.2f}/kg",
            delta=f"{price_change:+.1f}%",
            border=True,
        )
        st.metric(
            "Stock available",
            f"{float(product.get('stock_kg') or 0.0):,.1f} kg",
            border=True,
        )

    chart_col, detail_col = st.columns([1.5, 1])
    with chart_col:
        with st.container(border=True):
            st.subheader("Price movement")
            history = pd.DataFrame(_price_history(product))
            st.line_chart(
                history,
                x="Period",
                y="Price (₹/kg)",
                color="#287D58",
            )
            st.caption(
                f"{product.get('name', 'Commodity')} · "
                f"{product.get('category', 'Other')}"
            )
    with detail_col:
        with st.container(border=True):
            st.subheader("Market snapshot")
            st.metric("Day 1 reference", f"₹{day1:,.2f}/kg")
            st.metric("Day 2 reference", f"₹{day2:,.2f}/kg")
            st.badge(
                "Rate clamped" if product.get("capped") else "Within price band",
                icon=(
                    ":material/warning:"
                    if product.get("capped")
                    else ":material/check_circle:"
                ),
                color="orange" if product.get("capped") else "green",
            )

    with st.expander("Compare all commodities"):
        st.dataframe(
            [
                {
                    "Commodity": item.get("name", "Unknown"),
                    "Category": item.get("category", "Other"),
                    "Day 1 (₹/kg)": float(item.get("day1_price") or 0.0),
                    "Day 2 (₹/kg)": float(item.get("day2_price") or 0.0),
                    "Live (₹/kg)": float(
                        item.get("dynamic_price") or item.get("day2_price") or 0.0
                    ),
                    "Movement": f"{float(item.get('price_change_pct') or 0.0):+.1f}%",
                }
                for item in filtered_products
            ],
            hide_index=True,
            width="stretch",
        )


def app() -> None:
    page_header(
        "Market signals",
        "Read the price beneath the price.",
        "Compare APMC reference prices with live, demand-adjusted market prices.",
        ":material/query_stats:",
    )
    section = st.segmented_control(
        "Market data view",
        ["Price explorer", "Report uploads"],
        default="Price explorer",
        key="market_prices_view",
        label_visibility="collapsed",
    )
    if section == "Report uploads":
        st.info(
            "Market report uploads and MSP/arrival history are not available in "
            "the current Phase 2 REST backend. The live commodity price explorer "
            "uses the supported Redis-backed feed."
        )
        return

    _render_public()
