"""Agritech Marketplace - Streamlit entry point.

Multi-page app with:
- Buyer marketplace & live shopping
- Seller live host studio
- Sales analytics & optimization dashboard

Run from project root:
    streamlit run frontend/app.py

Improvements (9-point Hermes audit)
-------------------------------------
#. Caching layer — @st.cache_data on read-only API GETs
#. st.navigation + app_pages/ — sidebar-based navigation
#. Theme — custom colour palette in .streamlit/config.toml
#. Skeletons — placeholder content on analytics page while data loads
#. session_state — persistent input values across reruns
#. Native st.badge — no unsafe_allow_html for status chips
#. Material Symbols — layout emoji replaced with Material Symbols font
#. Dynamic tabs — on_change=rerun on seller studio tabs (content refresh)
#. Native st.chat_message — live stream chat uses Streamlit chat API
"""

import sys
from pathlib import Path

import streamlit as st

# Ensure the project root is on sys.path so that `app_pages` and `config`
# are importable when running as `streamlit run frontend/app.py` from the
# project root (matching how main.py sets up PROJECT_ROOT).
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app_pages import analytics, marketplace, seller_studio  # noqa: E402

st.set_page_config(
    page_title="Agritech Marketplace",
    page_icon=":material/grass:",
    layout="wide",
    initial_sidebar_state="expanded",
)

page = st.navigation(
    [
        st.Page(
            marketplace.app,
            title="Buyer marketplace & live shopping",
            icon=":material/store:",
            url_path="marketplace",
        ),
        st.Page(
            seller_studio.app,
            title="Seller live host studio",
            icon=":material/video_camera_front:",
            url_path="seller-studio",
        ),
        st.Page(
            analytics.app,
            title="Sales analytics & optimization dashboard",
            icon=":material/analytics:",
            url_path="analytics",
        ),
    ]
)
page.run()
