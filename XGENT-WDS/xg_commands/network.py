"""Network diagnostics command handlers."""

from __future__ import annotations

import ipaddress
import re
import socket
import time


def wifi_passwords(
    payload: dict, *, device_id, publish_response, run_without_console,
    check_output, devnull, parse_wifi_profile_names, parse_key_content,
) -> None:
    try:
        profiles_out = run_without_console(
            check_output, ["netsh", "wlan", "show", "profiles"],
            text=True, stderr=devnull, timeout=10, encoding="utf-8", errors="ignore",
        )
        names = parse_wifi_profile_names(profiles_out)
        if not names:
            text, ok = "📶 Сохранённые Wi-Fi сети не найдены.", True
        else:
            rows, shown = [], names[:15]
            for name in shown:
                try:
                    output = run_without_console(
                        check_output, ["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"],
                        text=True, stderr=devnull, timeout=10,
                        encoding="utf-8", errors="ignore",
                    )
                    password = parse_key_content(output)
                    rows.append(f"• {name}: {password or '(без ключа)'}")
                except Exception:
                    rows.append(f"• {name}: (недоступен)")
            text = "📶 Сохранённые Wi-Fi:\n" + "\n".join(rows)
            if len(names) > len(shown):
                text += f"\n… и ещё {len(names) - len(shown)}"
            ok = True
    except Exception as exc:
        text, ok = f"⚠️ Wi-Fi: {exc}", False
    publish_response("net_wifi_passwords", {
        "type": "net_wifi_passwords", "device_id": device_id, "ok": ok, "text": text[:3800],
    })


def bluetooth_list(payload: dict, *, device_id, publish_response, run_without_console,
                   check_output, devnull) -> None:
    try:
        output = run_without_console(
            check_output,
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class Bluetooth -Status OK | Select-Object -First 20 -ExpandProperty FriendlyName"],
            text=True, stderr=devnull, timeout=15,
        )
        items = [line.strip() for line in output.splitlines() if line.strip()]
        text = "🔵 Bluetooth устройства:\n" + ("\n".join(f"• {item}" for item in items) or "(нет активных)")
        ok = True
    except Exception as exc:
        items, text, ok = [], f"⚠️ Bluetooth: {exc}", False
    publish_response("net_bluetooth_list", {
        "type": "net_bluetooth_list", "device_id": device_id,
        "ok": ok, "items": items[:20], "text": text,
    })


def net_ping(payload: dict, *, device_id, publish_response, run_without_console, subprocess_module, re_module) -> None:
    """Run the legacy bounded Windows ping and preserve its user-facing response."""
    host = payload.get("host", "8.8.8.8")
    host = re_module.sub(r"[^\w\.-]", "", str(host)) or "8.8.8.8"
    try:
        result = run_without_console(
            subprocess_module.run,
            ["ping", "-n", "4", host],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout or result.stderr
        text = f"🏓 Результаты Ping к {host}:\n```\n{output.strip()[:3500]}\n```"
        ok = result.returncode == 0
    except Exception as exc:
        text = f"⚠️ Ошибка ping: {exc}"
        ok = False
    publish_response("output", {
        "type": "net_ping", "device_id": device_id, "ok": ok, "text": text,
    })


def external_ip(payload: dict, *, device_id, publish_response, urlopen, ipaddress_module, datetime_module) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "ext_ip", "schema": "xider.external-ip.v1", "feature_id": "D04-F30",
        "device_id": device_id, "request_id": request_id, "state": "unknown",
        "source": "ipify", "ip": "",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with urlopen("https://api.ipify.org", timeout=6) as response:
            candidate = response.read(65).decode("ascii", "ignore").strip()
        address = ipaddress_module.ip_address(candidate)
        if address.is_global and str(address) == candidate:
            result.update(state="available", ip=candidate)
    except Exception:
        # Provider details can include URLs, proxy configuration, or local
        # network metadata; report unknown without leaking those details.
        pass
    result["text"] = f"🌍 Внешний IP: {result['ip']}" if result["ip"] else "Внешний IP не подтверждён"
    publish_response("ext_ip", result)


def captive_portal_command(payload: dict, *, device_id, publish_response, detect_portal, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "captive_portal",
        "schema": "xider.captive-portal.v1",
        "feature_id": "D04-F11",
        "device_id": device_id,
        "request_id": request_id,
        "state": "unknown",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None,
        "detail": "probe_failed",
    }
    try:
        snapshot = detect_portal()
        result["state"] = snapshot.get("state", "unknown")
        result["source"] = snapshot.get("source", source)
        origin = snapshot.get("portal_origin")
        result["data"] = {"portal_origin": origin} if origin is not None else None
        result["detail"] = snapshot.get("detail")
    except Exception:
        pass
    publish_response("captive_portal", result)


def ipv6_connectivity_command(payload: dict, *, device_id, publish_response, probe_ipv6, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "ipv6_connectivity",
        "schema": "xider.ipv6-connectivity.v1",
        "feature_id": "D04-F12",
        "device_id": device_id,
        "request_id": request_id,
        "state": "unknown",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None,
        "detail": "probe_failed",
    }
    try:
        snapshot = probe_ipv6()
        state = snapshot.get("state")
        result["state"] = state if state in {"reachable", "no_route", "unknown", "unsupported"} else "unknown"
        result["source"] = snapshot.get("source", source)
        result["detail"] = snapshot.get("detail")
    except Exception:
        pass
    publish_response("ipv6_connectivity", result)


def tls_certificate_command(payload: dict, *, device_id, publish_response, parse_target, probe_certificate, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    raw_host = payload.get("host")
    raw_target = f"[{raw_host}]:{payload.get('port')}" if isinstance(raw_host, str) and ":" in raw_host else f"{raw_host}:{payload.get('port')}"
    parsed = parse_target(raw_target)
    host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
    result = {
        "type": "tls_certificate",
        "schema": "xider.tls-certificate.v1",
        "feature_id": "D04-F13",
        "device_id": device_id,
        "request_id": request_id,
        "host": host,
        "port": port,
        "source": source,
        "state": "invalid_request" if not parsed else "unknown",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": "invalid_endpoint" if not parsed else "tls_probe_failed",
        "data": None,
    }
    if parsed:
        try:
            snapshot = probe_certificate(host, port)
            if snapshot.get("state") in {"valid", "expired", "not_yet_valid", "chain_invalid", "unknown"}:
                result.update(state=snapshot["state"], detail=snapshot.get("detail"), data=snapshot.get("data"))
        except Exception:
            pass
    publish_response("tls_certificate", result)


def path_mtu_command(payload: dict, *, device_id, publish_response, diagnose_path_mtu, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "path_mtu",
        "schema": "xider.path-mtu.v1",
        "feature_id": "D04-F10",
        "device_id": device_id,
        "request_id": request_id,
        "state": "unknown",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None,
        "detail": None,
    }
    try:
        snapshot = diagnose_path_mtu(payload.get("target"), platform="windows")
        result["source"], result["state"] = snapshot.get("source", source), snapshot.get("state", "unknown")
        if result["state"] == "supported":
            result["data"] = {key: snapshot[key] for key in ("target", "mtu", "max_tested") if key in snapshot}
        else:
            result["data"] = None
            result["detail"] = snapshot.get("detail") or (
                "ping_unavailable" if result["state"] == "unsupported" else "probe_no_confirmed_reply"
            )
    except ValueError:
        result.update(state="invalid_request", detail="invalid_path_mtu_target")
    except Exception:
        result.update(state="unknown", detail="path_mtu_probe_failed")
    publish_response("path_mtu", result)


def service_probe_command(payload: dict, *, device_id, publish_response, parse_target, probe_service, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    raw_host = payload.get("host")
    raw_target = f"[{raw_host}]:{payload.get('port')}" if isinstance(raw_host, str) and ":" in raw_host else f"{raw_host}:{payload.get('port')}"
    parsed = parse_target(raw_target)
    protocol = payload.get("protocol")
    valid = parsed is not None and protocol in {"tcp", "tls"}
    host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
    result = {
        "type": "service_probe",
        "schema": "xider.service-probe.v1",
        "feature_id": "D04-F14",
        "device_id": device_id,
        "request_id": request_id,
        "host": host,
        "port": port,
        "protocol": protocol if protocol in {"tcp", "tls"} else None,
        "source": source,
        "state": "unknown" if valid else "invalid_request",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": "probe_failed" if valid else "invalid_endpoint_or_protocol",
    }
    if valid:
        try:
            snapshot = probe_service(host, port, protocol)
            if snapshot.get("state") in {"reachable", "reachable_untrusted", "unreachable", "unknown"}:
                result.update(state=snapshot["state"], detail=snapshot.get("detail"))
        except Exception:
            pass
    publish_response("service_probe", result)


def latency_series_command(payload: dict, *, device_id, publish_response, valid_endpoint, measure_latency, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    host, port = payload.get("host"), payload.get("port")
    valid = valid_endpoint(host, port)
    result = {
        "type": "latency_series", "schema": "xider.latency-series.v1", "feature_id": "D04-F01",
        "device_id": device_id, "request_id": request_id,
        "host": host if valid else None, "port": port if valid else None,
        "source": source, "state": "unknown" if valid else "invalid_request",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "samples": [], "summary": None,
        "detail": "probe_failed" if valid else "invalid_endpoint",
    }
    if valid:
        try:
            snapshot = measure_latency(host, port)
            if snapshot.get("source") == source and snapshot.get("state") in {"available", "partial", "unknown"}:
                result.update(state=snapshot["state"], samples=snapshot["samples"],
                              summary=snapshot["summary"], detail=snapshot["detail"])
        except Exception:
            pass
    publish_response("latency_series", result)


def packet_loss_history_command(payload: dict, *, device_id, publish_response, literal_address, measure_packet_loss, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    address = literal_address(payload.get("address"))
    snapshot = None
    if address:
        try:
            snapshot = measure_packet_loss(address=address)
        except Exception:
            pass
    result = {
        "type": "packet_loss_history", "schema": "xider.packet-loss-history.v1", "feature_id": "D04-F02",
        "device_id": device_id, "request_id": request_id,
        "address": address, "source": source,
        "state": snapshot.get("state") if snapshot and snapshot.get("state") in {"complete", "partial", "unknown"} else "invalid_request" if not address else "unknown",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": snapshot if snapshot and snapshot.get("source") == source else {
            "address": address, "state": "unknown", "source": source,
            "detail": "probe_failed" if address else "invalid_literal_address", "sent": 10,
            "observed": 0, "received": 0, "lost": 0, "loss_percent": None, "samples": [],
        },
    }
    publish_response("packet_loss_history", result)


def jitter_measurement_command(payload: dict, *, device_id, publish_response, literal_address, measure_jitter, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    address = literal_address(payload.get("address"))
    snapshot = None
    if address:
        try:
            snapshot = measure_jitter(address=address)
        except Exception:
            pass
    state = snapshot.get("state") if snapshot else "unknown"
    if state not in {"available", "partial", "unknown"}:
        state = "invalid_request" if not address else "unknown"
    data = snapshot if snapshot and snapshot.get("source") == source else {
        "address": address, "state": "unknown", "source": source,
        "detail": "probe_failed" if address else "invalid_literal_address",
        "attempted": 10, "received": 0, "lost": 0, "unknown": 10,
        "pairs": 0, "jitter_ms": None, "samples": [],
    }
    publish_response("network_jitter", {
        "type": "network_jitter", "schema": "xider.network-jitter.v1", "feature_id": "D04-F03",
        "device_id": device_id, "request_id": request_id, "address": address,
        "source": source, "state": state,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data,
    })


def network_throughput_command(payload: dict, *, device_id, publish_response, parse_endpoint, measure_throughput, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    endpoint = parse_endpoint(payload.get("endpoint"))
    result = measure_throughput(endpoint["url"]) if endpoint else {
        "state": "invalid_request", "source": source,
        "detail": "invalid_test_endpoint",
    }
    state = result.get("state") if result.get("state") in {"available", "unknown", "limit_exceeded", "invalid_request"} else "unknown"
    publish_response("network_throughput", {
        "type": "network_throughput", "schema": "xider.network-throughput.v1", "feature_id": "D04-F04",
        "device_id": device_id, "request_id": request_id,
        "endpoint": endpoint["url"] if endpoint else None,
        "source": source, "state": state,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": result,
    })


def network_link_speed_command(payload: dict, *, device_id, publish_response, collect_catalog, collect_link_speed, source) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    interface_id = payload.get("interface_id")
    catalog = collect_catalog()
    if interface_id is None:
        data = {"state": catalog["state"], "interfaces": catalog["interfaces"], "detail": catalog["detail"]}
        mode = "catalog"
    else:
        data = collect_link_speed(interface_id)
        mode = "measure"
    state = data.get("state") if data.get("state") in {"available", "unsupported", "unknown"} else "unknown"
    publish_response("network_link_speed", {
        "type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
        "device_id": device_id, "request_id": request_id, "mode": mode,
        "source": source, "state": state,
        "interface_id": interface_id if isinstance(interface_id, str) else None,
        "data": data,
    })


def route_lookup_command(payload: dict, *, device_id, publish_response, normalize_destination, lookup_route, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    destination = payload.get("destination")
    try:
        normalized = normalize_destination(destination)
        data = lookup_route(normalized)
    except (ValueError, TypeError):
        data = {"state": "invalid_request"}
    publish_response("route_lookup", {
        "type": "route_lookup", "schema": "xider.route-lookup.v1", "feature_id": "D04-F22",
        "request_id": request_id, "device_id": device_id,
        "source": source, "state": data.get("state", "unknown"),
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data,
    })


def endpoint_batch_command(payload: dict, *, device_id, publish_response, check_public_set, preset_id, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    ids = ("example", "cloudflare", "github")
    try:
        sample = check_public_set()
    except Exception:
        sample = {"preset": preset_id, "state": "unknown", "results": []}
    state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
    rows = sample.get("results")
    results = []
    if sample.get("preset") != preset_id or not isinstance(rows, list) or len(rows) != len(ids):
        state, rows = "unknown", []
    for index, target_id in enumerate(ids):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
        item_state, detail = row.get("state"), row.get("detail")
        if row.get("target_id") != target_id or item_state not in {"reachable", "unreachable", "unknown"}:
            item_state, detail = "unknown", "probe_failed"
            state = "unknown"
        if detail not in {None, "probe_timeout", "connection_refused", "probe_failed"}:
            item_state, detail, state = "unknown", "probe_failed", "unknown"
        results.append({"target_id": target_id, "state": item_state, "detail": detail})
    publish_response("endpoint_batch", {
        "type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
        "device_id": device_id, "request_id": request_id, "source": source,
        "preset": preset_id, "state": state, "results": results,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
    })


def wifi_quality_command(payload: dict, *, device_id, publish_response, collect_wifi_quality, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        sample = collect_wifi_quality()
    except Exception:
        sample = {"state": "unknown", "metric": None, "value": None,
                  "source": source, "detail": "wifi_query_failed"}
    metric, value = sample.get("metric"), sample.get("value")
    if sample.get("state") != "available":
        metric = value = None
    publish_response("wifi_quality_history", {
        "type": "wifi_quality_history", "schema": "xider.wifi-quality.v1", "feature_id": "D04-F15",
        "device_id": device_id, "request_id": request_id, "source": source,
        "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
        "metric": metric, "value": value,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else "wifi_query_failed",
    })


def wifi_channel_command(payload: dict, *, device_id, publish_response, collect_wifi_channel, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        sample = collect_wifi_channel()
    except Exception:
        sample = {"state": "unknown", "channel": None, "width_mhz": None,
                  "width_state": "unknown", "source": source,
                  "detail": "wifi_query_failed"}
    channel = sample.get("channel") if sample.get("state") == "available" else None
    width = sample.get("width_mhz") if channel is not None else None
    width_state = sample.get("width_state") if width is None else "available"
    if width_state not in {"available", "unsupported", "unknown"}:
        width_state = "unknown"
    publish_response("wifi_channel_summary", {
        "type": "wifi_channel_summary", "schema": "xider.wifi-channel.v1", "feature_id": "D04-F16",
        "device_id": device_id, "request_id": request_id, "source": source,
        "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
        "channel": channel, "width_mhz": width, "width_state": width_state,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else None,
    })


def wifi_connection_history_command(payload: dict, *, device_id, publish_response, collect_history, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        sample = collect_history()
    except Exception:
        sample = {"state": "unknown", "events": [], "detail": "event_log_unavailable"}
    state = sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown"
    events = []
    if state == "available" and isinstance(sample.get("events"), list):
        for event in sample["events"][-20:]:
            if isinstance(event, dict) and isinstance(event.get("at"), str) and event.get("kind") in {"connected", "disconnected"}:
                events.append({"at": event["at"], "kind": event["kind"]})
    if state == "available" and not events:
        state = "unknown"
    detail = sample.get("detail")
    allowed_details = {"no_connection_events", "event_log_unavailable", "invalid_event_log"}
    publish_response("wifi_connection_history", {
        "type": "wifi_connection_history", "schema": "xider.wifi-connection-history.v1",
        "feature_id": "D04-F17", "device_id": device_id, "request_id": request_id,
        "source": source, "state": state, "events": events,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": detail if detail in allowed_details else ("invalid_event_log" if state == "unknown" else None),
    })


def proxy_status_command(payload: dict, *, device_id, publish_response, collect_proxy_status, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        sample = collect_proxy_status()
    except Exception:
        sample = {"state": "unknown", "sources": []}
    state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
    sources = sample.get("sources") if state == "available" else []
    allowed = {"current_user_manual", "current_user_pac", "current_user_auto_detect", "winhttp", "environment"}
    if not isinstance(sources, list) or any(item not in allowed for item in sources) or len(set(sources)) != len(sources):
        state, sources = "unknown", []
    publish_response("proxy_status", {
        "type": "proxy_status", "schema": "xider.proxy-status.v1", "feature_id": "D04-F23",
        "device_id": device_id, "request_id": request_id, "source": source,
        "state": state, "sources": sources,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": sample.get("detail") if sample.get("detail") in {"registry_unavailable", "winhttp_query_failed"} else None,
    })


def vpn_route_status_command(payload: dict, *, device_id, publish_response, collect_vpn_summary, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        sample = collect_vpn_summary()
    except Exception:
        sample = {"state": "unknown", "up_tunnel_candidates": 0,
                  "down_tunnel_candidates": 0, "route_assessment": "not_verified"}
    state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
    up = sample.get("up_tunnel_candidates", 0)
    down = sample.get("down_tunnel_candidates", 0)
    if (not isinstance(up, int) or isinstance(up, bool) or not 0 <= up <= 128
            or not isinstance(down, int) or isinstance(down, bool) or not 0 <= down <= 128
            or sample.get("route_assessment") != "not_verified"):
        state, up, down = "unknown", 0, 0
    if state == "unknown":
        up, down = 0, 0
    publish_response("vpn_route_status", {
        "type": "vpn_route_status", "schema": "xider.vpn-route.v1", "feature_id": "D04-F24",
        "device_id": device_id, "request_id": request_id, "source": source,
        "state": state, "up_tunnel_candidates": up, "down_tunnel_candidates": down,
        "route_assessment": "not_verified",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "detail": sample.get("detail") if sample.get("detail") in {"psutil_unavailable", "interface_query_failed"} else None,
    })


def network_traffic_command(payload: dict, *, device_id, publish_response, collect_catalog, collect_interface_traffic, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    interface_id = payload.get("interface_id")
    if interface_id is None:
        sample = collect_catalog()
        result = {
            "type": "network_traffic", "schema": "xider.network-traffic.v1",
            "feature_id": "D04-F18", "device_id": device_id, "request_id": request_id,
            "source": source, "mode": "catalog",
            "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
            "interfaces": sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else [],
            "detail": sample.get("detail") if sample.get("detail") in {"counter_source_unavailable", "no_interfaces"} else None,
        }
    else:
        sample = collect_interface_traffic(interface_id)
        result = {
            "type": "network_traffic", "schema": "xider.network-traffic.v1",
            "feature_id": "D04-F18", "device_id": device_id, "request_id": request_id,
            "source": source, "mode": "sample",
            "state": sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown",
            "interface_id": sample.get("interface_id"), "label": sample.get("label"),
            "sent_bytes": sample.get("sent_bytes"), "received_bytes": sample.get("received_bytes"),
            "sampled_at": sample.get("sampled_at"),
            "detail": sample.get("detail") if sample.get("detail") in {"counter_unavailable", "counter_source_unavailable", "interface_not_found"} else None,
        }
    result["checked_at"] = datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z")
    publish_response("network_traffic", result)


def gateway_probe_command(payload: dict, *, device_id, publish_response, probe_gateway, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        snapshot = probe_gateway()
    except Exception:
        snapshot = {"state": "unknown", "gateway": None, "source": source, "detail": "gateway_probe_failed"}
    state = snapshot.get("state")
    if state not in {"reachable", "unknown", "no_gateway", "unsupported"}:
        state = "unknown"
    gateway = snapshot.get("gateway")
    try:
        gateway = str(ipaddress.IPv4Address(gateway)) if gateway is not None else None
    except (ipaddress.AddressValueError, TypeError):
        gateway, state = None, "unknown"
    publish_response("gateway_probe", {
        "type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
        "device_id": device_id, "request_id": request_id, "state": state,
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {"gateway": gateway} if gateway else None,
        "detail": snapshot.get("detail") if isinstance(snapshot.get("detail"), str) else None,
    })


def wifi_info_command(payload: dict, *, device_id, publish_response, run_without_console, check_output, devnull, parse_wifi_output) -> None:
    try:
        output = run_without_console(
            check_output, ["netsh", "wlan", "show", "interfaces"],
            text=True, stderr=devnull, timeout=10,
        )
        parsed = parse_wifi_output(output)
        text = f"📶 Wi-Fi: {parsed['ssid'] or '—'} (сигнал: {parsed['signal'] or '—'})"
    except Exception as exc:
        parsed, text = {}, f"⚠️ Wi-Fi недоступен: {exc}"
    publish_response("wifi_info", {
        "type": "wifi_info", "device_id": device_id, "ok": bool(parsed), "text": text, **parsed,
    })


def network_traffic_interval_command(payload: dict, *, device_id, publish_response, schedule_path, save_schedule, wake_event, logger) -> None:
    request_id = payload.get("id")
    state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None,
             "interface_id": None, "threshold_bytes": None, "next_sample_at": None}
    try:
        state = save_schedule(
            schedule_path, payload.get("interval_seconds"), payload.get("schedule_id"),
            payload.get("interface_id"), payload.get("threshold_bytes"),
            first_sample=payload.get("interval_seconds") != 0,
        )
        state = {**state, "state": "supported"}
        wake_event.set()
    except (OSError, TypeError, ValueError):
        logger.warning("Rejected or could not persist D04-F19 schedule", exc_info=True)
    publish_response("network_traffic_alert", {
        "type": "network_traffic_alert_status", "schema": "xider.network-traffic-alert.v1",
        "feature_id": "D04-F19", "device_id": device_id,
        "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
        **state,
    })


def network_traffic_schedule_loop(*, running, wake_event, path, load_schedule, save_schedule, collect_interface_traffic, publish_response, device_id, logger, source, time_module=time) -> None:
    while running.is_set():
        schedule = load_schedule(path)
        interval, next_at = schedule["interval_seconds"], schedule["next_sample_at"]
        if interval == 0 or not isinstance(next_at, int):
            wake_event.wait(30)
            wake_event.clear()
            continue
        delay = next_at - int(time_module.time())
        if delay > 0:
            wake_event.wait(min(delay, 30))
            wake_event.clear()
            continue
        schedule_id = schedule["schedule_id"]
        interface_id = schedule["interface_id"]
        try:
            current = load_schedule(path)
            if current != schedule:
                continue
            sample = collect_interface_traffic(interface_id)
            current = load_schedule(path)
            if current != schedule:
                continue
            updated = save_schedule(path, interval, schedule_id, interface_id, schedule["threshold_bytes"])
            state = sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown"
            counters_valid = all(type(sample.get(key)) is int and sample[key] >= 0
                                 for key in ("sent_bytes", "received_bytes"))
            if state == "available" and not counters_valid:
                state = "unknown"
            publish_response("network_traffic_alert", {
                "type": "network_traffic_alert_sample", "schema": "xider.network-traffic-alert.v1",
                "feature_id": "D04-F19", "device_id": device_id,
                "schedule_id": schedule_id, "interface_id": interface_id,
                "source": source, "state": state,
                "sent_bytes": sample.get("sent_bytes") if state == "available" else None,
                "received_bytes": sample.get("received_bytes") if state == "available" else None,
                "sampled_at": sample.get("sampled_at") if state == "available" else None,
                "next_sample_at": updated["next_sample_at"],
                "detail": sample.get("detail") if sample.get("detail") in {
                    "counter_unavailable", "counter_source_unavailable", "interface_not_found"
                } else None,
            })
        except Exception:
            logger.warning("D04-F19 scheduled sample unavailable", exc_info=True)
            current = load_schedule(path)
            if (current.get("schedule_id") == schedule_id
                    and current.get("interval_seconds") == interval
                    and current.get("interface_id") == interface_id):
                try:
                    save_schedule(path, interval, schedule_id, interface_id, current["threshold_bytes"])
                except (OSError, TypeError, ValueError):
                    logger.warning("Could not advance D04-F19 retry deadline", exc_info=True)


def interface_status_history_command(payload: dict, *, device_id, publish_response, path, collect_catalog, configure_monitor, collect_status, record_sample, read_history, stop_monitor, wake_event, logger, source, datetime_module) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None
    mode = payload.get("mode")
    interface_id = payload.get("interface_id")
    state, detail, events, interfaces, interval = "unknown", None, [], [], 0
    if mode == "catalog":
        sample = collect_catalog()
        state = sample.get("state", "unknown")
        interfaces = sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else []
        detail = sample.get("detail")
    elif mode == "start":
        try:
            config = configure_monitor(path, interface_id, interval_seconds=payload.get("interval_seconds", 300))
            sample = collect_status(interface_id)
            record_sample(path, interface_id, sample)
            wake_event.set()
            state, interval = "monitoring", config["interval_seconds"]
            events = read_history(path, interface_id)["events"]
            detail = "initial_state_unknown" if sample.get("state") == "unknown" else None
        except (OSError, TypeError, ValueError):
            logger.warning("Rejected or could not persist D04-F20 monitor", exc_info=True)
            detail = "monitor_configuration_failed"
    elif mode in {"read", "stop"} and isinstance(interface_id, str):
        if mode == "stop":
            stop_monitor(path)
            wake_event.set()
        result = read_history(path, interface_id)
        state, events, detail = result["state"], result["events"], result["detail"]
    else:
        detail = "invalid_request"
    publish_response("interface_status_history", {
        "type": "interface_status_history", "schema": "xider.interface-status-history.v1",
        "feature_id": "D04-F20", "device_id": device_id, "request_id": request_id,
        "source": source, "mode": mode if mode in {"catalog", "start", "read", "stop"} else "unknown",
        "state": state if state in {"available", "unsupported", "unknown", "monitoring"} else "unknown",
        "interface_id": interface_id if isinstance(interface_id, str) else None,
        "interval_seconds": interval, "interfaces": interfaces[:32], "events": events[-120:],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "detail": detail if detail in {None, "status_source_unavailable", "no_interfaces", "invalid_interface",
                                        "interface_not_found", "no_observed_transitions", "initial_state_unknown",
                                        "monitor_configuration_failed", "invalid_request"} else "unknown",
    })


def interface_status_history_loop(*, running, wake_event, path, load_monitor, stop_monitor, collect_status, record_sample, logger, time_module=time) -> None:
    current_key, next_sample_at = None, 0
    while running.is_set():
        monitor = load_monitor(path)
        now = int(time_module.time())
        key = (monitor.get("interface_id"), monitor.get("interval_seconds")) if monitor.get("enabled") else None
        if key is None:
            current_key, next_sample_at = None, 0
            wake_event.wait(30)
            wake_event.clear()
            continue
        if monitor.get("expires_at", 0) <= now:
            stop_monitor(path)
            current_key, next_sample_at = None, 0
            continue
        if key != current_key:
            current_key, next_sample_at = key, now
        if now < next_sample_at:
            wake_event.wait(min(next_sample_at - now, 30))
            wake_event.clear()
            continue
        try:
            latest = load_monitor(path)
            if not latest.get("enabled") or (latest.get("interface_id"), latest.get("interval_seconds")) != key:
                current_key, next_sample_at = None, 0
                continue
            sample = collect_status(key[0])
            record_sample(path, key[0], sample, now=now)
        except Exception:
            logger.warning("D04-F20 interface status sample unavailable", exc_info=True)
        next_sample_at = now + key[1]


def dns_resolution(payload: dict, *, device_id, publish_response, resolve_dns, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "dns_resolution",
        "schema": "xider.dns-resolution.v1",
        "feature_id": "D04-F05",
        "device_id": device_id,
        "request_id": request_id,
        "state": "supported",
        "source": "system_getaddrinfo",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None,
        "detail": None,
    }
    try:
        result["data"] = resolve_dns(payload.get("hostname"), payload.get("expected_addresses"))
    except ValueError:
        result.update(state="invalid_request", detail="invalid_dns_request")
    except TimeoutError:
        result.update(state="unknown", detail="dns_lookup_timeout_or_busy")
    except (LookupError, socket.gaierror):
        result.update(state="unavailable", detail="dns_name_unavailable")
    except OSError:
        result.update(state="unavailable", detail="system_resolver_unavailable")
    publish_response("dns_resolution", result)


def dns_settings(payload: dict, *, device_id, publish_response, read_settings, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    try:
        snapshot = read_settings()
        resolvers = snapshot.get("resolvers", [])
        state = snapshot.get("state", "supported" if resolvers else "unknown")
        if state not in {"supported", "unknown", "unsupported", "unavailable"}:
            state, resolvers = "unknown", []
        if not isinstance(resolvers, list):
            state, resolvers = "unknown", []
    except Exception:
        snapshot = {"source": source}
        state, resolvers = "unknown", []
    result = {
        "type": "dns_settings",
        "schema": "xider.dns-settings.v1",
        "feature_id": "D04-F06",
        "device_id": device_id,
        "request_id": request_id,
        "state": state,
        "source": snapshot.get("source", source),
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {"resolvers": resolvers},
    }
    publish_response("dns_settings", result)


def dns_comparison(payload: dict, *, device_id, publish_response, compare_resolvers, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "dns_comparison", "schema": "xider.dns-comparison.v1", "feature_id": "D04-F07",
        "device_id": device_id, "request_id": request_id, "state": "supported",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None, "detail": None,
    }
    try:
        result["data"] = compare_resolvers(payload.get("hostname"), payload.get("resolvers"))
        if result["data"].get("comparison") == "unknown":
            result["state"] = "unknown"
    except ValueError:
        result.update(state="invalid_request", detail="invalid_dns_comparison_request")
    except (OSError, TimeoutError):
        result.update(state="unknown", detail="dns_resolver_unavailable")
    publish_response("dns_comparison", result)


def dnssec_check(payload: dict, *, device_id, publish_response, check_domain, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "dnssec_check", "schema": "xider.dnssec-check.v1", "feature_id": "D04-F26",
        "device_id": device_id, "request_id": request_id, "state": "unknown",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "hostname": None, "resolver": None, "ad_bit": None, "rcode": None,
    }
    try:
        checked = check_domain(payload.get("hostname"), payload.get("resolver"))
        result.update(hostname=checked["hostname"], resolver=checked["resolver"],
                      state=checked["assessment"], ad_bit=checked["ad_bit"], rcode=checked["rcode"])
    except ValueError:
        result.update(state="invalid_request")
    except (OSError, TimeoutError):
        result.update(state="unknown")
    publish_response("dnssec_check", result)


def route_trace_command(payload: dict, *, device_id, publish_response, trace_route, source, datetime_module) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
        "device_id": device_id, "request_id": request_id, "state": "unknown",
        "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": None, "detail": None,
    }
    try:
        traced = trace_route(payload.get("target"), platform="windows")
        result["data"] = {key: traced[key] for key in ("target", "reached", "hops") if key in traced}
        result["source"] = traced.get("source", source)
        result["state"] = traced.get("state", "unknown")
        result["detail"] = traced.get("detail")
    except ValueError:
        result.update(state="invalid_request", detail="invalid_route_trace_target")
    except Exception:
        result.update(state="unknown", detail="route_trace_failed")
    publish_response("route_trace", result)


from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    bound = {}
    method_names = {'_do_packet_loss_history', '_do_wifi_channel_summary', '_do_ext_ip', '_do_latency_series', '_do_captive_portal', '_do_network', '_do_route_trace', '_do_network_link_speed', '_do_dnssec_check', '_interface_status_history_loop', '_do_network_traffic', '_do_endpoint_batch', '_do_tls_certificate', '_do_interface_status_history', '_do_network_throughput', '_do_net_wifi_passwords', '_do_gateway_probe', '_do_net_bluetooth_list', '_do_dns_resolution', '_do_ipv6_connectivity', '_do_network_traffic_interval', '_do_jitter_measurement', '_do_path_mtu', '_do_net_ping', '_do_wifi_connection_history', '_do_netstat', '_do_wifi_info', '_network_traffic_schedule_loop', '_do_proxy_status', '_do_path_open', '_do_dns_comparison', '_do_route_lookup', '_do_wifi_quality_history', '_do_vpn_route_status', '_do_download_url', '_do_dns_settings'}
    for name in method_names:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def _do_network_traffic_interval(self, payload: dict) -> None:
        _network_commands.network_traffic_interval_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            schedule_path=CONFIG_DIR / "network_traffic_schedule.json",
            save_schedule=save_network_traffic_schedule,
            wake_event=self._network_traffic_schedule_wake, logger=log,
        )

def _network_traffic_schedule_loop(self) -> None:
        _network_commands.network_traffic_schedule_loop(
            running=self._running, wake_event=self._network_traffic_schedule_wake,
            path=CONFIG_DIR / "network_traffic_schedule.json",
            load_schedule=load_network_traffic_schedule, save_schedule=save_network_traffic_schedule,
            collect_interface_traffic=network_traffic.collect_interface_traffic,
            publish_response=self._publish_response, device_id=DEVICE_ID,
            logger=log, source=network_traffic.SOURCE,
        )

def _do_interface_status_history(self, payload: dict) -> None:
        _network_commands.interface_status_history_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            path=CONFIG_DIR / "interface_status_history.json",
            collect_catalog=interface_status_history.collect_catalog,
            configure_monitor=interface_status_history.configure_monitor,
            collect_status=interface_status_history.collect_interface_status,
            record_sample=interface_status_history.record_sample,
            read_history=interface_status_history.read_history,
            stop_monitor=interface_status_history.stop_monitor,
            wake_event=self._interface_status_history_wake, logger=log,
            source=interface_status_history.SOURCE, datetime_module=datetime,
        )

def _interface_status_history_loop(self) -> None:
        _network_commands.interface_status_history_loop(
            running=self._running, wake_event=self._interface_status_history_wake,
            path=CONFIG_DIR / "interface_status_history.json",
            load_monitor=interface_status_history.load_monitor,
            stop_monitor=interface_status_history.stop_monitor,
            collect_status=interface_status_history.collect_interface_status,
            record_sample=interface_status_history.record_sample, logger=log,
        )

def _do_network(self, payload: dict) -> None:
        """Сводка по сетевым интерфейсам и трафику."""
        return _system_info_commands.network(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, format_network=_format_network, logger=log,
        )

def _do_path_open(self, payload: dict) -> None:
        return _filesystem_commands.path_open(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            expanduser=os.path.expanduser, startfile=lambda path: os.startfile(path),  # type: ignore[attr-defined]
        )

def _do_ext_ip(self, payload: dict) -> None:
        import urllib.request
        return _network_commands.external_ip(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            urlopen=urllib.request.urlopen, ipaddress_module=ipaddress, datetime_module=datetime,
        )

def _do_wifi_info(self, payload: dict) -> None:
        _network_commands.wifi_info_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL,
            parse_wifi_output=_parse_wifi_output,
        )

def _do_netstat(self, payload: dict) -> None:
        _system_info_commands.netstat(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL,
        )

def _do_download_url(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        dest = (payload.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Downloads", os.path.basename(url.split("?")[0]) or "xgent_download.bin"
        )
        try:
            result = _run_without_console(subprocess.run,
                ["curl", "-L", "--max-time", "120", "-o", dest, url],
                capture_output=True, text=True, timeout=130,
            )
            ok = result.returncode == 0 and os.path.exists(dest)
            size = os.path.getsize(dest) if ok else 0
            text = f"⬇️ Скачано {size} байт → {dest}" if ok else f"⚠️ curl rc={result.returncode}"
        except Exception as exc:
            ok, size, text = False, 0, f"⚠️ {exc}"
        self._publish_response("download_url", {"type": "download_url", "device_id": DEVICE_ID,
                                                "ok": ok, "path": dest, "size": size, "text": text})

def _do_net_wifi_passwords(self, payload: dict) -> None:
        """Список сохранённых Wi-Fi сетей и паролей (netsh key=clear)."""
        return _network_commands.wifi_passwords(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL, parse_wifi_profile_names=_parse_wifi_profile_names,
            parse_key_content=_parse_key_content,
        )

def _do_net_bluetooth_list(self, payload: dict) -> None:
        """Список активных Bluetooth-устройств (Get-PnpDevice)."""
        return _network_commands.bluetooth_list(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL,
        )

def _do_net_ping(self, payload: dict) -> None:
        """Проверка доступности узла (Ping)."""
        return _network_commands.net_ping(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            run_without_console=_run_without_console, subprocess_module=subprocess, re_module=re,
        )

def _do_dns_resolution(self, payload: dict) -> None:
        return _network_commands.dns_resolution(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            resolve_dns=dns_diagnostics.resolve_dns, datetime_module=datetime,
        )

def _do_dns_settings(self, payload: dict) -> None:
        return _network_commands.dns_settings(
            payload,
            device_id=DEVICE_ID, publish_response=self._publish_response,
            read_settings=dns_settings.read_dns_settings, source=dns_settings.SOURCE,
            datetime_module=datetime,
        )

def _do_dns_comparison(self, payload: dict) -> None:
        return _network_commands.dns_comparison(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            compare_resolvers=dns_comparison.compare_resolvers, source=dns_comparison.SOURCE,
            datetime_module=datetime,
        )

def _do_dnssec_check(self, payload: dict) -> None:
        return _network_commands.dnssec_check(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            check_domain=dnssec_check.check_domain, source=dnssec_check.SOURCE,
            datetime_module=datetime,
        )

def _do_route_trace(self, payload: dict) -> None:
        return _network_commands.route_trace_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            trace_route=route_trace.trace_route, source=route_trace.WINDOWS_SOURCE,
            datetime_module=datetime,
        )

def _do_path_mtu(self, payload: dict) -> None:
        _network_commands.path_mtu_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            diagnose_path_mtu=path_mtu.diagnose_path_mtu, source=path_mtu.WINDOWS_SOURCE,
            datetime_module=datetime,
        )

def _do_captive_portal(self, payload: dict) -> None:
        _network_commands.captive_portal_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            detect_portal=captive_portal.detect_captive_portal, source=captive_portal.SOURCE,
            datetime_module=datetime,
        )

def _do_ipv6_connectivity(self, payload: dict) -> None:
        _network_commands.ipv6_connectivity_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            probe_ipv6=ipv6_connectivity.probe_ipv6, source=ipv6_connectivity.SOURCE,
            datetime_module=datetime,
        )

def _do_tls_certificate(self, payload: dict) -> None:
        _network_commands.tls_certificate_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            parse_target=tls_certificate.parse_target, probe_certificate=tls_certificate.probe_certificate,
            source=tls_certificate.SOURCE, datetime_module=datetime,
        )

def _do_latency_series(self, payload: dict) -> None:
        _network_commands.latency_series_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            valid_endpoint=latency_series._valid_endpoint, measure_latency=latency_series.measure_latency_series,
            source=latency_series.SOURCE, datetime_module=datetime,
        )

def _do_packet_loss_history(self, payload: dict) -> None:
        _network_commands.packet_loss_history_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            literal_address=packet_loss_history._literal_address,
            measure_packet_loss=packet_loss_history.measure_packet_loss,
            source=packet_loss_history.SOURCE, datetime_module=datetime,
        )

def _do_jitter_measurement(self, payload: dict) -> None:
        _network_commands.jitter_measurement_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            literal_address=packet_loss_history._literal_address,
            measure_jitter=jitter_measurement.measure_jitter,
            source=jitter_measurement.SOURCE, datetime_module=datetime,
        )

def _do_network_throughput(self, payload: dict) -> None:
        _network_commands.network_throughput_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            parse_endpoint=network_throughput.parse_endpoint,
            measure_throughput=network_throughput.measure_throughput,
            source=network_throughput.SOURCE, datetime_module=datetime,
        )

def _do_network_link_speed(self, payload: dict) -> None:
        _network_commands.network_link_speed_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_catalog=network_link_speed.collect_catalog,
            collect_link_speed=network_link_speed.collect_link_speed,
            source=network_link_speed.SOURCE,
        )

def _do_route_lookup(self, payload: dict) -> None:
        _network_commands.route_lookup_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            normalize_destination=route_lookup.normalize_destination, lookup_route=route_lookup.lookup_route,
            source=route_lookup.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_endpoint_batch(self, payload: dict) -> None:
        _network_commands.endpoint_batch_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            check_public_set=endpoint_batch.check_public_set,
            preset_id=endpoint_batch.PRESET_ID, source=endpoint_batch.WINDOWS_SOURCE,
            datetime_module=datetime,
        )

def _do_wifi_quality_history(self, payload: dict) -> None:
        _network_commands.wifi_quality_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_wifi_quality=wifi_quality.collect_wifi_quality,
            source=wifi_quality.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_wifi_channel_summary(self, payload: dict) -> None:
        _network_commands.wifi_channel_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_wifi_channel=wifi_channel.collect_wifi_channel,
            source=wifi_channel.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_wifi_connection_history(self, payload: dict) -> None:
        _network_commands.wifi_connection_history_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_history=wifi_connection_history.collect_wifi_connection_history,
            source=wifi_connection_history.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_proxy_status(self, payload: dict) -> None:
        _network_commands.proxy_status_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_proxy_status=proxy_status.collect_proxy_status,
            source=proxy_status.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_vpn_route_status(self, payload: dict) -> None:
        _network_commands.vpn_route_status_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_vpn_summary=vpn_route.collect_vpn_summary,
            source=vpn_route.WINDOWS_SOURCE, datetime_module=datetime,
        )

def _do_network_traffic(self, payload: dict) -> None:
        _network_commands.network_traffic_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            collect_catalog=network_traffic.collect_catalog,
            collect_interface_traffic=network_traffic.collect_interface_traffic,
            source=network_traffic.SOURCE, datetime_module=datetime,
        )

def _do_gateway_probe(self, payload: dict) -> None:
        _network_commands.gateway_probe_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            probe_gateway=gateway_probe.probe_gateway,
            source=gateway_probe.SOURCE, datetime_module=datetime,
        )
