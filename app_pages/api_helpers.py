"""Shared helpers for all Agritech pages.

Covers: HTTP helpers (with caching), material symbols, and chart/skeleton
rendering primitives for the Redis-backed pages (see
``phase_integration_specification.md`` §Phase 2 — all reads go through
``_api_get``/``_api_post``/``_api_put`` against the configured REST endpoint).

Improvements covered here:
  #1  Caching — @st.cache_data on read-only API GETs
  #7  Material Symbols — a couple of centrally useful symbols
"""

from __future__ import annotations

import sys
import http.client
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import Config

API_BASE_URL: str = Config.API_BASE_URL
BASE_URL = API_BASE_URL

BadgeColor = Literal[
    "red", "orange", "yellow", "blue", "green", "violet", "gray", "grey", "primary"
]


def _api_base_url() -> str:
    """Return the API base URL, reading fresh from Config each call."""
    return Config.API_BASE_URL.rstrip("/")


# ---------------------------------------------------------------------------
# Configuration & helpers
# ---------------------------------------------------------------------------

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
# HTTP helpers (the Redis REST engine is public — no auth layer)
# ---------------------------------------------------------------------------

def _http_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def _api_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: int = 20,
    show_spinner: bool = False,
) -> Optional[Dict[str, Any]]:
    """Cached GET to the backend."""

    @st.cache_data(ttl=ttl, show_spinner=show_spinner)
    def _inner(url: str) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(
                url,
                headers=_http_headers(),
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except http.client.HTTPException as exc:
            print(f"[api_helpers] {url} returned an invalid HTTP response: {exc}")
            return None
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            print(f"[api_helpers] {url} request failed: {exc}")
            return None

    url = f"{_api_base_url()}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return _inner(url)


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
    url = f"{_api_base_url()}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers=_http_headers({"Content-Type": "application/json"}),
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
    except http.client.HTTPException as exc:
        print(f"[api_helpers] {path} returned an invalid HTTP response: {exc}")
        return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"[api_helpers] {path} error: {exc}")
        return None


def _api_upload(
    path: str,
    filename: str,
    content: bytes,
) -> Optional[Dict[str, Any]]:
    """POST a file using multipart form data, preserving backend error payloads."""
    url = f"{_api_base_url()}{path}"
    boundary = f"----Agritech{uuid.uuid4().hex}"
    safe_filename = Path(filename.replace("\\", "/")).name
    safe_filename = "".join(
        character for character in safe_filename
        if character.isprintable() and character not in {'"', "\\"}
    )
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        headers=_http_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except http.client.HTTPException as exc:
        print(f"[api_helpers] {path} returned an invalid HTTP response: {exc}")
        return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"[api_helpers] {path} error: {exc}")
        return None


def _api_put(
    path: str,
    data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """PUT JSON to the REST backend, preserving server error payloads."""
    url = f"{_api_base_url()}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=_http_headers({"Content-Type": "application/json"}),
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except http.client.HTTPException as exc:
        print(f"[api_helpers] {path} returned an invalid HTTP response: {exc}")
        return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"[api_helpers] {path} error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Shared rendering helpers — charts and skeletons
# ---------------------------------------------------------------------------

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
