"""Central application configuration.

All values are sourced from the environment (see ``.env.example``). A
``.env`` file next to this module is loaded automatically via python-dotenv.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str, default: bool = False) -> bool:
    return value.lower() in ("1", "true", "yes", "on") if value else default


class Config:
    """Process-wide settings for the framework."""

    # --- Core server -------------------------------------------------------
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    DEBUG: bool = _as_bool(os.getenv("DEBUG", ""), default=False)
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "5000"))
    SQL_ECHO: bool = _as_bool(os.getenv("SQL_ECHO", ""), default=False)

    # --- Databases ----------------------------------------------------------
    # SQLite by default (agri_sales.db); PostgreSQL support via DATABASE_URI / PG_DATABASE_URI.
    _default_sqlite = "sqlite:///" + (BASE_DIR / "data" / "agri_sales.db").as_posix()
    DATABASE_URI: str = os.getenv("DATABASE_URI", "") or _default_sqlite
    # Optional override, handy to keep a separate URI for CI/production.
    PG_DATABASE_URI: str = os.getenv("PG_DATABASE_URI", "")

    # --- MongoDB (primary data store when MONGO_URI is set) ----------------
    # When MONGO_URI is empty the framework uses mongomock (no server needed).
    MONGO_URI: str = os.getenv("MONGO_URI", "")
    MONGO_DB: str = os.getenv("MONGO_DB", "agritech")

    # Switch backend: "mongo" (default) or "sql" (legacy SQLAlchemy path)
    DATABASE_BACKEND: str = os.getenv("DATABASE_BACKEND", "mongo").lower()

    # --- NLP -----------------------------------------------------------------
    NLP_MODEL: str = os.getenv("NLP_MODEL", "en_core_web_sm")
    SENTIMENT_MODEL: str = os.getenv(
        "SENTIMENT_MODEL", "distilbert-base-uncased-finetuned-sst-2-english"
    )
    INTENT_MODEL: str = os.getenv(
        "INTENT_MODEL", "facebook/bart-large-mnli"
    )
    USE_TRANSFORMER_SENTIMENT: bool = _as_bool(
        os.getenv("USE_TRANSFORMER_SENTIMENT", ""), default=False
    )
    AUTO_DOWNLOAD_NLP_MODEL: bool = _as_bool(
        os.getenv("AUTO_DOWNLOAD_NLP_MODEL", ""), default=True
    )

    # --- Twilio ---------------------------------------------------------------
    TWILIO_ENABLED: bool = _as_bool(os.getenv("TWILIO_ENABLED", ""), default=False)
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")

    # --- Consumers -------------------------------------------------------------
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:5000")

    @classmethod
    def effective_database_uri(cls) -> str:
        """Return the URI the SQLAlchemy engine should connect to."""
        return cls.PG_DATABASE_URI or cls.DATABASE_URI

    @classmethod
    def twilio_ready(cls) -> bool:
        return bool(
            cls.TWILIO_ENABLED
            and cls.TWILIO_ACCOUNT_SID
            and cls.TWILIO_AUTH_TOKEN
            and cls.TWILIO_FROM_NUMBER
        )