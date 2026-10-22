"""Privacy-preserving summary of configured macOS proxy sources."""

from __future__ import annotations

import subprocess


MACOS_SOURCE = "macos_scutil_proxy_flags"


def parse_scutil_proxy(output: str) -> dict[str, object]:
    """Parse scutil's enabled flags, discarding every endpoint and credential value."""
    if not isinstance(output, str) or "<dictionary>" not in output:
        return {"state": "unknown", "sources": []}
    flags: dict[str, bool] = {}
    for line in output.splitlines():
        key, sep, value = line.strip().partition(":")
        key = key.strip()
        if not sep or key not in {"HTTPEnable", "HTTPSEnable", "SOCKSEnable", "ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable"}:
            continue
        flags[key] = value.strip() == "1"
    mapping = (
        ("HTTPEnable", "http"), ("HTTPSEnable", "https"), ("SOCKSEnable", "socks"),
        ("ProxyAutoConfigEnable", "pac"), ("ProxyAutoDiscoveryEnable", "auto_detect"),
    )
    recognized = any(key in flags for key, _ in mapping)
    if not recognized:
        return {"state": "unknown", "sources": []}
    return {"state": "available", "sources": [name for key, name in mapping if flags.get(key)]}


def collect_proxy_status() -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["/usr/sbin/scutil", "--proxy"], capture_output=True, text=True,
            timeout=4, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"state": "unknown", "sources": [], "detail": "scutil_unavailable"}
    if completed.returncode != 0:
        return {"state": "unknown", "sources": [], "detail": "scutil_query_failed"}
    result = parse_scutil_proxy(completed.stdout)
    result["source"] = MACOS_SOURCE
    return result
