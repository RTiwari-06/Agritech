from http.client import BadStatusLine

from app_pages import api_helpers


def test_api_get_handles_non_http_response(monkeypatch, capsys):
    monkeypatch.setattr(
        api_helpers.Config,
        "API_BASE_URL",
        "http://invalid-http-response.test",
    )

    def raise_bad_status_line(*args, **kwargs):
        raise BadStatusLine("-ERR wrong number of arguments for 'get' command")

    monkeypatch.setattr(api_helpers.urllib.request, "urlopen", raise_bad_status_line)

    assert api_helpers._api_get("/api/products", ttl=3) is None
    assert "invalid HTTP response" in capsys.readouterr().out
