"""Bounded explicit HTTPS upload/download measurement against one test endpoint."""
from __future__ import annotations

import ipaddress
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE = "https_xider_bandwidth_endpoint"
ENDPOINT_PATH = "/.well-known/xider-bandwidth"
MAX_DOWNLOAD_BYTES = 256 * 1024
UPLOAD_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_ENDPOINT_CHARS = 300


def parse_endpoint(value: object) -> dict | None:
    """Accept a single HTTPS XIDER test endpoint, never credentials or redirects."""
    if not isinstance(value, str) or not value or len(value) > MAX_ENDPOINT_CHARS or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if (parsed.scheme.lower() != "https" or not host or parsed.username is not None or parsed.password is not None
            or parsed.path != ENDPOINT_PATH or parsed.query or parsed.fragment):
        return None
    try:
        ip = ipaddress.ip_address(host)
        canonical_host = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    except ValueError:
        try:
            canonical_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            return None
        labels = canonical_host.split(".")
        if (not canonical_host or len(canonical_host) > 253
                or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                       or not all(ch.isalnum() or ch == "-" for ch in label) for label in labels)):
            return None
    canonical_port = f":{port}" if port and port != 443 else ""
    canonical_url = f"https://{canonical_host}{canonical_port}{ENDPOINT_PATH}"
    return {"url": canonical_url, "host": host.lower()}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request: Request, *, timeout: float):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _result(state: str, detail: str | None = None, **fields) -> dict:
    return {"state": state, "source": SOURCE, **fields, **({"detail": detail} if detail else {})}


def measure_throughput(value: str, *, opener=_default_opener, clock=time.perf_counter) -> dict:
    """Run one capped GET then one fixed zero-filled POST against the same HTTPS endpoint."""
    endpoint = parse_endpoint(value)
    if endpoint is None:
        return _result("invalid_request", "invalid_test_endpoint")
    base = endpoint["url"]
    download_url = base + "?" + urlencode({"direction": "download", "bytes": MAX_DOWNLOAD_BYTES})
    download_request = Request(download_url, headers={
        "Accept-Encoding": "identity", "Range": f"bytes=0-{MAX_DOWNLOAD_BYTES - 1}",
        "User-Agent": "XIDER-network-throughput/1",
    }, method="GET")
    try:
        started = clock()
        with opener(download_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
        elapsed = clock() - started
        if status not in {200, 206} or not isinstance(body, bytes) or not body:
            return _result("unknown", "download_probe_failed")
        if len(body) > MAX_DOWNLOAD_BYTES:
            return _result("limit_exceeded", "download_size_limit_exceeded", download_bytes=len(body))
        if elapsed <= 0:
            return _result("unknown", "invalid_measurement_duration")
        download_duration_ms = max(1, round(elapsed * 1000))
        download_rate = round(len(body) * 1000 / download_duration_ms)

        upload_body = b"\0" * UPLOAD_BYTES
        upload_url = base + "?" + urlencode({"direction": "upload", "bytes": UPLOAD_BYTES})
        upload_request = Request(upload_url, data=upload_body, headers={
            "Accept-Encoding": "identity", "Content-Type": "application/octet-stream",
            "Content-Length": str(UPLOAD_BYTES), "User-Agent": "XIDER-network-throughput/1",
        }, method="POST")
        started = clock()
        with opener(upload_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            headers = getattr(response, "headers", {})
            receipt = headers.get("X-Xider-Received-Bytes") if headers else None
        elapsed = clock() - started
        if status not in {200, 204} or str(receipt) != str(UPLOAD_BYTES):
            return _result("unknown", "upload_not_confirmed", download_bytes=len(body),
                           download_duration_ms=download_duration_ms,
                           download_bytes_per_second=download_rate)
        if elapsed <= 0:
            return _result("unknown", "invalid_measurement_duration", download_bytes=len(body),
                           download_duration_ms=download_duration_ms,
                           download_bytes_per_second=download_rate)
        upload_duration_ms = max(1, round(elapsed * 1000))
        return _result("available", download_bytes=len(body), download_duration_ms=download_duration_ms,
                       download_bytes_per_second=download_rate, upload_bytes=UPLOAD_BYTES,
                       upload_duration_ms=upload_duration_ms,
                       upload_bytes_per_second=round(UPLOAD_BYTES * 1000 / upload_duration_ms))
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError):
        return _result("unknown", "probe_failed")
