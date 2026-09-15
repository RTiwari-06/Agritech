"""Dashboard blueprint: serves the self-contained web UI at the root URL.

The dashboard is a single-page vanilla HTML/JS app served by Flask itself,
so running ``python main.py`` exposes both the REST API (``/api/*``) and the
UI (``/``) from the same origin — no separate frontend process required.
"""

from flask import Blueprint, render_template

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static/dashboard",
)


@dashboard_bp.get("/")
def dashboard_page():
    return render_template("dashboard.html")