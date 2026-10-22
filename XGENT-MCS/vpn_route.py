"""Read-only, privacy-preserving VPN/tunnel interface hints for macOS."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


MACOS_SOURCE = "macos_interface_hints"
_MARKERS = ("vpn", "wireguard", "wintun", "tap-windows", "openvpn", "tailscale", "zerotier",
            "nordlynx", "mullvad", "anyconnect", "globalprotect", "forticlient", "ipsec")


def _looks_like_tunnel(name: str, description: str) -> bool:
    value = f"{name} {description}".casefold()
    if any(marker in value for marker in _MARKERS):
        return True
    compact = name.casefold().replace(" ", "")
    return (compact.startswith(("utun", "tun", "tap", "ppp"))
            or (compact.startswith("wg") and compact[2:3].isdigit()))


def summarize_interfaces(interfaces: Sequence[Mapping[str, object]] | None) -> dict[str, object]:
    """Expose only aggregate interface counts; this does not establish route coverage."""
    if interfaces is None:
        return {"state": "unknown", "up_tunnel_candidates": 0,
                "down_tunnel_candidates": 0, "route_assessment": "not_verified"}
    up = down = 0
    for item in interfaces:
        if not isinstance(item, Mapping):
            return {"state": "unknown", "up_tunnel_candidates": 0,
                    "down_tunnel_candidates": 0, "route_assessment": "not_verified"}
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        if not _looks_like_tunnel(name, description):
            continue
        is_up = item.get("is_up")
        if not isinstance(is_up, bool):
            return {"state": "unknown", "up_tunnel_candidates": 0,
                    "down_tunnel_candidates": 0, "route_assessment": "not_verified"}
        if is_up:
            up += 1
        else:
            down += 1
    return {"state": "available", "up_tunnel_candidates": up,
            "down_tunnel_candidates": down, "route_assessment": "not_verified"}


def collect_vpn_summary() -> dict[str, object]:
    try:
        import psutil

        stats = psutil.net_if_stats()
    except ImportError:
        return {"state": "unknown", "up_tunnel_candidates": 0,
                "down_tunnel_candidates": 0, "route_assessment": "not_verified",
                "detail": "psutil_unavailable"}
    except Exception:
        return {"state": "unknown", "up_tunnel_candidates": 0,
                "down_tunnel_candidates": 0, "route_assessment": "not_verified",
                "detail": "interface_query_failed"}
    rows = [{"name": name, "is_up": bool(status.isup)} for name, status in stats.items()]
    return summarize_interfaces(rows)
