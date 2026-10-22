"""One-shot, credential-free captive portal detection via a fixed HTTP canary."""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROBE_URL = "http://www.msftconnecttest.com/connecttest.txt"
EXPECTED_BODY = b"Microsoft Connect Test"
SOURCE = "http_connect_test"
_MAX_BODY = 512


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def _origin(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    default = 80 if parsed.scheme.lower() == "http" else 443
    suffix = f":{port}" if port is not None and port != default else ""
    return f"{parsed.scheme.lower()}://{host}{suffix}"


def detect_captive_portal(*, timeout: float = 2.5) -> dict:
    """Probe a fixed plain-HTTP canary; never follow redirects or send credentials."""
    if type(timeout) not in {int, float} or not 0.25 <= timeout <= 5:
        raise ValueError("timeout must be between 0.25 and 5 seconds")
    request = Request(
        PROBE_URL,
        method="GET",
        headers={"Accept": "text/plain", "Cache-Control": "no-cache", "User-Agent": "XIDER-captive-check/1"},
    )
    response = None
    try:
        opener = build_opener(_NoRedirect())
        try:
            response = opener.open(request, timeout=float(timeout))
        except HTTPError as exc:
            response = exc
        status = int(response.getcode())
        headers = response.headers
        body = response.read(_MAX_BODY + 1)
    except (URLError, TimeoutError, OSError, ValueError):
        return {"state": "unknown", "source": SOURCE, "portal_origin": None, "detail": "probe_unavailable"}
    finally:
        if response is not None:
            response.close()

    if len(body) > _MAX_BODY:
        return {"state": "unknown", "source": SOURCE, "portal_origin": None, "detail": "response_too_large"}
    location = headers.get("Location") if headers is not None else None
    if 300 <= status < 400:
        return {"state": "suspected", "source": SOURCE,
                "portal_origin": _origin(location), "detail": "redirected"}
    if status == 200 and body.strip() == EXPECTED_BODY:
        return {"state": "clear", "source": SOURCE, "portal_origin": None, "detail": None}
    content_type = (headers.get("Content-Type", "") if headers is not None else "").lower()
    if status == 200 and ("text/html" in content_type or b"<html" in body[:128].lower()):
        return {"state": "suspected", "source": SOURCE, "portal_origin": None, "detail": "unexpected_html"}
    return {"state": "unknown", "source": SOURCE, "portal_origin": None, "detail": "unexpected_response"}
