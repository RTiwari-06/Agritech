"""Agritech Marketplace — Streamlit multi-page application.

Entry point: ``streamlit run frontend/app.py`` (from the project root).

Page modules
------------
Each page is a module with a single ``app()`` entry function.  The three pages
are wired together by ``frontend/app.py`` via ``st.navigation``.

This package centralises shared helpers in ``api_helpers`` and keeps every
page self-contained so the folder can be dropped into another Streamlit app
without changes.

Improvements delivered across the app (9-point audit)
------------------------------------------------------
#. Caching layer — every read-only API GET is wrapped in ``@st.cache_data``
   on the helper so the browser re-run on input change does not hit the
   backend again within the TTL window.
#. ``st.navigation`` — the old multi-page layout is gone; the sidebar now
   carries ``st.Page`` entries wired back to this package.
#. Theme — custom colour palette shipped in ``.streamlit/config.toml``,
   no layout-breaking custom CSS.
#. Skeletons — the analytics page shows placeholder widgets while data loads.
#. ``st.session_state`` — persistent input values across reruns (selected
   seller, chat history, stream id).
#. Native ``st.badge`` — every status chip / price-change badge / rating
   chip is a real ``st.badge`` call, no more ``unsafe_allow_html`` divs.
#. Material Symbols — layout emoji has been replaced by the Material Symbols
   font shipped via a tiny ``<link>`` plus a Python helper that returns a
   ``<span>`` you can embed in markdown.
#. Dynamic tabs — the seller studio "Inventory / Orders / Chat" tabs switch
   on ``on_change="rerun"`` so selecting a tab always fetches fresh API data.
#. Native ``st.chat_message`` — the live-stream chat in both marketplace and
   seller studio uses ``st.chat_message`` (not a custom HTML chat log).
"""

# This package is imported only by frontend/app.py when Streamlit boots.
# Individual page modules are referenced directly by the navigation builder.

__all__ = ["marketplace", "seller_studio", "analytics"]
