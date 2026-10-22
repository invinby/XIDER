"""Privacy-preserving summary of configured Windows proxy sources."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping


WINDOWS_SOURCE = "windows_proxy_flags"


def summarize_proxy_sources(
    *, internet_settings: Mapping[str, object], winhttp_output: str, environment: Mapping[str, str],
) -> dict[str, object]:
    """Return categories only; never return proxy endpoints, PAC URLs, or credentials."""
    sources: list[str] = []
    try:
        if int(internet_settings.get("ProxyEnable", 0)) == 1:
            sources.append("current_user_manual")
    except (TypeError, ValueError, OverflowError):
        pass
    if isinstance(internet_settings.get("AutoConfigURL"), str) and internet_settings["AutoConfigURL"].strip():
        sources.append("current_user_pac")
    try:
        if int(internet_settings.get("AutoDetect", 0)) == 1:
            sources.append("current_user_auto_detect")
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = winhttp_output.casefold()
    winhttp_known = False
    if "direct access" in normalized or "no proxy server" in normalized:
        winhttp_known = True
    elif "proxy server(s)" in normalized or "proxy server" in normalized:
        winhttp_known = True
        tail = normalized.split(":", 1)[-1].strip()
        if tail and tail not in {"none", "(none)", "<none>"}:
            sources.append("winhttp")
    if any(isinstance(value, str) and value.strip() for key, value in environment.items()
           if key.casefold() in {"http_proxy", "https_proxy", "all_proxy"}):
        sources.append("environment")
    order = ("current_user_manual", "current_user_pac", "current_user_auto_detect", "winhttp", "environment")
    state = "available" if sources or winhttp_known else "unknown"
    return {"state": state, "sources": [item for item in order if item in sources] if state == "available" else []}


def collect_proxy_status() -> dict[str, object]:
    """Read Windows settings while retaining only boolean/category information."""
    settings: dict[str, object] = {}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL", "AutoDetect"):
                try:
                    settings[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    continue
    except (ImportError, OSError):
        return {"state": "unknown", "sources": []}
    try:
        completed = subprocess.run(
            ["netsh", "winhttp", "show", "proxy"], capture_output=True, text=True,
            timeout=4, check=False,
        )
        winhttp_output = completed.stdout if completed.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        winhttp_output = ""
    result = summarize_proxy_sources(
        internet_settings=settings, winhttp_output=winhttp_output, environment=os.environ,
    )
    result["source"] = WINDOWS_SOURCE
    if result["state"] == "unknown":
        result["detail"] = "winhttp_query_failed"
    return result
