from http.client import BadStatusLine

from app_pages import api_helpers


def test_api_base_url_uses_configured_backend(monkeypatch):
    monkeypatch.setattr(
        api_helpers.Config,
        "API_BASE_URL",
        "http://127.0.0.2:5000/",
    )

    assert api_helpers._api_base_url() == "http://127.0.0.2:5000"


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


def test_api_upload_sends_multipart_file_and_parses_backend_response(monkeypatch):
    monkeypatch.setattr(
        api_helpers.Config,
        "API_BASE_URL",
        "http://127.0.0.2:5000",
    )
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def read():
            return b'{"ok": true, "data": {"rows_parsed": 1}}'

    def capture_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(api_helpers.urllib.request, "urlopen", capture_request)

    result = api_helpers._api_upload(
        "/api/market-prices/upload",
        "..\\report.csv\r\nX-Injected: yes",
        b"commodity,market,date\r\nTomato,Bengaluru,2026-10-25",
    )

    request = captured["request"]
    assert result == {"ok": True, "data": {"rows_parsed": 1}}
    assert request.get_method() == "POST"
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert b'filename="report.csvX-Injected: yes"\r\n' in request.data
    assert b"\r\nX-Injected: yes" not in request.data
    assert b"Tomato,Bengaluru,2026-10-25" in request.data
    assert captured["timeout"] == 30
