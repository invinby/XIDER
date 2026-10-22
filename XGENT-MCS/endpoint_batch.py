"""Bounded owner-requested checks against a fixed set of public TCP endpoints."""

from __future__ import annotations

from collections.abc import Callable

import service_probe


MACOS_SOURCE = "fixed_public_tcp_endpoints"
PRESET_ID = "public_basics"
_ENDPOINTS = (
    ("example", "example.com", 443),
    ("cloudflare", "www.cloudflare.com", 443),
    ("github", "github.com", 443),
)
_STATES = {"reachable", "unreachable", "unknown"}
_DETAILS = {None, "probe_timeout", "connection_refused", "probe_failed"}
PER_ENDPOINT_TIMEOUT = 1.5


def check_public_set(*, probe: Callable[..., dict] | None = None) -> dict[str, object]:
    """Probe only the three compiled-in host:443 targets; never accept arbitrary hosts."""
    run_probe = probe or service_probe.probe_service
    results: list[dict[str, str | None]] = []
    for target_id, host, port in _ENDPOINTS:
        try:
            raw = run_probe(host, port, "tcp", timeout=PER_ENDPOINT_TIMEOUT)
        except Exception:
            raw = {"state": "unknown", "detail": "probe_failed"}
        state = raw.get("state") if isinstance(raw, dict) else "unknown"
        detail = raw.get("detail") if isinstance(raw, dict) else "probe_failed"
        if state not in _STATES or detail not in _DETAILS:
            state, detail = "unknown", "probe_failed"
        results.append({"target_id": target_id, "state": state, "detail": detail})
    return {"preset": PRESET_ID, "state": "available", "results": results}
