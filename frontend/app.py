"""Agritech Marketplace - Streamlit entry point.

Multi-page app with:
- Buyer marketplace & live shopping
- Seller live host studio
- Sales analytics & optimization dashboard
- Agmarknet market price/arrival data (PDF/CSV ingest by sellers)

Run from project root:
    streamlit run frontend/app.py

Demo mode
---------
The auth layer has been removed. Every buyer-side action acts as the first
seeded buyer and every seller-side action as the first seeded seller
(``app_pages.api_helpers._ensure_identities``). No login UI exists.

Navigation
----------
The sidebar is disabled; a top-level ``st.segmented_control`` tab bar drives
routing.
"""

import sys
from pathlib import Path

# Silence Windows asyncio ProactorBasePipeTransport socket shutdown tracebacks on connection lost
if sys.platform == "win32":
    import asyncio
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost
        def _silent_call_connection_lost(self, exc=None):
            try:
                _orig_call_connection_lost(self, exc)
            except (OSError, RuntimeError):
                pass
        _ProactorBasePipeTransport._call_connection_lost = _silent_call_connection_lost
    except (ImportError, AttributeError):
        pass

import streamlit as st

# Ensure the project root is on sys.path so that `app_pages` and `config`
# are importable when running as `streamlit run frontend/app.py` from the
# project root (matching how main.py sets up PROJECT_ROOT).
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app_pages import analytics, market_prices, marketplace, seller_studio  # noqa: E402
from app_pages.api_helpers import (  # noqa: E402
    _ensure_identities,
    current_seller,
    current_user,
    material_symbol,
)

st.set_page_config(
    page_title="Agritech Marketplace",
    page_icon=":material/grass:",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Top navigation
# ---------------------------------------------------------------------------

def _tab_bar(pages: list) -> None:
    labels = [p[0] for p in pages]
    icons = [p[1] for p in pages]

    if "nav_page" not in st.session_state or st.session_state.nav_page not in labels:
        st.session_state.nav_page = labels[0]

    selected = st.segmented_control(
        "Navigate",
        options=labels,
        format_func=lambda o: f"{icons[labels.index(o)]}  {o}",
        key="nav_page",
    )
    st.divider()
    return selected


def _run(pages: list) -> None:
    selected = st.session_state.nav_page
    for label, _icon, fn in pages:
        if label == selected:
            fn()
            return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SESSION_KEYS = ("nav_page", "buyer_id", "seller_id")


def _clear_session() -> None:
    for key in _SESSION_KEYS:
        st.session_state.pop(key, None)


def main() -> None:
    if not _ensure_identities():
        st.error(
            f"{material_symbol('error')}  Could not reach the backend on port 5000. "
            "Start it with `python main.py`, then refresh this page."
        )
        st.stop()

    buyer = current_user() or {}
    seller = current_seller() or {}
    username = buyer.get("username") or seller.get("username") or "demo"

    header_l, header_r = st.columns([5, 3])
    with header_l:
        st.markdown(f"## {material_symbol('grass')}  Agritech Marketplace")
    with header_r:
        st.caption(
            f"{material_symbol('account_circle')}  **{username}**  ·  "
            "demo mode (auth removed)"
        )

    pages = [
        ("Market & Shop", "🛍️", marketplace.app),
        ("Live Streams", "🛰️", marketplace.live_streams_page),
        ("Market Prices", "🌾", market_prices.app),
        ("Seller Studio", "🎙️", seller_studio.app),
        ("Analytics", "📈", analytics.app),
    ]

    _tab_bar(pages)
    _run(pages)


main()