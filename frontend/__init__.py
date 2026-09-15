"""Streamlit marketplace & seller analytics frontend."""

# Import the page modules directly so that running
# `streamlit run frontend/app.py` from the project root works.
# (Avoids a circular `from frontend.app import main` which triggers
#  app.py before sys.path is ready.)
from app_pages import analytics, marketplace, seller_studio

__all__ = ["marketplace", "seller_studio", "analytics"]