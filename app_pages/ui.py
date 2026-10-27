"""Shared presentation primitives for Agritech Streamlit pages."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import streamlit as st


def page_header(eyebrow: str, title: str, description: str, icon: str) -> None:
    """Render the consistent editorial header used across the app."""
    st.caption(f"AGRITECH  /  {eyebrow.upper()}")
    st.title(title, icon=icon)
    st.caption(description)
