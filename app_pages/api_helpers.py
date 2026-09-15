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
}


def material_symbol(name: str, fallback: str = "circle") -> str:
    """Return a ``:material/...:`` icon shorthand."""
    n = MATERIAL_SYMBOLS.get(name, fallback)
    return f":material/{n}:"


def _api_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: int = 20,
    show_spinner: bool = False,
) -> Dict[str, Any]:
    """Cached GET to the backend.  The cache key is (path, params)."""

    @st.cache_data(ttl=ttl, show_spinner=show_spinner)
    def _inner(url: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    url = f"{_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return _inner(url)


def _api_post(
    path: str,
    data: Dict[str, Any],
    ttl: int = 0,
    show_spinner: bool = False,
) -> Dict[str, Any]:
    """POST to the backend (no caching — side-effect)."""
    url = f"{_API}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def material_symbol(name: str, fallback: str = "circle") -> str:
    """Return a ``:material/...:`` shorthand — empty when the material name
    isn't known to us, so the caption still degrades gracefully."""
    return f":material/{name}:"


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


def badge_status(text: str, color: str = "blue") -> None:
    st.badge(label=text, icon=":material/info:", color=color)


def badge_trend(direction: str) -> None:
    color = "green" if direction > 0 else ("red" if direction < 0 else "grey")
    icon = "trending_up" if direction > 0 else ("trending_down" if direction < 0 else "direction")
    st.badge(label=f"{'+' if direction > 0 else ''}{direction:.1f}%", icon=f":material/{icon}:", color=color)


def badge_rating(rating: float) -> None:
    stars = round(rating)
    icon = f"star" if stars > 0 else "star_outline"
    color = "gold" if rating >= 4 else ("orange" if rating >= 3 else "grey")
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
    seller_name = product.get("seller", {}).get("username") or "Seller"
    location = product.get("seller", {}).get("location") or "—"
    stock_status = _stock_status(stock)
    rating_stars = _stars(rating)

    col_img, col_meta, col_price, col_stock, col_action = st.columns(
        [2, 2, 1.2, 1.2, 1.4],
        vertical_alignment="center",
    )

    with col_img:
        st.image(
            "https://img.icons8.com/fluency/96/grass.png",
            width=48,
        )

    with col_meta:
        st.markdown(f"**{name}**", help=f"Category: {category}")
        st.caption(f"{material_symbol('person')} {seller_name} · "
                   f"{material_symbol('place')} {location} · "
                   f"{material_symbol('tag')} {category}")
        st.caption(material_symbol("star") + f"  {rating_stars}")

    with col_price:
        st.markdown(f"**₹{price:.2f} / kg**")
        badge_trend(product.get("price_change_pct") or 0)

    with col_stock:
        st.markdown(f"~{stock:.0f} kg")
        badge_status(stock_status, color="green" if stock_status.startswith("✓") else "red")

    with col_action:
        buyer_id = st.session_state.get("buyer_id")
        if buyer_id:
            api_url = f"/api/orders"
            def _place_order():
                item = product
                _api_post(
                    api_url,
                    {
                        "buyer_id": int(buyer_id),
                        "product_id": int(product["id"]),
                        "quantity_kg": 1.0,
                        "price_per_kg": item.get("price", 0.0),
                    },
                )
                st.rerun()

            btn = st.button(
                f"{material_symbol('shopping_cart')}  Buy 1 kg",
                key=f"buy_{product['id']}_{index}",
                use_container_width=True,
                on_click=_place_order,
            )
        else:
            st.button(
                "Login to buy",
                key=f"login_{product['id']}_{index}",
                use_container_width=True,
                type="secondary",
                disabled=True,
            )


def _stock_status(stock_kg: float) -> str:
    if stock_kg <= 0:
        return "✗  Out of stock"
    if stock_kg < 50:
        return "⚠  Low stock"
    if stock_kg < 200:
        return "●  Limited"
    return "✓  In stock"


def _stars(rating: float) -> str:
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return ("★" * full) + ("½" * half) + ("☆" * empty)


def skeleton_card(label: str = "Loading…") -> None:
    """Placeholder skeleton card for pages that load data asynchronously (#4)."""
    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown(
            "<div style='background:#e5e7eb; height:48px; width:48px; "
            "border-radius:50%; margin-bottom: 8px;'></div>"
            "<div style='background:#e5e7eb; height:14px; width:60%; "
            "border-radius:4px; margin-bottom:4px;'></div>"
            "<div style='background:#e5e7eb; height:10px; width:40%; "
            "border-radius:4px;'></div>",
            unsafe_allow_html=True,
        )
    with col2:
        for _ in range(2):
            st.markdown(
                "<div style='background:#e5e7eb; height:14px; width:100%; "
                "border-radius:4px; margin-bottom:4px;'></div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<div style='background:#e5e7eb; height:40px; width:100%; "
            "border-radius:6px;'></div>",
            unsafe_allow_html=True,
        )


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
    st.plotly_chart(fig, use_container_width=True, theme="streamlit-dark")


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
    st.plotly_chart(fig, use_container_width=True, theme="streamlit-dark")


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
    st.plotly_chart(fig, use_container_width=True, theme="streamlit-dark")


def _current_user_id() -> Optional[int]:
    """Return the current logged-in buyer ID from session state, or None."""
    uid = st.session_state.get("buyer_id")
    if uid and isinstance(uid, (int, float)) and not pd.isna(uid):
        return int(uid)
    return None


def _ensure_session_keys() -> None:
    """Initialise any session_state keys this module expects."""
    pass
