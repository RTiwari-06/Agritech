"""Agritech Marketplace - Streamlit entry point.

Multi-page app with:
- Buyer marketplace & live shopping
- Seller live host studio
- Sales analytics & optimization dashboard
- Agmarknet market price/arrival data (PDF/CSV ingest by sellers)

Run from project root:
    streamlit run frontend/app.py

Auth flow
---------
A login gate sits in front of every page.  On success the bearer token and
user dict are kept in ``st.session_state`` (``auth_token`` / ``auth_user``)
and every ``api_helpers`` request attaches ``Authorization: Bearer <token>``.

Navigation
----------
The sidebar is disabled; a top-level ``st.segmented_control`` tab bar drives
routing.  Tabs are role-filtered: sellers see the Seller Studio + Analytics
tabs, buyers don't.
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

from app_pages import analytics, market_prices, marketplace, seller_studio  # noqa: E402
from app_pages.api_helpers import current_user, material_symbol, _api_post  # noqa: E402

st.set_page_config(
    page_title="Agritech Marketplace",
    page_icon=":material/grass:",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

_CAPTIONS = (
    "Demo accounts use password **`demo-password-123`** for every user.  "
    "Sellers: `ramesh_organic_farms`, `karnataka_produce_hub`, `punjab_grain_corp`.  "
    "Buyers: `lakshmi_bazaar`, `sanjay_supermarkets`, `meena_food_labs`, "
    "`rajesh_restaurants`, `priya_export_hub`."
)


def _grant(resp: dict) -> None:
    """Store the bearer token + user, then proceed to the app."""
    st.session_state.auth_token = resp["data"]["token"]
    st.session_state.auth_user = resp["data"]["user"]
    st.session_state.nav_page = None
    st.rerun()


def _signin_block() -> None:
    with st.form("signin_form", border=False):
        identifier = st.text_input(
            "Username or email",
            placeholder="e.g. ramesh_organic_farms",
        )
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button(
            f"{material_symbol('lock_open')}  Sign in",
            type="primary",
            width="stretch",
        )
        if submitted:
            resp = _api_post(
                "/api/auth/login",
                {"identifier": identifier, "password": password},
            )
            if resp and resp.get("ok"):
                _grant(resp)
            else:
                st.error(
                    f"{material_symbol('error')}  "
                    f"{(resp or {}).get('error', 'Login failed.')}"
                )


def _register_block() -> None:
    with st.form("register_form", border=False):
        r_username = st.text_input("Username")
        r_email = st.text_input("Email")
        r_password = st.text_input("Password", type="password", help="At least 8 characters")
        r_role = st.selectbox("I am a", ["buyer", "seller"])
        r_location = st.text_input("Location", placeholder="e.g. Nashik, Maharashtra")
        submitted = st.form_submit_button(
            f"{material_symbol('person_add')}  Create account",
            type="primary",
            width="stretch",
        )
        if submitted:
            if not (r_username.strip() and r_email.strip() and r_password):
                st.error(f"{material_symbol('warning')}  Username, email, and password are required.")
            elif len(r_password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                resp = _api_post(
                    "/api/auth/register",
                    {
                        "username": r_username.strip(),
                        "email": r_email.strip(),
                        "password": r_password,
                        "role": r_role,
                        "location": r_location,
                    },
                )
                if resp and resp.get("ok"):
                    _grant(resp)
                else:
                    st.error(
                        f"{material_symbol('error')}  "
                        f"{(resp or {}).get('error', 'Registration failed.')}"
                    )


def _auth_view() -> None:
    st.title(f"{material_symbol('grass')}  Agritech Marketplace")
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.caption(_CAPTIONS)
        st.markdown("### Welcome")
        mode = st.segmented_control(
            "Access",
            ["Sign in", "Create account"],
            default="Sign in",
        )
        if mode == "Create account":
            _register_block()
        else:
            _signin_block()
    st.stop()


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

def main() -> None:
    if not st.session_state.get("auth_token"):
        _auth_view()

    user = current_user() or {}
    role = user.get("role", "buyer")
    username = user.get("username", "guest")

    header_l, header_r = st.columns([5, 3])
    with header_l:
        st.markdown(f"## {material_symbol('grass')}  Agritech Marketplace")
    with header_r:
        st.caption(
            f"{material_symbol('account_circle')}  **{username}**  ·  `{role}`"
        )
        if st.button(f"{material_symbol('logout')}  Sign out"):
            _api_post("/api/auth/logout", {})
            for key in ("auth_token", "auth_user", "nav_page", "buyer_id", "seller_id"):
                st.session_state.pop(key, None)
            st.rerun()

    pages = [
        ("Market & Shop", "🛍️", marketplace.app),
        ("Live Streams", "🛰️", marketplace.live_streams_page),
        ("Market Prices", "🌾", market_prices.app),
    ]
    if role == "seller":
        pages += [
            ("Seller Studio", "🎙️", seller_studio.app),
            ("Analytics", "📈", analytics.app),
        ]

    _tab_bar(pages)
    _run(pages)


main()