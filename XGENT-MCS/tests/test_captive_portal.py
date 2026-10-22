from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import captive_portal


def _probe_server(status, headers=None, body=b""):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, calls, f"http://127.0.0.1:{server.server_port}/connecttest.txt"


def _close(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_expected_probe_body_reports_clear_without_echoing_response(monkeypatch):
    server, thread, calls, url = _probe_server(200, body=b"Microsoft Connect Test\n")
    monkeypatch.setattr(captive_portal, "PROBE_URL", url)
    try:
        result = captive_portal.detect_captive_portal()
    finally:
        _close(server, thread)
    assert result == {
        "state": "clear", "source": "http_connect_test", "portal_origin": None,
        "detail": None,
    }
    assert calls == ["/connecttest.txt"]


def test_redirect_is_not_followed_and_only_sanitized_origin_is_returned(monkeypatch):
    server, thread, calls, url = _probe_server(
        302, {"Location": "https://portal.example/login?token=private"}, b"",
    )
    monkeypatch.setattr(captive_portal, "PROBE_URL", url)
    try:
        result = captive_portal.detect_captive_portal()
    finally:
        _close(server, thread)
    assert result == {
        "state": "suspected", "source": "http_connect_test",
        "portal_origin": "https://portal.example", "detail": "redirected",
    }
    assert calls == ["/connecttest.txt"]
    assert "private" not in repr(result)


def test_interstitial_html_is_suspected_but_unrecognized_text_is_unknown(monkeypatch):
    server, thread, _calls, url = _probe_server(200, {"Content-Type": "text/html"}, b"<html><title>Sign in</title></html>")
    monkeypatch.setattr(captive_portal, "PROBE_URL", url)
    try:
        html_result = captive_portal.detect_captive_portal()
    finally:
        _close(server, thread)
    assert html_result["state"] == "suspected" and html_result["detail"] == "unexpected_html"
    assert set(html_result) == {"state", "source", "portal_origin", "detail"}

    server, thread, _calls, url = _probe_server(200, body=b"unrelated plain text")
    monkeypatch.setattr(captive_portal, "PROBE_URL", url)
    try:
        text_result = captive_portal.detect_captive_portal()
    finally:
        _close(server, thread)
    assert text_result["state"] == "unknown" and text_result["detail"] == "unexpected_response"


def test_large_probe_response_is_bounded_and_unknown(monkeypatch):
    server, thread, _calls, url = _probe_server(200, body=b"x" * 8192)
    monkeypatch.setattr(captive_portal, "PROBE_URL", url)
    try:
        result = captive_portal.detect_captive_portal()
    finally:
        _close(server, thread)
    assert result["state"] == "unknown" and result["detail"] == "response_too_large"


def test_agent_publishes_correlated_captive_portal_status(monkeypatch):
    import xgent_mcs as agent
    monkeypatch.setattr(captive_portal, "detect_captive_portal", lambda **_kwargs: {
        "state": "suspected", "source": captive_portal.SOURCE,
        "portal_origin": "https://portal.example", "detail": "redirected",
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_captive_portal({"id": "portal-1"})
    topic, result = sent[0]
    assert topic == "captive_portal" and result["schema"] == "xider.captive-portal.v1"
    assert result["feature_id"] == "D04-F11" and result["request_id"] == "portal-1"
    assert result["device_id"] == agent.DEVICE_ID and result["state"] == "suspected"
    assert result["data"] == {"portal_origin": "https://portal.example"}
