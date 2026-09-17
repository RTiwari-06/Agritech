"""Shared helpers for all Agritech pages.

Covers: HTTP helpers (with caching), rendering primitives (native badges,
material symbols, product cards, charts), and skeleton placeholders (#4).

Improvements covered here:
  #1  Caching — @st.cache_data on read-only API GETs
  #6  Native st.badge helpers — drop unsafe_allow_html badges
  #7  Material Symbols — a couple of centrally useful symbols
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from config import Config
except Exception:
    Config = type("C", (), {})()
    Config.API_BASE_URL = "http://localhost:5000"


# ---------------------------------------------------------------------------
# Configuration & helpers
# ---------------------------------------------------------------------------

_API = getattr(Config, "API_BASE_URL", "http://localhost:5000").rstrip("/")


# Material Symbols icon shorthand — central registry of icons the app uses
MATERIAL_SYMBOLS: Dict[str, str] = {
    "store": "store",
    "grass": "grass",
    "account_circle": "account_circle",
    "person": "person",
    "mail": "mail",
    "search": "search",
    "live_tv": "live_tv",
    "video_camera_front": "video_camera_front",
    "play_arrow": "play_arrow",
    "stop": "stop",
    "support_agent": "support_agent",
    "analytics": "analytics",
    "trending_up": "trending_up",
    "trending_down": "trending_down",
    "currency_rupee": "currency_rupee",
    "inventory_2": "inventory_2",
    "shopping_cart": "shopping_cart",
    "local_shipping": "local_shipping",
    "chart_line": "show_chart",
    "star": "star",
    "star_outline": "star_outline",
    "star_half": "star_half",
    "lock": "lock",
    "warning": "warning",
    "info": "info",
    "cancel": "cancel",
    "check_circle": "check_circle",
    "error": "error",
    "schedule": "schedule",
    "refresh": "refresh",
    "thumb_up": "thumb_up",
    "thumb_down": "thumb_down",
    "mood": "mood",
    "sentiment_satisfied": "sentiment_satisfied",
    "sentiment_neutral": "sentiment_neutral",
    "sentiment_very_dissatisfied": "sentiment_very_dissatisfied",
    "place": "place",
    "tag": "tag",
    "mic": "mic",
    "chat": "chat",
    "arrow_upward": "arrow_upward",
    "arrow_drop_up": "arrow_drop_up",
    "arrow_drop_down": "arrow_drop_down",
    "home": "home",
    "contact_page": "contact_page",
    "logout": "logout",
    "lock_open": "lock_open",
    "person_add": "person_add",
    "monitoring": "monitoring",
    "co2": "co2",
    "verified": "verified",
}


def material_symbol(name: str, fallback: str = "circle") -> str:
    """Return a ``:material/...:`` icon shorthand."""
    n = MATERIAL_SYMBOLS.get(name, fallback)
    return f":material/{n}:"


# ---------------------------------------------------------------------------
# Auth-aware HTTP helpers
# ---------------------------------------------------------------------------

def _auth_token() -> Optional[str]:
    """Current bearer token stored from the login screen, or None."""
    token = st.session_state.get("auth_token")
    return str(token) if token else None


def current_user() -> Optional[Dict[str, Any]]:
    """The authenticated user dict cached in session state, or None."""
    if st.session_state.get("auth_token") and st.session_state.get("auth_user"):
        return st.session_state["auth_user"]
    return None


def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    token = _auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def _api_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: int = 20,
    show_spinner: bool = False,
) -> Dict[str, Any]:
    """Cached GET to the backend.

    The cache key includes the auth token so one user's cached rows can never
    leak into another user's session.
    """

    @st.cache_data(ttl=ttl, show_spinner=show_spinner)
    def _inner(url: str, token: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(
                url,
                headers=_auth_headers(),
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return None

    token = _auth_token()
    url = f"{_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return _inner(url, token)


def _api_post(
    path: str,
    data: Dict[str, Any],
    ttl: int = 0,
    show_spinner: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST to the backend (no caching — side-effect).

    On HTTP errors the server's JSON body (``{"ok": false, "error": ...}``)
    is returned so callers can show the real message; ``None`` is returned
    only for network-level failures.
    """
    url = f"{_API}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        print(f"[api_helpers] {path} error: {exc}")
        return None


def _api_upload(
    path: str, filename: str, file_bytes: bytes, extra: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Multipart POST for market-report uploads (PDF/CSV)."""
    boundary = "----AgritechBoundary" + uuid.uuid4().hex
    body_parts = []
    if extra:
        for key, value in extra.items():
            body_parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            )
    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    )
    body = "".join(body_parts).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{_API}{path}",
        data=body,
        headers=_auth_headers({
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        print(f"[api_helpers] {path} upload error: {exc}")
        return None


# ---------------------------------------------------------------------------
# #6  Native st.badge helpers — no unsafe_allow_html needed
# ---------------------------------------------------------------------------

def badge_sentiment(sentiment: str, delta: float = 0.0) -> None:
    icon_map = {
        "NEGATIVE": "sentiment_very_dissatisfied",
        "NEUTRAL": "sentiment_neutral",
        "POSITIVE": "sentiment_satisfied",
    }
    color_map = {
        "NEGATIVE": "red",
        "NEUTRAL": "grey",
        "POSITIVE": "green",
    }
    icon = icon_map.get(sentiment, fallback_name(sentiment))
    color = color_map.get(sentiment, "grey")
    st.badge(label=f"{sentiment}  ({delta:+.1%})", icon=f":material/{icon}:", color=color)


def badge_status(text: str, color: str = "blue", icon: str = "info") -> None:
    st.badge(label=text, icon=f":material/{icon}:", color=color)


def badge_trend(direction: float, suffix: str = "%") -> None:
    delta = float(direction)
    color = "green" if delta > 0 else ("red" if delta < 0 else "gray")
    icon = "trending_up" if delta > 0 else ("trending_down" if delta < 0 else "trending_flat")
    st.badge(
        label=f"{'+' if delta > 0 else ''}{delta:.1f}{suffix}",
        icon=f":material/{icon}:",
        color=color,
    )


def badge_rating(rating: float) -> None:
    icon = "star" if rating > 0 else "star_outline"
    color = "green" if rating >= 4.0 else ("orange" if rating >= 3.0 else "gray")
    st.badge(label=f"{rating:.1f} / 5.0", icon=f":material/{icon}:", color=color)


def fallback_name(name: str) -> str:
    """Pick a reasonable material-equivalent when exact name unknown."""
    aliases = {
        "live_tv": "live_tv",
        "trending_up": "trending_up",
        "trending_down": "trending_down",
        "star": "star",
        "star_outline": "star_outline",
        "warning": "warning",
        "info": "info",
        "cancel": "cancel",
        "check_circle": "check_circle",
        "error": "error",
        "circle": "circle",
        "search": "search",
        "shopping_cart": "shopping_cart",
        "store": "storefront",
        "grass": "grass",
        "analytics": "analytics",
        "trending_up": "trending_up",
        "person": "person",
        "inventory_2": "inventory_2",
        "currency_rupee": "currency_rupee",
        "shopping_cart": "shopping_cart",
        "schedule": "schedule",
        "local_shipping": "local_shipping",
        "thumb_up": "thumb_up",
        "thumb_down": "thumb_down",
        "chat": "chat",
        "mic": "mic",
        "video_camera_front": "video_camera_front",
        "live_tv": "live_tv",
        "refresh": "refresh",
        "support_agent": "support_agent",
        "mood": "mood",
        "sentiment_very_dissatisfied": "sentiment_very_dissatisfied",
        "sentiment_satisfied": "sentiment_satisfied",
        "sentiment_neutral": "sentiment_neutral",
        "arrow_drop_down": "arrow_drop_down",
        "arrow_drop_up": "arrow_drop_up",
        "precision_manufacturing": "precision_manufacturing",
        "business_center": "business_center",
        "eco": "leaf",
    }
    return aliases.get(name, "circle")


# ---------------------------------------------------------------------------
# Shared rendering helpers — product cards, charts, skeletons
# ---------------------------------------------------------------------------


def product_card_row(product: Dict[str, Any], index: int) -> None:
    """Render one product row (native Streamlit — no unsafe_allow_html)."""
    name = product.get("name") or "Unknown"
    category = product.get("category") or "—"
    price = product.get("price") or 0.0
    stock = product.get("stock_kg") or 0.0
    rating = product.get("rating") or 0.0
    live_engagement = product.get("live_engagement")
    live_boost = product.get("live_price_boost_pct") or 0.0
    seller_name = product.get("seller", {}).get("username") or "Seller"
    location = product.get("seller", {}).get("location") or "—"
    status_key, status_icon, status_color = _stock_status(stock)

    col_img, col_meta, col_price, col_stock, col_action = st.columns(
        [1.2, 2.2, 1.2, 1.2, 1.4],
        vertical_alignment="center",
    )

    with col_img:
        st.image(
            "https://img.icons8.com/fluency/96/grass.png",
            width=48,
        )

    with col_meta:
        st.markdown(f"**{name}**", help=f"Category: {category}")
        st.caption(
            f"{material_symbol('person')} {seller_name} · "
            f"{material_symbol('place')} {location} · "
            f"{material_symbol('tag')} {category}"
        )
        st.markdown(f"{material_symbol('star')}  {_stars(rating)}")

    with col_price:
        st.markdown(f"**₹{price:.2f} / kg**")
        badge_trend(product.get("price_change_pct") or 0)
        if live_engagement is not None:
            st.caption(f"{material_symbol('live_tv')}  Boost {live_boost:+.1f}%")

    with col_stock:
        st.markdown(f"~{stock:.0f} kg")
        badge_status(status_key, color=status_color, icon=status_icon)

    with col_action:
        user = current_user()
        if user and user.get("role") == "buyer":
            def _place_order():
                item = product
                _api_post(
                    "/api/orders",
                    {
                        "product_id": int(product["id"]),
                        "quantity_kg": 1.0,
                        "price_per_kg": item.get("price", 0.0),
                    },
                )
                st.rerun()

            st.button(
                f"{material_symbol('shopping_cart')}  Buy 1 kg",
                key=f"buy_{product['id']}_{index}",
                width="stretch",
                on_click=_place_order,
            )
        else:
            st.button(
                f"{material_symbol('lock')}  Login to buy",
                key=f"login_{product['id']}_{index}",
                width="stretch",
                type="secondary",
                disabled=True,
            )


def _stock_status(stock_kg: float) -> tuple[str, str, str]:
    if stock_kg <= 0:
        return "Out of stock", "cancel", "red"
    if stock_kg < 50:
        return "Low stock", "warning", "orange"
    if stock_kg < 200:
        return "Limited", "inventory_2", "yellow"
    return "In stock", "check_circle", "green"


def _stars(rating: float) -> str:
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = max(5 - full - half, 0)
    return (":material/star: " * full) + (":material/star_half: " * half) + (":material/star_outline: " * empty)


def skeleton_card(label: str = "Loading…") -> None:
    """Native loading skeleton placeholder (#4)."""
    with st.skeleton(height=64):
        st.caption(label)
        st.text("")


def chart_bar(
    title: str,
    categories: List[str],
    values: List[float],
    color: str = "#00B4D8",
    x_label: str = "",
    y_label: str = "",
) -> None:
    """Render a horizontal bar chart via Plotly."""
    fig = go.Figure(data=[go.Bar(
        y=categories,
        x=values,
        orientation="h",
        marker_color=color,
        text=[f"{v:,.2f}" for v in values],
        textposition="outside",
    )])
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="plotly_dark",
        margin=dict(l=120, r=40, t=60, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8EDF2"),
    )
    st.plotly_chart(fig, theme=None)


def chart_line(
    title: str,
    x: List[str],
    y_series: List[tuple],
    x_label: str = "",
    y_label: str = "",
) -> None:
    """Render a line chart; ``y_series`` is ``[(label, values), ...]``."""
    fig = go.Figure()
    for label, vals in y_series:
        fig.add_trace(go.Scatter(
            x=x,
            y=vals,
            mode="lines+markers",
            name=label,
        ))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="plotly_dark",
        margin=dict(l=60, r=40, t=60, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8EDF2"),
    )
    st.plotly_chart(fig, theme=None)


def chart_sentiment_pie(sentiments: Dict[str, int]) -> None:
    labels = list(sentiments.keys())
    sizes = list(sentiments.values())
    colors = {"POSITIVE": "#4CAF50", "NEUTRAL": "#FFC107", "NEGATIVE": "#F44336"}
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=sizes,
        marker_colors=[colors.get(l, "#9E9E9E") for l in labels],
    )])
    fig.update_layout(
        title="Sentiment Distribution",
        template="plotly_dark",
        margin=dict(l=40, r=40, t=40, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#E8EDF2"),
    )
    st.plotly_chart(fig, theme=None)


def _current_user_id() -> Optional[int]:
    """Return the authenticated user's id from session state, or None."""
    user = current_user()
    if user and user.get("id"):
        return int(user["id"])
    return None


def _ensure_session_keys() -> None:
    """Initialise any session_state keys this module expects."""
    pass
