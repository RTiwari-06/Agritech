"""Agritech — market prices page (Agmarknet data).

Public view: commodity price/arrival series and price-vs-MSP insights,
fed by real "Marketwise Price & Arrival" PDF/CSV reports.
Sellers may upload new reports (PDF or CSV) which the backend parses,
normalizes (Rs/quintal → Rs/kg) and de-duplicates on
(commodity, market, market_date).  Unparseable rows are never dropped
silently — they surface in the ingest report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from app_pages.api_helpers import (
    _api_get,
    _api_post,
    _api_upload,
    current_user,
    material_symbol,
)


@st.cache_data(ttl=60, show_spinner=False)
def _load_commodities() -> Dict[str, Any]:
    resp = _api_get("/api/market/commodities")
    if not resp or not resp.get("ok"):
        return {"groups": [], "markets": []}
    return resp.get("data", {})


@st.cache_data(ttl=30, show_spinner=False)
def _load_series(commodity: str, market: Optional[str]) -> List[Dict[str, Any]]:
    params = {"commodity": commodity}
    if market:
        params["market"] = market
    resp = _api_get("/api/market/series", params)
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", [])


@st.cache_data(ttl=30, show_spinner=False)
def _load_prices(**kwargs) -> List[Dict[str, Any]]:
    resp = _api_get("/api/market/prices", kwargs.get("params", {}))
    if not resp or not resp.get("ok"):
        return []
    return resp.get("data", [])


def _render_public() -> None:
    data = _load_commodities()
    groups = data.get("groups", [])
    markets = data.get("markets", []) or ["National Aggregate"]

    flat_commodities = sorted({c["commodity"] for g in groups for c in _all_commodities(g)})
    if not flat_commodities:
        st.info(
            f"{material_symbol('info')}  No market data yet. "
            "Ask a seller to upload a Marketwise Price & Arrival report."
        )
        return

    c_col, m_col = st.columns([2, 1])
    with c_col:
        commodity = st.selectbox("Commodity", flat_commodities)
    with m_col:
        market = st.selectbox("Market", markets)

    rows = _load_series(commodity, market)

    if not rows:
        st.warning(f"No series found for {commodity}.")
        return

    days = [r["market_date"] for r in rows]
    prices = [r["price_per_kg"] for r in rows]
    msps = [r["msp_per_kg"] for r in rows]
    arrivals = [r["arrival_metric_tonnes"] or 0 for r in rows]

    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else None
    price_delta = None
    if prev and prev.get("price_per_kg") is not None and latest.get("price_per_kg"):
        price_delta = latest["price_per_kg"] - prev["price_per_kg"]

    with st.container(horizontal=True):
        st.metric("Price (₹/kg)", f"{latest['price_per_kg']:.2f}" if latest.get("price_per_kg") is not None else "—",
                  delta=f"{price_delta:+.2f}" if price_delta is not None else None, border=True)
        st.metric("MSP (₹/kg)", f"{latest['msp_per_kg']:.2f}" if latest.get("msp_per_kg") is not None else "—", border=True)
        vs = latest.get("vs_msp_pct")
        st.metric("vs MSP", f"{vs:+.1f}%" if vs is not None else "—", border=True)
        st.metric("Arrival (MT)", f"{latest['arrival_metric_tonnes']:,.1f}" if latest.get("arrival_metric_tonnes") is not None else "—", border=True)
        st.metric("Date", latest["market_date"], border=True)

    chart_l, chart_r = st.columns(2)
    with chart_l:
        df = pd.DataFrame({"date": days, "price": prices, "msp": msps})
        st.line_chart(df.set_index("date"))
        st.caption(f"{material_symbol('show_chart')}  Price vs MSP — {commodity} ({market})")
    with chart_r:
        df2 = pd.DataFrame({"date": days, "arrival_mt": arrivals})
        st.bar_chart(df2.set_index("date"), height=300)
        st.caption(f"{material_symbol('co2')}  Arrival (metric tonnes)")

    st.space("small")
    with st.expander(f"{material_symbol('table')}  Recent market records"):
        st.dataframe(
            pd.DataFrame(_load_prices(params={"commodity": commodity})),
            hide_index=True,
            use_container_width=True,
        )


def _all_commodities(group: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{"commodity": c} for c in group.get("commodities", [])]


def _render_seller_ingest() -> None:
    st.divider()
    st.subheader(f"{material_symbol('upload')}  Upload a Marketwise report (sellers)")
    st.caption(
        "PDF from agmarknet.gov.in or a CSV.  Rows are normalized to ₹/kg "
        "and de-duplicated on commodity + market + date."
    )

    upload_col, info_col = st.columns([1, 2])
    with upload_col:
        uploaded = st.file_uploader("Choose report", type=["pdf", "csv", "txt"])
        if uploaded is not None:
            if st.button(
                f"{material_symbol('cloud_upload')}  Ingest report",
                type="primary",
                width="stretch",
            ):
                with st.spinner("Parsing and ingesting…"):
                    resp = _api_upload(
                        "/api/market/ingest",
                        uploaded.name,
                        uploaded.getvalue(),
                    )
                if resp and resp.get("ok"):
                    j = resp["data"]
                    st.success(
                        f"Ingested {j['ingested']['inserted']} new, "
                        f"{j['ingested']['updated']} updated, "
                        f"{j['ingested']['skipped']} duplicate rows."
                    )
                    if j["parsed"]["errors"] or j["ingested"]["errors"]:
                        st.warning(
                            f"{j['parsed']['errors']}|{j['ingested']['errors']} "
                            "rows could not be parsed — shown below."
                        )
                        for e in (j.get("error_rows") or [])[:10]:
                            st.caption(
                                f"⚠️ {e.get('reason', '')} — `{e.get('raw_text', '')[:120]}`"
                            )
                    st.caption(
                        f"Report date: {j['parsed'].get('report_date')} · "
                        f"Generated: {j['parsed'].get('generated_at')}"
                    )
                    st.cache_data.clear()
                else:
                    st.error((resp or {}).get("error", "Ingest failed.")
                             if resp else "Ingest failed.")

    with info_col:
        resp = _api_get("/api/market/status")
        if resp and resp.get("ok"):
            stats = resp["data"].get("stats", {})
            if stats:
                with st.container(horizontal=True):
                    st.metric("Total records", stats.get("total_records", 0), border=True)
                    st.metric("Real", stats.get("real", 0), border=True)
                    st.metric("Synthetic", stats.get("synthetic", 0), border=True)
                    st.metric("Latest date", stats.get("latest_market_date") or "—", border=True)
            with st.expander("Recent ingests"):
                for log_entry in resp["data"].get("recent_ingests", []):
                    st.caption(
                        f"`{log_entry.get('ingested_at', '')[:19]}`  "
                        f"source={log_entry.get('source')}  "
                        f"+{log_entry.get('inserted', 0)} new / "
                        f"~{log_entry.get('skipped', 0)} dup / "
                        f"⚠{log_entry.get('errors', 0)} err"
                    )


def app() -> None:
    st.title(f"{material_symbol('monitoring')}  Market Prices & Arrival")
    st.caption('Agmarknet "Marketwise Price & Arrival" feeds — normalized to ₹/kg.')

    user = current_user()
    if user and user.get("role") == "seller":
        st.toggle(
            "Seller tools: upload reports & view ingest status",
            key="market_seller_tools",
        )
        if st.session_state.get("market_seller_tools"):
            _render_seller_ingest()

    _render_public()