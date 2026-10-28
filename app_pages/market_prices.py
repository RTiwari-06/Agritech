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

from app_pages.api_helpers import _api_get, _api_upload
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


def _comparison_rows(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build comparable Day 1 and Day 2 APMC prices for each commodity."""
    return [
        {
            "Commodity": str(product.get("name") or "Unknown"),
            "Day 1 (₹/kg)": float(product.get("day1_price") or 0.0),
            "Day 2 (₹/kg)": float(product.get("day2_price") or 0.0),
        }
        for product in products
    ]


def _render_report_uploads() -> None:
    """Upload market reports and explore their Redis-backed price history."""
    with st.container(border=True):
        st.subheader("Import a market report")
        st.caption(
            "Upload an Agmarknet Marketwise Price & Arrival PDF or a CSV with "
            "commodity, market date, and price, MSP, or arrival columns."
        )
        uploaded_file = st.file_uploader(
            "Market report",
            type=["pdf", "csv"],
            key="market_report_upload",
        )
        if st.button(
            "Import report",
            key="import_market_report",
            disabled=uploaded_file is None,
        ):
            result = _api_upload(
                "/api/market-prices/upload",
                uploaded_file.name,
                uploaded_file.getvalue(),
            )
            if result and result.get("ok"):
                details = result.get("data") or {}
                st.success(
                    f"Imported {details.get('rows_parsed', 0)} rows "
                    f"({details.get('inserted', 0)} new, "
                    f"{details.get('updated', 0)} updated)."
                )
            else:
                st.error(
                    (result or {}).get("error")
                    or "Report upload failed; check the Flask API connection."
                )

    response = _api_get("/api/market-prices", params={"limit": 1000}, ttl=0)
    if response is None:
        st.error("Could not load market history. Check the Flask API and Redis connection.")
        return
    records = response.get("data") or []
    if not records:
        st.info("No market reports have been imported yet.")
        return

    commodities = sorted({str(row.get("commodity") or "Unknown") for row in records})
    selected_commodity = st.selectbox(
        "Commodity",
        commodities,
        key="market_history_commodity",
    )
    filtered = [
        row for row in records
        if str(row.get("commodity") or "Unknown") == selected_commodity
    ]
    markets = sorted({str(row.get("market") or "Unknown") for row in filtered})
    selected_market = st.selectbox(
        "Market",
        ["All markets", *markets],
        key="market_history_market",
    )
    if selected_market != "All markets":
        filtered = [
            row for row in filtered
            if str(row.get("market") or "Unknown") == selected_market
        ]

    history = pd.DataFrame(
        [
            {
                "Date": row.get("market_date"),
                "Price (₹/kg)": row.get("price_per_kg"),
                "MSP (₹/kg)": row.get("msp_per_kg"),
                "Arrival (MT)": row.get("arrival_metric_tonnes"),
                "Market": row.get("market"),
                "Source": row.get("source"),
            }
            for row in filtered
        ]
    )
    for column in ("Price (₹/kg)", "MSP (₹/kg)", "Arrival (MT)"):
        history[column] = pd.to_numeric(history[column], errors="coerce")

    price_columns = [
        column for column in ("Price (₹/kg)", "MSP (₹/kg)")
        if history[column].notna().any()
    ]
    if price_columns:
        st.subheader(f"{selected_commodity} price and MSP history")
        st.line_chart(
            history,
            x="Date",
            y=price_columns,
            color=["#287D58", "#D89B28"][:len(price_columns)],
        )
    if history["Arrival (MT)"].notna().any():
        st.subheader("Reported arrivals")
        st.bar_chart(history, x="Date", y="Arrival (MT)", color="#287D58")

    st.dataframe(history, hide_index=True, width="stretch")
    st.caption(
        "MSP and arrival values remain blank when the source report does not provide them."
    )


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

    comparison = _comparison_rows(filtered_products)
    if comparison:
        st.subheader("Day 2 price comparison")
        st.caption(
            "Compare each commodity’s Day 2 APMC price with its Day 1 reference (₹/kg)."
        )
        st.bar_chart(
            pd.DataFrame(comparison),
            x="Commodity",
            y=["Day 1 (₹/kg)", "Day 2 (₹/kg)"],
            horizontal=True,
            sort="-Day 2 (₹/kg)",
            stack=False,
            height=max(420, min(780, len(comparison) * 22)),
        )
        if all(
            row["Day 1 (₹/kg)"] == row["Day 2 (₹/kg)"]
            for row in comparison
        ):
            st.info(
                "The current market feed has identical Day 1 and Day 2 prices "
                "for these commodities, so no day-over-day movement is available."
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

    with st.expander("View exact comparison values"):
        st.dataframe(
            [
                {
                    **row,
                    "Category": str(product.get("category") or "Other"),
                    "Live (₹/kg)": float(
                        product.get("dynamic_price") or product.get("day2_price") or 0.0
                    ),
                    "Movement": f"{float(product.get('price_change_pct') or 0.0):+.1f}%",
                }
                for row, product in zip(comparison, filtered_products)
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
        _render_report_uploads()
        return

    _render_public()
