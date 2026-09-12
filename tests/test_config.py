"""Configuration unit tests."""

from config import Config


def test_sane_defaults():
    assert Config.SECRET_KEY
    assert 0 < Config.PORT <= 65535
    assert Config.NLP_MODEL == "en_core_web_sm"
    assert Config.HOST


def test_default_database_is_sqlite():
    uri = Config.effective_database_uri()
    assert uri.startswith("sqlite:///")


def test_pg_override_wins(monkeypatch):
    monkeypatch.setattr(Config, "PG_DATABASE_URI", "postgresql://u:p@localhost/agr")
    try:
        assert Config.effective_database_uri() == "postgresql://u:p@localhost/agr"
    finally:
        monkeypatch.setattr(Config, "PG_DATABASE_URI", "")


def test_twilio_readiness(monkeypatch):
    monkeypatch.setattr(Config, "TWILIO_ENABLED", False)
    monkeypatch.setattr(Config, "TWILIO_ACCOUNT_SID", "ACx")
    monkeypatch.setattr(Config, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(Config, "TWILIO_FROM_NUMBER", "+15550000000")
    assert Config.twilio_ready() is False

    monkeypatch.setattr(Config, "TWILIO_ENABLED", True)
    monkeypatch.setattr(Config, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(Config, "TWILIO_AUTH_TOKEN", "token123")
    monkeypatch.setattr(Config, "TWILIO_FROM_NUMBER", "+15550000000")
    assert Config.twilio_ready() is True