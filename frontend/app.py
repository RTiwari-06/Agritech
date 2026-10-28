"""Fieldnote — Agritech's Streamlit workspace."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st

from app_pages import analytics, market_prices, marketplace, seller_studio
from app_pages.api_helpers import API_BASE_URL, _api_get


def _marketplace_page() -> None:
    marketplace.app()


def _market_prices_page() -> None:
    market_prices.app()


def _seller_studio_page() -> None:
    seller_studio.app()


def _analytics_page() -> None:
    analytics.app()


st.set_page_config(
    page_title="Fieldnote · Agritech",
    page_icon=":material/eco:",
    layout="wide",
    initial_sidebar_state="expanded",
)


page = st.navigation(
    {
        "Discover": [
            st.Page(
                _marketplace_page,
                title="Market",
                icon=":material/storefront:",
                default=True,
            ),
            st.Page(
                _market_prices_page,
                title="Market signals",
                icon=":material/query_stats:",
            ),
        ],
        "Manage": [
            st.Page(
                _seller_studio_page,
                title="Seller studio",
                icon=":material/inventory_2:",
            ),
            st.Page(
                _analytics_page,
                title="Insights",
                icon=":material/analytics:",
            ),
        ],
    },
    position="sidebar",
)

with st.sidebar:
    st.markdown("## :material/eco: Fieldnote")
    st.caption("A clearer view of every market.")
    health = _api_get("/health", ttl=5)
    if health and health.get("ok") and health.get("redis"):
        st.badge("Services connected", icon=":material/check_circle:", color="green")
    elif health and health.get("ok"):
        st.badge("Redis unavailable", icon=":material/warning:", color="orange")
    else:
        st.badge("Flask API unavailable", icon=":material/error:", color="red")
        st.caption("Flask uses port 5000; Redis should use port 6379.")
    st.caption(f"REST · {API_BASE_URL}")
    st.caption("Live market data refreshes every 3 seconds.")

with st.container(horizontal=True, vertical_alignment="center"):
    st.markdown("### :material/eco: Fieldnote")
    st.caption("MARKET INTELLIGENCE FOR GROWERS")

page.run()
