"""Клиент XGENT для macOS (MacBook).

Подключается к публичному MQTT-брокеру, слушает команды бота
и выполняет их на этой машине.
"""
import argparse
import base64
import binascii
import datetime
import hashlib
import ipaddress
import io
import importlib.machinery
import importlib.util
import json
import logging
import locale
import math
import os
import plistlib
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import uuid
from urllib.parse import urlsplit
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Release build probe runs before importing device services or loading .env.
if len(sys.argv) > 1 and sys.argv[1] == "--command-approval-bundle-smoke":
    import command_approval_bundle_smoke  # noqa: F401 - exits after writing its report

from sleep_wake_history import parse_pmset_sleep_wake_log
from sysinfo_schedule import load_schedule, save_schedule
import camera_quality
import camera_devices
import media_temp
import display_contact_sheet
import window_capture
import dns_diagnostics
import dns_settings
import dns_comparison
import dnssec_check
import gateway_probe
import route_trace
import path_mtu
import captive_portal
import ipv6_connectivity
import tls_certificate
import service_probe
import latency_series
import packet_loss_history
import jitter_measurement
import network_throughput
import network_link_speed
import route_lookup
import endpoint_batch
import wifi_quality
import wifi_channel
import wifi_connection_history
import proxy_status
import vpn_route
import network_traffic
import interface_status_history
from network_traffic_schedule import load_schedule as load_network_traffic_schedule, save_schedule as save_network_traffic_schedule
from media_request_control import MEDIA_REQUEST_ACTIONS, MediaRequestRegistry
from mac_service_control import (
    LABELS as MAC_SERVICE_LABELS,
    app_install_root as mac_app_install_root,
    launch_agent_running,
    start_launch_agent,
    stop_launch_agent,
    write_launch_agent as write_mac_launch_agent,
)

_NUITKA_RUNTIME = bool("__compiled__" in globals() and not getattr(sys, "frozen", False))
_BUNDLED_RUNTIME = bool(getattr(sys, "frozen", False) or _NUITKA_RUNTIME)

MAX_FILE_PUT_BYTES = 30 * 1024 * 1024
MAX_FILE_PUT_B64_CHARS = 4 * ((MAX_FILE_PUT_BYTES + 2) // 3)
MAX_SCREENSHOT_BYTES = 30 * 1024 * 1024
_OPEN_URL_OPERATION_TTL_SEC = 600.0
_OPEN_URL_OPERATION_LIMIT = 256
_VOLUME_HISTORY_MIN_SPAN_SECONDS = 6 * 60 * 60
_VOLUME_HISTORY_RETENTION_SECONDS = 90 * 24 * 60 * 60
_VOLUME_HISTORY: dict[str, list[dict[str, float | int]]] = {}
_VOLUME_HISTORY_LOCK = threading.Lock()
_MEMORY_PRESSURE_SOURCE = "macos_memory_pressure_utility_free_percent"
_THERMAL_SOURCE = "macos_public_temperature_sensor_api_unavailable"
_BATTERY_HEALTH_SOURCE = "macos_system_profiler_battery_capacity"
_BATTERY_CYCLE_SOURCE = "macos_system_profiler_battery_cycle_count"
_SLEEP_WAKE_SOURCE = "macos_pmset_log"
_STARTUP_DURATION_SOURCE = "macos_boot_duration_api_unavailable"
_PENDING_RESTART_SOURCE = "macos_pending_restart_flag_unavailable"
APPLICATIONS_PATH = "/Applications"


def _read_startup_duration_snapshot() -> dict:
    """Fail explicitly when macOS exposes no reliable public boot-duration metric."""
    return {"state": "unsupported", "source": _STARTUP_DURATION_SOURCE, "duration_ms": None, "completed_at": None}


def _read_pending_restart_snapshot() -> dict:
    """Do not equate available macOS updates with a pending reboot requirement."""
    return {"state": "unsupported", "source": _PENDING_RESTART_SOURCE, "pending": None, "reasons": [], "completeness": "unknown"}


def _read_os_support_identity() -> dict:
    """Read only macOS version; Apple publishes update history, not fixed end dates."""
    try:
        release = platform.mac_ver()[0]
    except Exception:
        release = ""
    if isinstance(release, str) and re.fullmatch(r"\d{1,2}\.\d{1,2}(?:\.\d{1,2})?", release):
        return {"state": "supported", "family": "macos", "release": release, "edition": None}
    return {"state": "unknown", "family": "macos", "release": None, "edition": None}


def _read_hardware_inventory_snapshot() -> dict:
    """Read only public sysctl model/size keys; never query serials or UUIDs."""
    unknown = {
        "state": "unknown", "manufacturer": None, "model": None, "cpu": None,
        "architecture": None, "memory_gib": None, "graphics": [],
    }


    sysctl = shutil.which("sysctl")
    if not sysctl:
        return {**unknown, "state": "unsupported"}

    def read_value(key: str) -> str | None:
        try:
            result = subprocess.run(
                [sysctl, "-n", key], capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode != 0 or not isinstance(result.stdout, str) or len(result.stdout) > 256:
                return None
            value = result.stdout.strip()
            return value if value and not any(ord(char) < 32 for char in value) else None
        except Exception:
            return None

    model = read_value("hw.model")
    raw_memory = read_value("hw.memsize")
    raw_cpu = read_value("machdep.cpu.brand_string")
    memory_bytes = int(raw_memory) if raw_memory and raw_memory.isdigit() else None
    memory_gib = (memory_bytes + 2**29) // 2**30 if memory_bytes and memory_bytes > 0 else None
    cpu = raw_cpu[:120] if raw_cpu and len(raw_cpu) <= 120 else None
    architecture = platform.machine()
    if not isinstance(architecture, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,24}", architecture):
        architecture = None
    result = {
        "state": "supported", "manufacturer": "Apple", "model": model[:120] if model and len(model) <= 120 else None,
        "cpu": cpu, "architecture": architecture, "memory_gib": memory_gib, "graphics": [],
    }
    if not any(value is not None for key, value in result.items() if key not in {"state", "graphics"}):
        return unknown
    return result


def _read_filevault_status_snapshot() -> dict:
    """Read only the FileVault On/Off status; never list users or recovery keys."""
    source = "fdesetup_status"
    try:
        result = subprocess.run(
            ["fdesetup", "status"], capture_output=True, text=True,
            timeout=8, check=False, shell=False,
        )
    except FileNotFoundError:
        return {"state": "unsupported", "source": source, "data": {"enabled": None}}
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "source": source, "data": {"enabled": None}}
    if result.returncode != 0:
        return {"state": "unknown", "source": source, "data": {"enabled": None}}
    output = str(result.stdout or "").strip()
    if re.fullmatch(r"FileVault is On\.?", output, flags=re.IGNORECASE):
        return {"state": "supported", "source": source, "data": {"enabled": True}}
    if re.fullmatch(r"FileVault is Off\.?", output, flags=re.IGNORECASE):
        return {"state": "supported", "source": source, "data": {"enabled": False}}
    return {"state": "unknown", "source": source, "data": {"enabled": None}}


def _read_sip_status_snapshot() -> dict:
    """Read SIP state from csrutil without changing settings or exposing output."""
    source = "csrutil_status"
    try:
        result = subprocess.run(
            ["csrutil", "status"], capture_output=True, text=True,
            timeout=8, check=False, shell=False,
        )
    except FileNotFoundError:
        return {"state": "unsupported", "source": source, "data": {"enabled": None}}
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "source": source, "data": {"enabled": None}}
    if result.returncode == 0:
        output = str(result.stdout or "").strip()
        if re.fullmatch(r"System Integrity Protection status: enabled\.?", output, flags=re.IGNORECASE):
            return {"state": "supported", "source": source, "data": {"enabled": True}}
        if re.fullmatch(r"System Integrity Protection status: disabled\.?", output, flags=re.IGNORECASE):
            return {"state": "supported", "source": source, "data": {"enabled": False}}
    return {"state": "unknown", "source": source, "data": {"enabled": None}}


def _read_gatekeeper_status_snapshot() -> dict:
    """Read Gatekeeper assessment policy without changing it or exposing output."""
    source = "spctl_status"
    try:
        result = subprocess.run(
            ["spctl", "--status"], capture_output=True, text=True,
            timeout=8, check=False, shell=False,
        )
    except FileNotFoundError:
        return {"state": "unsupported", "source": source, "data": {"enabled": None}}
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "source": source, "data": {"enabled": None}}
    if result.returncode == 0:
        output = str(result.stdout or "").strip().casefold()
        if output == "assessments enabled":
            return {"state": "supported", "source": source, "data": {"enabled": True}}
        if output == "assessments disabled":
            return {"state": "supported", "source": source, "data": {"enabled": False}}
    return {"state": "unknown", "source": source, "data": {"enabled": None}}


def _read_driver_inventory_snapshot() -> dict:
    """macOS has no stable public inventory equivalent to signed Windows drivers."""
    return {"state": "unsupported", "drivers": []}


def _read_installed_app_inventory_snapshot() -> dict:
    """Read bounded top-level app bundle names/versions, not IDs, paths, or executables."""
    unknown = {"state": "unknown", "apps": []}
    try:
        with os.scandir(APPLICATIONS_PATH) as iterator:
            entries = sorted(iterator, key=lambda item: item.name.casefold())
        bundles = [entry for entry in entries if entry.name.endswith(".app") and entry.is_dir(follow_symlinks=False)]
        if len(bundles) > 500:
            return unknown
        apps = []
        for entry in bundles:
            name, version = entry.name[:-4], None
            try:
                info_path = Path(entry.path) / "Contents" / "Info.plist"
                with info_path.open("rb") as handle:
                    info = plistlib.load(handle)
                candidate = info.get("CFBundleName")
                if isinstance(candidate, str) and 0 < len(candidate) <= 160 and not any(ord(char) < 32 for char in candidate):
                    name = candidate.strip() or name
                candidate = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
                if isinstance(candidate, str) and len(candidate) <= 160 and not any(ord(char) < 32 for char in candidate):
                    version = candidate.strip() or None
            except (OSError, TypeError, ValueError, plistlib.InvalidFileException):
                # Unreadable bundle metadata is represented by its visible bundle name only.
                pass
            if not name or len(name) > 160 or any(ord(char) < 32 for char in name):
                return unknown
            apps.append({"name": name, "publisher": None, "version": version})
        return {"state": "supported", "apps": apps}
    except OSError:
        return {"state": "unsupported", "apps": []}


def _read_startup_inventory_snapshot() -> dict:
    """macOS login-item enumeration has no stable supported public source here."""
    return {"state": "unsupported", "entries": []}


def _read_service_inventory_snapshot() -> dict:
    """Do not infer a comparable service inventory from unstable macOS launchctl output."""
    return {"state": "unsupported", "services": []}


def _read_crash_summary_snapshot() -> dict:
    """Do not export private unified-log records without a stable safe aggregate source."""
    return {"state": "unsupported", "events": []}


def _read_smart_trend_snapshot() -> dict:
    """macOS diskutil exposes no stable SMART counter history interface."""
    return {"state": "unsupported", "disks": []}


def _volume_topology_device(mountpoint: str) -> int:
    """Return the local filesystem device number for one mounted path."""
    return os.stat(mountpoint).st_dev


def _read_volume_topology_snapshot() -> dict:
    """Group macOS mount aliases by st_dev without exporting /dev identifiers."""
    source = "macos_psutil_partitions_and_stat_device"
    unknown = {"state": "unknown", "source": source, "volumes": []}
    try:
        partitions = list(psutil.disk_partitions(all=False))[:64]
        grouped: dict[str, set[str]] = {}
        for part in partitions:
            mountpoint = getattr(part, "mountpoint", None)
            if not isinstance(mountpoint, str) or not mountpoint or len(mountpoint) > 512:
                continue
            try:
                device_number = _volume_topology_device(mountpoint)
            except Exception:
                continue
            if type(device_number) is not int or device_number < 0:
                continue
            volume_key = "vol-" + hashlib.sha256(str(device_number).encode("ascii")).hexdigest()[:16]
            grouped.setdefault(volume_key, set()).add(mountpoint)
        volumes = [
            {"volume_key": key, "mount_paths": sorted(paths)[:16], "state": "supported"}
            for key, paths in sorted(grouped.items()) if paths
        ][:64]
        return {**unknown, "state": "supported" if volumes else "unknown", "volumes": volumes}
    except Exception:
        log.info("macOS volume topology unavailable", exc_info=True)
        return unknown


def _read_clock_drift_snapshot() -> dict:
    """Estimate wall-clock steps against monotonic elapsed time over one second."""
    source = "wall_vs_monotonic_elapsed"
    try:
        wall_start = time.time_ns()
        monotonic_start = time.perf_counter_ns()
        time.sleep(1.0)
        wall_end = time.time_ns()
        monotonic_end = time.perf_counter_ns()
        wall_elapsed = wall_end - wall_start
        monotonic_elapsed = monotonic_end - monotonic_start
        sample_ms = round(monotonic_elapsed / 1_000_000)
        if (wall_elapsed <= 0 or monotonic_elapsed <= 0
                or not 500 <= sample_ms <= 5000):
            return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}
        drift_ms = round((wall_elapsed - monotonic_elapsed) / 1_000_000)
        return {"state": "supported", "source": source,
                "drift_ms": drift_ms, "sample_ms": sample_ms}
    except Exception:
        log.info("macOS local clock comparison unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}


def _read_sleep_wake_history_snapshot() -> dict:
    """Read a bounded pmset history and discard all non-event log details."""
    command = shutil.which("pmset")
    if not command:
        return {"state": "unsupported", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}
    try:
        completed = subprocess.run(
            [command, "-g", "log"], capture_output=True, text=True,
            timeout=12, check=False,
        )
        if (completed.returncode != 0 or not isinstance(completed.stdout, str)
                or len(completed.stdout) > 1024 * 1024):
            return {"state": "unknown", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}
        return parse_pmset_sleep_wake_log(completed.stdout)
    except Exception:
        log.info("macOS sleep/wake telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}


def _read_battery_health_snapshot() -> dict:
    """Read design/full capacity only when the native structured report exposes both."""
    command = shutil.which("system_profiler")
    if not command:
        return {"state": "unsupported", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}
    try:
        completed = subprocess.run(
            [command, "SPPowerDataType", "-json"], capture_output=True, text=True,
            timeout=20, check=False,
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}
        payload = json.loads(completed.stdout or "null")
        if not isinstance(payload, dict):
            return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}

        def capacity(row, *keys):
            for key in keys:
                value = row.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    number = value
                elif isinstance(value, str):
                    cleaned = value.replace(",", "")
                    match = re.fullmatch(r"\s*(\d+)\s*(?:mAh)?\s*", cleaned, re.IGNORECASE)
                    number = int(match.group(1)) if match else None
                else:
                    number = None
                if number is not None and 0 < number < 1_000_000:
                    return number
            return None

        rows = []
        sections = payload.get("SPPowerDataType")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                records = section.get("SPBatteryData")
                if isinstance(records, list):
                    rows.extend(row for row in records if isinstance(row, dict))
                elif isinstance(records, dict):
                    rows.append(records)
        batteries = []
        found_battery = bool(rows)
        for row in rows:
            capacity_row = row.get("sppower_battery_health_data")
            if not isinstance(capacity_row, dict):
                capacity_row = row
                has_direct_capacity = any(key in row for key in (
                    "design_capacity_milliamp_hours", "sppower_battery_health_design_capacity",
                    "full_charge_capacity_milliamp_hours", "sppower_battery_health_full_charge_capacity",
                ))
                if not has_direct_capacity:
                    continue
            design = capacity(capacity_row, "design_capacity_milliamp_hours", "sppower_battery_health_design_capacity")
            full = capacity(capacity_row, "full_charge_capacity_milliamp_hours", "sppower_battery_health_full_charge_capacity")
            if design is None and full is None:
                continue
            found_battery = True
            if design is None or full is None:
                continue
            health = full * 100.0 / design
            if not math.isfinite(health) or not 1.0 <= health <= 150.0:
                continue
            health_info = row.get("sppower_battery_health")
            if not isinstance(health_info, dict):
                health_info = capacity_row.get("sppower_battery_health")
            condition = health_info.get("sppower_battery_health_condition") if isinstance(health_info, dict) else None
            if condition not in {"Normal", "Service Recommended", "Replace Soon", "Replace Now", "Fair", "Poor"}:
                condition = "Unknown"
            batteries.append({
                "label": "Internal battery",
                "design_capacity_mah": design,
                "full_charge_capacity_mah": full,
                "health_percent": round(health, 1),
                "condition": condition,
                "quality": "system_profiler",
            })
        if batteries:
            state = "supported"
        else:
            state = "unknown" if found_battery else "unsupported"
        return {"state": state, "source": _BATTERY_HEALTH_SOURCE, "batteries": batteries}
    except Exception:
        return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}


def _read_battery_cycle_snapshot() -> dict:
    """Read the cycle count only from macOS's structured System Information data."""
    command = shutil.which("system_profiler")
    if not command:
        return {"state": "unsupported", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
    try:
        completed = subprocess.run(
            [command, "SPPowerDataType", "-json"], capture_output=True, text=True,
            timeout=20, check=False,
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        payload = json.loads(completed.stdout or "null")
        if not isinstance(payload, dict):
            return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        sections = payload.get("SPPowerDataType")
        if not isinstance(sections, list):
            return {"state": "unsupported", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        rows = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            records = section.get("SPBatteryData")
            if isinstance(records, list):
                rows.extend(row for row in records if isinstance(row, dict))
            elif isinstance(records, dict):
                rows.append(records)
        if not rows:
            return {"state": "unsupported", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        batteries = []
        saw_cycle_field = False
        for row in rows[:8]:
            health_data = row.get("sppower_battery_health_data")
            if not isinstance(health_data, dict) or "sppower_battery_cycle_count" not in health_data:
                continue
            saw_cycle_field = True
            value = health_data.get("sppower_battery_cycle_count")
            if isinstance(value, int) and not isinstance(value, bool):
                count = value
            elif isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
                count = int(value.strip())
            else:
                continue
            if not 0 <= count <= 1_000_000:
                continue
            batteries.append({
                "label": "Internal battery", "cycle_count": count, "quality": "system_profiler",
            })
        state = "supported" if batteries else "unknown"
        return {"state": state, "source": _BATTERY_CYCLE_SOURCE, "batteries": batteries}
    except Exception:
        return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}


def _read_thermal_snapshot() -> dict:
    """Do not invent sensor values where macOS exposes no supported public adapter in this agent."""
    return {"state": "unsupported", "source": _THERMAL_SOURCE, "sensors": []}


def _read_memory_pressure_sample() -> dict:
    """Read the native memory_pressure utility's free-percent metric, not Activity Monitor's graph."""
    command = shutil.which("memory_pressure")
    if not command:
        return {"state": "unsupported", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
    try:
        completed = subprocess.run(
            [command, "-Q"], capture_output=True, text=True, timeout=5, check=False,
        )
        if completed.returncode != 0:
            return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        match = re.search(
            r"System-wide memory free percentage:\s*([0-9]+(?:\.[0-9]+)?)%",
            completed.stdout or "",
        )
        if not match:
            return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        value = float(match.group(1))
        if not math.isfinite(value) or not 0 <= value <= 100:
            return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        return {"state": "supported", "available_percent": value, "source": _MEMORY_PRESSURE_SOURCE}
    except Exception:
        return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}


def _memory_pressure_history_sample(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("source") != _MEMORY_PRESSURE_SOURCE:
        return {"state": "unknown", "available_percent": None}
    if raw.get("state") != "supported":
        return {"state": raw.get("state") if raw.get("state") in {"unsupported", "unknown"} else "unknown",
                "available_percent": None}
    value = raw.get("available_percent")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        return {"state": "unknown", "available_percent": None}
    return {"state": "supported", "available_percent": round(float(value), 1)}


def _volume_history_measurement(device: str, mountpoint: str, total: int, used: int, now: float) -> dict:
    """Compare a live filesystem reading with bounded in-process observations."""
    key = f"{device[:256]}\0{mountpoint[:512]}"
    with _VOLUME_HISTORY_LOCK:
        if key not in _VOLUME_HISTORY and len(_VOLUME_HISTORY) >= 256:
            oldest_key = min(
                _VOLUME_HISTORY,
                key=lambda item: max((float(sample.get("at", 0)) for sample in _VOLUME_HISTORY[item]), default=0),
            )
            _VOLUME_HISTORY.pop(oldest_key, None)
        history = _VOLUME_HISTORY.setdefault(key, [])
        history[:] = [
            row for row in history
            if isinstance(row, dict) and 0 <= now - float(row.get("at", -1)) <= _VOLUME_HISTORY_RETENTION_SECONDS
        ]
        previous_capacity = any(
            row.get("total") != total and now - float(row.get("at", now)) >= _VOLUME_HISTORY_MIN_SPAN_SECONDS
            for row in history
        )
        baseline = max(
            (row for row in history
             if row.get("total") == total
             and _VOLUME_HISTORY_MIN_SPAN_SECONDS <= now - float(row.get("at", now)) <= _VOLUME_HISTORY_RETENTION_SECONDS),
            key=lambda row: float(row.get("at", 0)), default=None,
        )
        if not history or now - float(history[-1].get("at", now)) >= 300:
            history.append({"at": now, "total": total, "used": used})
            del history[:-128]
        row = {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": total - used,
            "used_percent": round(used * 100.0 / total, 1),
            "trend_state": "capacity_changed" if previous_capacity and baseline is None else "insufficient_history",
            "observed_span_seconds": None,
            "delta_used_bytes": None,
            "used_bytes_per_hour": None,
            "estimated_hours_to_full": None,
            "history_scope": "agent_process",
        }
        if baseline is not None:
            elapsed = now - float(baseline["at"])
            delta = used - int(baseline["used"])
            row["observed_span_seconds"] = int(elapsed)
            row["delta_used_bytes"] = delta
            if delta > 0:
                rate = delta * 3600.0 / elapsed
                row.update(
                    trend_state="increasing",
                    used_bytes_per_hour=round(rate),
                    estimated_hours_to_full=round(max(0, total - used) / rate, 1) if rate > 0 else None,
                )
            elif delta < 0:
                row["trend_state"] = "decreasing"
                row["used_bytes_per_hour"] = round(delta * 3600.0 / elapsed)
            else:
                row["trend_state"] = "stable"
                row["used_bytes_per_hour"] = 0
        return row


def _screenshot_session_available() -> bool:
    """Console ownership is a session gate, never evidence of TCC permission."""
    try:
        return os.stat("/dev/console").st_uid == os.getuid() and os.getuid() != 0
    except (OSError, AttributeError):
        return False


def _validated_open_url(payload: dict) -> tuple[str, int]:
    url = payload.get("url")
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("URL is empty or too long")
    if any(char in url for char in "\r\n\t\x00"):
        raise ValueError("URL contains control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
        raise ValueError("Only HTTP(S) URLs with a host are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not allowed")
    if any(char.isspace() for char in parsed.netloc):
        raise ValueError("Whitespace in URL host is not allowed")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port is invalid")
    count = payload.get("count", 1)
    if type(count) is not int or count not in {1, 2, 3, 5}:
        raise ValueError("Open count must be one of 1, 2, 3, or 5")
    return url, count


def _decode_file_put_payload(payload: dict) -> bytes:
    """Decode a bounded, canonical base64 file payload before touching disk."""
    if "b64" in payload:
        encoded = payload["b64"]
    elif "data" in payload:
        encoded = payload["data"]
    else:
        raise ValueError("В запросе отсутствуют данные файла.")
    if not isinstance(encoded, str):
        raise ValueError("Данные файла должны быть строкой base64.")
    if len(encoded) > MAX_FILE_PUT_B64_CHARS:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Повреждены данные base64.") from exc
    if len(content) > MAX_FILE_PUT_BYTES:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    return content


def _write_file_put_atomic(path: str, content: bytes) -> None:
    """Replace the destination only after the complete temporary file is written."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".xider-upload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary_path, path)
        temporary_path = ""
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

# The native macOS demo screen runs in a short-lived child process. Dispatch it
# before taking the agent's singleton lock or loading MQTT/update state; the
# frozen PyInstaller build re-enters this executable with the same flag.
if __name__ == "__main__" and sys.argv[1:] == ["--xider-fake-update"]:
    try:
        from fake_update_screen import run_fake_update_demo

        run_fake_update_demo()
    except Exception as exc:
        sys.stderr.write(f"XIDER demo screen failed: {exc}\n")
        raise SystemExit(1) from exc
    raise SystemExit(0)

if __name__ == "__main__" and sys.argv[1:] == ["--location-setup"]:
    from location_adapter import visible_local_setup
    visible_local_setup()
    raise SystemExit(0)

# Claim the normal process lock before examining a crash journal. A manual
# second launch must never roll back files while the live updater is replacing
# them. This prelude intentionally uses only the Python standard library.
_bootstrap_lock_file = None


def _claim_bootstrap_lock() -> bool:
    """Гарантирует, что запущен ровно один экземпляр агента на этой машине."""
    global _bootstrap_lock_file
    lock_path = os.path.join(tempfile.gettempdir(), "xgent_mcs.lock")
    try:
        _bootstrap_lock_file = open(lock_path, "a+")
        if fcntl:
            fcntl.flock(_bootstrap_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _bootstrap_lock_file.seek(0)
        _bootstrap_lock_file.truncate()
        _bootstrap_lock_file.write(str(os.getpid()) + "\n")
        _bootstrap_lock_file.flush()
        return True
    except (IOError, OSError):
        if _bootstrap_lock_file is not None:
            _bootstrap_lock_file.close()
        _bootstrap_lock_file = None
        return False


if (__name__ == "__main__" and not (len(sys.argv) > 1 and sys.argv[1] == "--service")
        and not _claim_bootstrap_lock()):
    sys.stderr.write("FATAL: XGENT-MCS уже запущен на этой машине! Второй экземпляр остановлен.\n")
    raise SystemExit(1)

from update_package import (
    DependencyUpdateRequired,
    UPDATE_STATE_NAME,
    download_verified_source_archive,
    extract_agent_files,
    install_agent_files,
    mark_agent_update_healthy,
    prepare_agent_update_start,
    rollback_unhealthy_agent,
    validate_source_update_requirements,
    read_agent_update_result,
    update_operation_id,
)
from release_signature import trusted_release_keys_ready

EARLY_UPDATE_STATE = "none"
if __name__ == "__main__" and not _BUNDLED_RUNTIME:
    EARLY_UPDATE_STATE = prepare_agent_update_start(
        Path.home() / ".xgent" / UPDATE_STATE_NAME,
        Path(__file__).resolve().parent,
    )
    if EARLY_UPDATE_STATE in {"recovered", "rolled_back"}:
        message = (
            "XIDER: прерванная установка восстановлена; перезапускаю предыдущую версию.\n"
            if EARLY_UPDATE_STATE == "recovered"
            else "XIDER: новая версия не прошла стартовую попытку; восстановлены предыдущие файлы.\n"
        )
        sys.stderr.write(message)
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

from guardian_recovery import activate_recovery_package, stage_recovery_package, validate_recovery_install

import paho.mqtt.client as mqtt
import psutil

from config import (
    CONFIG_DIR,
    set_guardian_desired_running,
    set_guardian_startup_enabled,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    HEARTBEAT_INTERVAL,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_TRANSPORT,
    MQTT_WS_PATH,
    MQTT_USERNAME,
    PLATFORM,
    VERSION,
    write_runtime_health,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload
log = logging.getLogger("xgent.mcs")
MEDIA_REQUESTS = MediaRequestRegistry()

_lock_file = None


def acquire_instance_lock() -> bool:
    """Гарантирует, что запущен ровно один экземпляр агента на машине."""
    global _lock_file
    if _bootstrap_lock_file is not None:
        _lock_file = _bootstrap_lock_file
        return True
    lock_path = os.path.join(tempfile.gettempdir(), "xgent_mcs.lock")
    try:
        _lock_file = open(lock_path, "a+")
        if fcntl:
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.seek(0)
        _lock_file.truncate()
        _lock_file.write(str(os.getpid()) + "\n")
        _lock_file.flush()
        return True
    except (IOError, OSError):
        return False

SUPPORTED_COMMANDS = (
    "open_url", "notify", "sound", "status_request", "screenshot", "media_temp_cleanup", "media_history", "webcam", "camera_series", "media_cancel", "camera_catalog", "camera_quality", "microphone_catalog", "microphone_level", "microphone_balance", "audio_output_catalog", "audio_output_switch", "microphone_test", "microphone_preview",
    "sysinfo", "sysinfo_interval", "network_traffic_interval", "cpu_history", "memory_history", "memory_pressure_history", "thermal_snapshot", "battery_health", "battery_cycles", "energy_usage", "sleep_wake_history", "startup_duration", "pending_restart", "os_support", "filevault_status", "sip_status", "gatekeeper_status", "hardware_inventory", "driver_inventory", "installed_app_inventory", "startup_inventory", "service_inventory", "crash_summary", "smart_trend", "volume_topology", "clock_drift", "volume_history", "processes", "battery", "network", "services", "clipboard",
    "clipboard_set", "kill_process", "disks", "volume_set",
    "mic", "shell", "shell_cancel", "open_app", "capabilities", "permission_settings", "lock", "volume_toggle",
    "display_catalog", "display_resolution", "display_contact_sheet", "display_screenshot", "display_region_screenshot", "window_catalog", "window_screenshot",
    "power", "stop", "agent_logs",
    "dir_list", "file_get", "file_put", "file_del", "find_file", "path_open",
    "ext_ip", "geo_location", "screen_off", "screensaver_on", "wallpaper_set",
    "msgbox_spam", "type_text", "hotkey",
    "proc_kill_name", "wifi_info", "wifi_quality_history", "wifi_channel_summary", "wifi_connection_history", "proxy_status", "vpn_route_status", "network_traffic", "interface_status_history", "usb_devices", "startup_list",
    "env_get", "netstat", "download_url",
    "prank_screamer", "prank_rickroll", "prank_matrix", "prank_siren",
    "prank_shout_tts", "prank_swap_mouse", "prank_crazy_cursor", "prank_hide_desktop",
    "prank_dancing_windows", "prank_black_screen", "prank_random_site",
    "display_brightness", "display_night_light", "display_rotate",
    "net_wifi_passwords", "net_bluetooth_list", "storage_smart",
    "sys_installed_apps", "sys_history_cmd",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep", "wake", "autorun_status", "autorun_enable", "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime", "sys_clean_temp", "net_ping", "dns_resolution", "dns_settings", "dns_comparison", "dnssec_check", "gateway_probe", "route_trace", "path_mtu", "captive_portal", "ipv6_connectivity", "tls_certificate", "service_probe", "latency_series", "packet_loss_history", "network_jitter", "network_throughput", "network_link_speed", "route_lookup", "endpoint_batch",
    # Расширенная матрица приколов
    "prank_bsod", "prank_fake_update", "prank_toast_spam", "prank_keyboard_disco",
    "prank_beep_morse", "prank_open_notepad_type", "prank_hacker_typer",
    "prank_sound_spooky", "prank_sound_fart", "prank_minimize_all",
    "prank_open_calc_spam", "prank_invert_screen", "prank_slow_mouse",
    "prank_random_clicks", "prank_paste_clipboard_spam", "prank_type_reversed",
    "prank_volume_jump", "prank_say_whisper", "prank_fake_virus", "prank_open_cd",
    "prank_change_wallpaper", "prank_restore_wallpaper", "prank_speak_time",
    "prank_rickroll_terminal", "prank_screen_off_brief", "prank_alert_loop",
    "prank_open_browser_memes", "prank_glitch_cursor", "prank_shake_window",
    # Новые мега-приколы и троллинг
    "prank_meme_wallpaper", "prank_caps_disco", "prank_cursor_circle",
    "prank_fake_error_spam", "prank_fake_delete_sys32", "prank_ghost_typer",
    "prank_fbi_lock", "prank_cat_invaders", "prank_fake_ransom_cats",
    "prank_nyan_stream", "prank_random_beeps", "prank_confetti_winner",
    "prank_low_battery_fake", "prank_earthquake", "prank_laugh_track",
    "prank_stop_all",
    "agent_update", "uninstall_agent",
)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _resolve_command(name: str, fallback_paths: tuple[str, ...]) -> str | None:
    return shutil.which(name) or next((path for path in fallback_paths if Path(path).is_file()), None)


def _command_available(name: str, fallback_paths: tuple[str, ...]) -> bool:
    return bool(_resolve_command(name, fallback_paths))


_COMMAND_MODULE_LOADER = None


def _same_absolute_path(left, right) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def _load_trusted_command_module_loader():
    """Bootstrap the one shared loader without importing it by a shadowable name."""
    global _COMMAND_MODULE_LOADER
    if _COMMAND_MODULE_LOADER is not None:
        return _COMMAND_MODULE_LOADER
    module_name = "command_module_loader"
    try:
        if _NUITKA_RUNTIME:
            import command_module_loader as module
            runtime_info = globals().get("__compiled__")
            loader_info = getattr(module, "__compiled__", None)
            runtime_dir = getattr(runtime_info, "containing_dir", None)
            loader_dir = getattr(loader_info, "containing_dir", None)
            if (
                loader_info is None
                or not isinstance(runtime_dir, (str, os.PathLike))
                or not isinstance(loader_dir, (str, os.PathLike))
                or not _same_absolute_path(runtime_dir, loader_dir)
            ):
                raise ImportError("Shared command loader is not compiled into the active Nuitka bundle")
            _COMMAND_MODULE_LOADER = module
            return module
        if _BUNDLED_RUNTIME:
            runtime = sys.modules.get("pyimod02_importers")
            finder_type = getattr(runtime, "PyiFrozenFinder", None)
            loader_type = getattr(runtime, "PyiFrozenLoader", None)
            archive = getattr(runtime, "pyz_archive", None)
            meipass = getattr(sys, "_MEIPASS", None)
            if (
                not isinstance(finder_type, type)
                or finder_type.__name__ != "PyiFrozenFinder"
                or finder_type.__module__ != "pyimod02_importers"
                or not isinstance(loader_type, type)
                or loader_type.__name__ != "PyiFrozenLoader"
                or loader_type.__module__ != "pyimod02_importers"
                or archive is None
                or not isinstance(getattr(archive, "toc", None), dict)
                or not isinstance(meipass, (str, os.PathLike))
                or not os.fspath(meipass)
            ):
                raise ImportError("Trusted PyInstaller loader is unavailable")
            finder = finder_type(os.fspath(meipass))
            spec = finder.find_spec(module_name)
            loader = getattr(spec, "loader", None)
            expected_origin = Path(meipass) / f"{module_name}.py"
            if (
                type(finder) is not finder_type
                or getattr(finder, "_pyz_archive", None) is not archive
                or not _same_absolute_path(getattr(finder, "_path", ""), meipass)
                or getattr(spec, "name", None) != module_name
                or type(loader) is not loader_type
                or getattr(loader, "name", None) != module_name
                or getattr(loader, "_pyz_archive", None) is not archive
                or getattr(loader, "_pyz_entry_name", None) != module_name
                or getattr(loader, "_is_package", None) is not False
                or module_name not in archive.toc
                or not getattr(spec, "origin", None)
                or not getattr(loader, "path", None)
                or not _same_absolute_path(loader.path, expected_origin)
                or not _same_absolute_path(spec.origin, expected_origin)
                or getattr(spec, "submodule_search_locations", None) is not None
            ):
                raise ImportError("Shared command loader is not bundled in the active PYZ")
        else:
            root = Path(__file__).resolve().parent
            expected = root / f"{module_name}.py"
            if expected.is_symlink() or not expected.is_file():
                raise ImportError("Shared command loader is missing")
            if not _same_absolute_path(expected.resolve(strict=True), expected):
                raise ImportError("Shared command loader escaped its repository directory")
            spec = importlib.util.spec_from_file_location(module_name, expected)
            if (
                spec is None
                or spec.name != module_name
                or type(spec.loader) is not importlib.machinery.SourceFileLoader
                or not spec.origin
                or not _same_absolute_path(spec.origin, expected)
                or not _same_absolute_path(spec.loader.get_filename(module_name), expected)
            ):
                raise ImportError("Shared command loader has an unexpected source spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMMAND_MODULE_LOADER = module
        return module
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError("Shared command loader could not be trusted") from exc


def _load_command_approval_component(module_name: str):
    return _load_trusted_command_module_loader().load_component(
        module_name,
        source_root=Path(__file__).resolve().parent,
        frozen=_BUNDLED_RUNTIME,
    )


def _shell_approval_runtime(client):
    """Load the separate Ed25519 verifier and durable replay store lazily."""
    try:
        signing = _load_command_approval_component("command_signing")
        replay = _load_command_approval_component("command_replay")
        _load_command_approval_component("shell_execution")
        ledger = getattr(client, "_command_replay_ledger", None)
        if ledger is None:
            ledger = replay.CommandReplayLedger()
            client._command_replay_ledger = ledger
        return signing, ledger
    except Exception:
        return None, None


def _command_approval_protocol_enabled(client) -> bool:
    """Advertise schema 1 only when its public keyset and replay store are usable."""
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        return signing.trusted_command_keys_ready() and ledger.ensure_available(now=int(time.time()))
    except Exception:
        return False


def _shell_runner_diagnostic(client) -> dict:
    """Report runner prerequisites from local metadata only; do not execute shell commands."""
    protocol_state = "ready" if _command_approval_protocol_enabled(client) else "unavailable"
    try:
        execution = _load_command_approval_component("shell_execution")
        executor_state = "available" if callable(getattr(execution, "execute_shell", None)) else "unavailable"
    except Exception:
        execution = None
        executor_state = "unavailable"
    if PLATFORM == "macOS":
        runner_path = "/bin/sh"
    else:
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        expected = str(Path(system_root) / "System32" / "cmd.exe")
        configured = os.environ.get("COMSPEC")
        runner_path = expected if not configured or os.path.normcase(os.path.abspath(configured)) == os.path.normcase(os.path.abspath(expected)) else ""
    if execution is None or not callable(getattr(execution, "inspect_shell_runner", None)):
        runner = {"runner_state": "unknown", "permission_state": "unknown"}
    else:
        runner = execution.inspect_shell_runner(runner_path)
    return {
        "schema": 1,
        "protocol_state": protocol_state,
        "executor_state": executor_state,
        "runner_state": runner["runner_state"],
        "permission_state": runner["permission_state"],
    }


def _reserve_shell_approval(client, payload: dict) -> bool:
    """Verify exact command bytes and persist the signed request ID before execution."""
    if type(payload) is not dict:
        return False
    command = payload.get("command")
    envelope = payload.get("approval")
    if not isinstance(command, str) or type(envelope) is not dict:
        return False
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        now = int(time.time())
        verified = signing.verify_approved_command(
            envelope,
            command=command,
            expected_device_id=DEVICE_ID,
            now=now,
        )
        if verified is None:
            return False
        return ledger.reserve(verified["request_id"], verified["expires_at"], now=now)
    except Exception:
        return False


def _detect_feature_status() -> dict[str, str]:
    """Report prerequisites only; never claim a live hardware/permission test."""
    statuses = {
        "shell": "supported",
        "open_app": "supported",
        "external_ip_watch": "supported",
        "geolocation": "approximate",
        "screenshot": (
            "permission_unverified"
            if _command_available("screencapture", ("/usr/sbin/screencapture",))
            else "unavailable"
        ),
        "webcam": "permission_unverified" if _module_available("cv2") else "dependency_missing",
        "microphone": (
            "permission_unverified"
            if _module_available("sounddevice") and _module_available("soundfile")
            else "dependency_missing"
        ),
        "clipboard": (
            "device_unverified"
            if _command_available("pbpaste", ("/usr/bin/pbpaste",))
            and _command_available("pbcopy", ("/usr/bin/pbcopy",))
            else "unavailable"
        ),
    }
    try:
        statuses["battery"] = "supported" if psutil.sensors_battery() is not None else "unavailable"
    except Exception:
        statuses["battery"] = "unknown"
    return statuses


def _capability_snapshot(feature_status: dict[str, str]) -> dict:
    """Passive diagnostics only: prerequisites are never an OS permission grant."""
    items = []
    for capability_id, legacy_id, settings_uri in (
        ("screen_recording", "screenshot", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
        ("camera", "webcam", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"),
        ("microphone", "microphone", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"),
        ("location", "geolocation", "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices"),
        ("accessibility", None, "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"),
        ("file_access", None, "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"),
    ):
        prerequisite = feature_status.get(legacy_id) if legacy_id else None
        state = "needs_live_test"
        detail = "Permission cannot be queried directly; only an owner-authorized trial action confirms access."
        if prerequisite in {"dependency_missing", "unavailable"}:
            state = "unavailable"
            detail = "Required local tool or library is unavailable; OS permission was not queried."
        elif legacy_id and prerequisite not in {"permission_unverified", "device_unverified", "approximate"}:
            state = "unknown"
            detail = "Diagnostic prerequisites could not be determined; no permission grant inferred."
        if capability_id == "location":
            from location_adapter import location_capability
            state, detail = location_capability()
        elif capability_id == "accessibility":
            detail = ("TCC Accessibility status not queried; owner must allow the actual app on the device. "
                      "Only an authorized UI trial confirms access.")
        elif capability_id == "file_access":
            detail = ("TCC Files and Folders is path-specific; Full Disk Access is not inferred. "
                      "Only an authorized trial for the chosen path confirms access.")
        items.append({"id": capability_id, "state": state, "detail": detail, "settings_uri": settings_uri})
    return {"schema": 1, "platform": PLATFORM,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "items": items}


def _humanize_seconds(secs):
    if secs is None: return None
    try: secs = int(secs)
    except: return None
    if secs < 0: return None
    hours, remainder = divmod(secs, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}ч {minutes}мин" if hours else f"{minutes}мин"

def _format_battery(battery):
    if battery is None:
        return {"available": False}
    secsleft = battery.secsleft
    if secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        secsleft = None
    plugged = bool(battery.power_plugged)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "power_plugged": plugged,
        "secsleft": int(secsleft) if secsleft is not None else None,
        "time_left": _humanize_seconds(secsleft),
        "state": "charging" if plugged else "on_battery",
    }

def _format_network(addrs, stats, io_stats):
    interfaces = []
    for name in sorted(addrs.keys()):
        nic_addrs = addrs[name]
        nic_stats = stats.get(name)
        ipv4 = ipv6 = mac = None
        for a in nic_addrs:
            if a.family == socket.AF_INET: ipv4 = a.address
            elif a.family == socket.AF_INET6: ipv6 = a.address.split("%")[0]
            elif a.family == psutil.AF_LINK: mac = a.address
        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
            "up": bool(nic_stats.isup) if nic_stats else False,
            "speed_mbps": nic_stats.speed if nic_stats else 0,
        })
    totals = {"bytes_sent": 0, "bytes_recv": 0}
    for counters in (io_stats or {}).values():
        totals["bytes_sent"] += counters.bytes_sent
        totals["bytes_recv"] += counters.bytes_recv
    return {"interfaces": interfaces, "totals": totals}

def _format_services():
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True)
        lines = out.strip().split("\n")[1:]
        total = len(lines)
        running = sum(1 for line in lines if not line.startswith("-"))
        return {"total": total, "running": running, "stopped": total - running, "failed_auto_start": []}
    except Exception:
        return {"total": 0, "running": 0, "stopped": 0, "failed_auto_start": []}

def _format_processes(procs, top_n=5):
    items = []
    for proc in procs:
        try:
            info = proc.info
            name = info.get("name")
            mem = info.get("memory_percent") or 0
            items.append((name, mem, info.get("pid")))
        except Exception: pass
    items.sort(key=lambda x: (x[1] or 0), reverse=True)
    top = items[:top_n]
    lines = [f"{n}: {m:.1f}%" for n, m, _ in top if n]
    structured = [{"name": n, "pid": p, "mem_percent": round(m, 1)} for n, m, p in top if n]
    return {"lines": lines, "top": structured}


def _primary_mac():
    """Первый валидный MAC из сетевых интерфейсов (для Wake-on-LAN)."""
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac = (addr.address or "").strip()
                if mac and mac.lower().replace(":", "-") != "00-00-00-00-00-00":
                    return mac
    return None


def _format_disks(partitions):
    """Список дисков с размерами."""
    disks = []
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent": round(usage.percent, 1),
        })
    return disks


def _regional_settings_snapshot(request_id: object = None) -> dict:
    """Read timezone/locale once without changing the process or OS settings."""
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    timezone_data = {"state": "unknown", "source": "python_localtime", "name": None, "utc_offset_minutes": None}
    locale_data = {"state": "unknown", "source": "python_runtime_locale", "name": None}
    try:
        local_now = datetime.datetime.now().astimezone()
        name = local_now.tzname()
        offset = local_now.utcoffset()
        offset_minutes = int(offset.total_seconds() // 60) if offset is not None else None
        if isinstance(name, str) and name.strip():
            timezone_data["name"] = name.strip()[:128]
        if offset_minutes is not None and -840 <= offset_minutes <= 840:
            timezone_data["utc_offset_minutes"] = offset_minutes
        if timezone_data["name"] is not None or timezone_data["utc_offset_minutes"] is not None:
            timezone_data["state"] = "supported"
    except (OSError, OverflowError, ValueError):
        pass
    try:
        language, _encoding = locale.getlocale()
        if isinstance(language, str) and language.strip():
            locale_data.update(state="supported", name=language.strip()[:128])
    except (TypeError, ValueError, locale.Error):
        pass
    return {
        "schema": 1,
        "feature_id": "D01-F23",
        "device_id": DEVICE_ID,
        "request_id": request_id if isinstance(request_id, str) and re.fullmatch(r"[0-9a-fA-F]{8,64}", request_id) else None,
        "source": "local_os_runtime_apis",
        "state": "supported" if "supported" in {timezone_data["state"], locale_data["state"]} else "unknown",
        "checked_at": checked_at,
        "data": {"timezone": timezone_data, "locale": locale_data},
    }

class XgentClient:
    def __init__(self, on_stop_requested=None) -> None:
        self.on_stop_requested = on_stop_requested
        self._running = threading.Event()
        self._running.set()
        self._update_health = threading.Event()
        self._update_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._update_cancelled = threading.Event()
        self._mqtt_connected = False
        self._pending_command_subacks: set[int] = set()
        self._command_subscriptions_failed = False
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xgent-mcs-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
            transport=MQTT_TRANSPORT,
        )
        if MQTT_TRANSPORT == "websockets":
            self._client.ws_set_options(path=MQTT_WS_PATH)
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="xgent-heartbeat", daemon=True
        )
        self._sysinfo_schedule_wake = threading.Event()
        self._sysinfo_schedule = threading.Thread(
            target=self._sysinfo_schedule_loop, name="xgent-sysinfo-schedule", daemon=True,
        )
        self._network_traffic_schedule_wake = threading.Event()
        self._network_traffic_schedule = threading.Thread(
            target=self._network_traffic_schedule_loop, name="xgent-network-traffic-schedule", daemon=True,
        )
        self._interface_status_history_wake = threading.Event()
        self._interface_status_history_thread = threading.Thread(
            target=self._interface_status_history_loop, name="xgent-interface-status-history", daemon=True,
        )
        self._standby = False
        self._command_context = threading.local()
        self._active_prank_cancels: dict[str, object] = {}
        self._active_prank_cancels_lock = threading.Lock()
        self._open_url_operations: dict[str, dict] = {}
        self._open_url_operations_lock = threading.Lock()
        self._handlers = {
            "open_url": self._do_open_url,
            "notify": self._do_notify,
            "sound": self._do_sound,
            "status_request": self._do_status_request,
            "screenshot": self._do_screenshot,
            "display_catalog": self._do_display_catalog,
            "display_resolution": self._do_display_resolution,
            "display_contact_sheet": self._do_display_contact_sheet,
            "display_screenshot": self._do_display_screenshot,
            "display_region_screenshot": self._do_display_region_screenshot,
            "window_catalog": self._do_window_catalog,
            "window_screenshot": self._do_window_screenshot,
            "media_temp_cleanup": self._do_media_temp_cleanup,
            "media_history": self._do_media_history,
            "webcam": self._do_webcam,
            "camera_series": self._do_camera_series,
            "media_cancel": self._do_media_cancel,
            "shell_cancel": self._do_shell_cancel,
            "camera_catalog": self._do_camera_catalog,
            "microphone_catalog": self._do_microphone_catalog,
            "microphone_level": self._do_microphone_level,
            "microphone_balance": self._do_microphone_balance,
            "audio_output_catalog": self._do_audio_output_catalog,
            "audio_output_switch": self._do_audio_output_switch,
            "microphone_test": self._do_microphone_test,
            "microphone_preview": self._do_microphone_preview,
            "camera_quality": self._do_camera_quality,
            "sysinfo": self._do_sysinfo,
            "sysinfo_interval": self._do_sysinfo_interval,
            "network_traffic_interval": self._do_network_traffic_interval,
            "cpu_history": self._do_cpu_history,
            "memory_history": self._do_memory_history,
            "memory_pressure_history": self._do_memory_pressure_history,
            "thermal_snapshot": self._do_thermal_snapshot,
            "battery_health": self._do_battery_health,
            "battery_cycles": self._do_battery_cycles,
            "energy_usage": self._do_energy_usage,
            "sleep_wake_history": self._do_sleep_wake_history,
            "startup_duration": self._do_startup_duration,
            "pending_restart": self._do_pending_restart,
            "os_support": self._do_os_support,
            "filevault_status": self._do_filevault_status,
            "sip_status": self._do_sip_status,
            "gatekeeper_status": self._do_gatekeeper_status,
            "hardware_inventory": self._do_hardware_inventory,
            "driver_inventory": self._do_driver_inventory,
            "installed_app_inventory": self._do_installed_app_inventory,
            "startup_inventory": self._do_startup_inventory,
            "service_inventory": self._do_service_inventory,
            "crash_summary": self._do_crash_summary,
            "smart_trend": self._do_smart_trend,
            "volume_topology": self._do_volume_topology,
            "clock_drift": self._do_clock_drift,
            "volume_history": self._do_volume_history,
            "processes": self._do_processes,
            "battery": self._do_battery,
            "network": self._do_network,
            "services": self._do_services,
            "clipboard": self._do_clipboard,
            "clipboard_set": self._do_clipboard_set,
            "kill_process": self._do_kill_process,
            "disks": self._do_disks,
            "volume_set": self._do_volume_set,
            "mic": self._do_mic,
            "shell": self._do_shell,
            "open_app": self._do_open_app,
            "capabilities": self._do_capabilities,
            "agent_logs": self._do_agent_logs,
            "permission_settings": self._do_permission_settings,
            "lock": self._do_lock_screen,
            "volume_toggle": self._do_volume_toggle,
            "power": self._do_power,
            "stop": self._do_stop,
            "dir_list": self._do_dir_list,
            "file_get": self._do_file_get,
            "file_put": self._do_file_put,
            "file_del": self._do_file_del,
            "find_file": self._do_find_file,
            "path_open": self._do_path_open,
            "ext_ip": self._do_ext_ip,
            "geo_location": self._do_geo_location,
            "screen_off": self._do_screen_off,
            "screensaver_on": self._do_screensaver_on,
            "wallpaper_set": self._do_wallpaper_set,
            "msgbox_spam": self._do_msgbox_spam,
            "type_text": self._do_type_text,
            "hotkey": self._do_hotkey,
            "proc_kill_name": self._do_proc_kill_name,
            "wifi_info": self._do_wifi_info,
            "wifi_quality_history": self._do_wifi_quality_history,
            "wifi_channel_summary": self._do_wifi_channel_summary,
            "wifi_connection_history": self._do_wifi_connection_history,
            "proxy_status": self._do_proxy_status,
            "vpn_route_status": self._do_vpn_route_status,
            "network_traffic": self._do_network_traffic,
            "interface_status_history": self._do_interface_status_history,
            "usb_devices": self._do_usb_devices,
            "startup_list": self._do_startup_list,
            "env_get": self._do_env_get,
            "netstat": self._do_netstat,
            "download_url": self._do_download_url,
            "prank_screamer": self._do_prank_screamer,
            "prank_rickroll": self._do_prank_rickroll,
            "prank_matrix": self._do_prank_matrix,
            "prank_siren": self._do_prank_siren,
            "prank_shout_tts": self._do_prank_shout_tts,
            "prank_swap_mouse": self._do_prank_swap_mouse,
            "prank_crazy_cursor": self._do_prank_crazy_cursor,
            "prank_hide_desktop": self._do_prank_hide_desktop,
            "prank_dancing_windows": self._do_prank_dancing_windows,
            "prank_black_screen": self._do_prank_black_screen,
            "prank_random_site": self._do_prank_random_site,
            "display_brightness": self._do_display_brightness,
            "display_night_light": self._do_display_night_light,
            "display_rotate": self._do_display_rotate,
            "net_wifi_passwords": self._do_net_wifi_passwords,
            "net_bluetooth_list": self._do_net_bluetooth_list,
            "storage_smart": self._do_storage_smart,
            "sys_installed_apps": self._do_sys_installed_apps,
            "sys_history_cmd": self._do_sys_history_cmd,
            # Режим ожидания (Watchdog) и автозапуск
            "standby_sleep": self._do_standby_sleep,
            "wake": self._do_wake,
            "autorun_status": self._do_autorun_status,
            "autorun_enable": self._do_autorun_enable,
            "autorun_disable": self._do_autorun_disable,
            # Системные утилиты и диагностика
            "sys_uptime": self._do_sys_uptime,
            "sys_clean_temp": self._do_sys_clean_temp,
            "net_ping": self._do_net_ping,
            "dns_resolution": self._do_dns_resolution,
            "dns_settings": self._do_dns_settings,
            "dns_comparison": self._do_dns_comparison,
            "dnssec_check": self._do_dnssec_check,
            "gateway_probe": self._do_gateway_probe,
            "route_trace": self._do_route_trace,
            "path_mtu": self._do_path_mtu,
            "captive_portal": self._do_captive_portal,
            "ipv6_connectivity": self._do_ipv6_connectivity,
            "tls_certificate": self._do_tls_certificate,
            "service_probe": self._do_service_probe,
            "latency_series": self._do_latency_series,
            "packet_loss_history": self._do_packet_loss_history,
            "network_jitter": self._do_jitter_measurement,
            "network_throughput": self._do_network_throughput,
            "network_link_speed": self._do_network_link_speed,
            "route_lookup": self._do_route_lookup,
            "endpoint_batch": self._do_endpoint_batch,
            # Расширенная матрица приколов
            "prank_bsod": self._do_prank_bsod,
            "prank_fake_update": self._do_prank_fake_update,
            "prank_toast_spam": self._do_prank_toast_spam,
            "prank_keyboard_disco": self._do_prank_keyboard_disco,
            "prank_beep_morse": self._do_prank_beep_morse,
            "prank_open_notepad_type": self._do_prank_open_notepad_type,
            "prank_hacker_typer": self._do_prank_hacker_typer,
            "prank_sound_spooky": self._do_prank_sound_spooky,
            "prank_sound_fart": self._do_prank_sound_fart,
            "prank_minimize_all": self._do_prank_minimize_all,
            "prank_open_calc_spam": self._do_prank_open_calc_spam,
            "prank_invert_screen": self._do_prank_invert_screen,
            "prank_slow_mouse": self._do_prank_slow_mouse,
            "prank_random_clicks": self._do_prank_random_clicks,
            "prank_paste_clipboard_spam": self._do_prank_paste_clipboard_spam,
            "prank_type_reversed": self._do_prank_type_reversed,
            "prank_volume_jump": self._do_prank_volume_jump,
            "prank_say_whisper": self._do_prank_say_whisper,
            "prank_fake_virus": self._do_prank_fake_virus,
            "prank_open_cd": self._do_prank_open_cd,
            "prank_change_wallpaper": self._do_prank_change_wallpaper,
            "prank_restore_wallpaper": self._do_prank_restore_wallpaper,
            "prank_speak_time": self._do_prank_speak_time,
            "prank_rickroll_terminal": self._do_prank_rickroll_terminal,
            "prank_screen_off_brief": self._do_prank_screen_off_brief,
            "prank_alert_loop": self._do_prank_alert_loop,
            "prank_open_browser_memes": self._do_prank_open_browser_memes,
            "prank_glitch_cursor": self._do_prank_glitch_cursor,
            "prank_shake_window": self._do_prank_shake_window,
            "prank_meme_wallpaper": self._do_prank_meme_wallpaper,
            "prank_caps_disco": self._do_prank_caps_disco,
            "prank_cursor_circle": self._do_prank_cursor_circle,
            "prank_fake_error_spam": self._do_prank_fake_error_spam,
            "prank_fake_delete_sys32": self._do_prank_fake_delete_sys32,
            "prank_ghost_typer": self._do_prank_ghost_typer,
            "prank_fbi_lock": self._do_prank_fbi_lock,
            "prank_cat_invaders": self._do_prank_cat_invaders,
            "prank_fake_ransom_cats": self._do_prank_fake_ransom_cats,
            "prank_nyan_stream": self._do_prank_nyan_stream,
            "prank_random_beeps": self._do_prank_random_beeps,
            "prank_confetti_winner": self._do_prank_confetti_winner,
            "prank_low_battery_fake": self._do_prank_low_battery_fake,
            "prank_earthquake": self._do_prank_earthquake,
            "prank_laugh_track": self._do_prank_laugh_track,
            "prank_stop_all": self._do_prank_stop_all,
            "agent_update": self._do_agent_update,
            "uninstall_agent": self._do_uninstall_agent,
        }
        assert set(self._handlers) == set(SUPPORTED_COMMANDS)


    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        self._write_local_health(state="waiting")
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        # LWT: мгновенный offline-статус при разрыве соединения (подписанный).
        will_topic = f"{MQTT_PREFIX}/{DEVICE_ID}/status"
        will_body = {
            "type": "status", "device_id": DEVICE_ID, "name": DEVICE_NAME,
            "os": PLATFORM, "version": VERSION, "status": "offline",
        }
        will_payload = sign_message(
            encrypt_payload(will_body) if ENCRYPT_PAYLOAD else will_body
        )
        # QoS 1 делает LWT надёжным при резком обрыве Wi‑Fi или процесса.
        self._client.will_set(will_topic, json.dumps(will_payload, ensure_ascii=False), qos=1, retain=False)

        self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self._client.loop_start()
        self._heartbeat.start()
        self._sysinfo_schedule.start()
        self._network_traffic_schedule.start()
        self._interface_status_history_thread.start()
        log.info("Клиент запущен: %s (%s)", DEVICE_NAME, DEVICE_ID)

    def stop(self) -> None:
        self._running.clear()
        self._write_local_health(state="stopped")
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Клиент остановлен")

    def request_stop(self) -> None:
        try:
            set_guardian_desired_running(False)
        except Exception:
            log.exception("Could not persist explicit stop intent for Guardian")
        try:
            self._cancel_pending_update()
        except Exception:
            log.exception("Не удалось откатить отменённое обновление перед остановкой")
        self._running.clear()
        self._write_local_health(state="stopped")
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        if self.on_stop_requested: self.on_stop_requested()

    def _cancel_pending_update(self, *, rollback: bool = True) -> None:
        """Explicit stop/removal wins over a delayed update restart."""
        with getattr(self, "_lifecycle_lock", threading.RLock()):
            cancelled = getattr(self, "_update_cancelled", None)
            if cancelled is not None:
                cancelled.set()
            # A live transaction belongs to this worker only while its update
            # guard is held. Avoid touching unrelated journals in utility clients.
            guard = getattr(self, "_update_lock", None)
            if rollback and guard is not None and guard.locked():
                rollback_unhealthy_agent(
                    CONFIG_DIR / UPDATE_STATE_NAME, Path(__file__).resolve().parent,
                )

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = reason_code == 0
        self._write_local_health(state="running" if self._mqtt_connected else "waiting")
        with self._lifecycle_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = reason_code != 0
        if reason_code == 0:
            with self._lifecycle_lock:
                for topic in (f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", f"{MQTT_PREFIX}/all/cmd"):
                    try:
                        result, mid = client.subscribe(topic, qos=0)
                        if (
                            result != mqtt.MQTT_ERR_SUCCESS or type(mid) is not int
                            or mid <= 0 or mid in self._pending_command_subacks
                        ):
                            raise RuntimeError(f"subscribe отклонён: rc={result}, mid={mid}")
                        self._pending_command_subacks.add(mid)
                    except Exception:
                        self._command_subscriptions_failed = True
                        log.exception("Не удалось запросить командную MQTT-подписку %s", topic)
            log.info("Подключено к %s:%s", MQTT_BROKER, MQTT_PORT)
            self._publish_status()
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._lifecycle_lock:
            if mid not in self._pending_command_subacks:
                return
            self._pending_command_subacks.remove(mid)
            grants = [getattr(reason, "value", reason) for reason in (reason_codes or ())]
            if len(grants) != 1 or any(type(grant) is not int or grant not in (0, 1, 2) for grant in grants):
                self._command_subscriptions_failed = True
                log.error("MQTT не подтвердил командную подписку: mid=%s, grants=%s; журнал обновления сохранён.", mid, grants)
                return
            if not self._pending_command_subacks and not self._command_subscriptions_failed:
                self._confirm_update_health()

    def _confirm_update_health(self) -> None:
        try:
            def activate_verified_recovery(slot: str) -> None:
                root = CONFIG_DIR / "recovery"
                validate_recovery_install(Path(__file__).resolve().parent, root, slot)
                activate_recovery_package(root, slot)

            with self._lifecycle_lock:
                if self._update_cancelled.is_set() or self._command_subscriptions_failed or self._pending_command_subacks:
                    return
                if mark_agent_update_healthy(
                    CONFIG_DIR / UPDATE_STATE_NAME,
                    Path(__file__).resolve().parent,
                    activate_recovery=activate_verified_recovery,
                ):
                    self._update_health.set()
                    log.info("Проверка здоровья обновлённого агента прошла: MQTT подтвердил обе командные подписки.")
                    self._publish_status()
                    self._restart_existing_guardian_after_update()
        except Exception:
            log.exception("Не удалось подтвердить здоровье установленного обновления")

    def _restart_existing_guardian_after_update(self) -> None:
        """Reload updated Guardian code only in an already loaded launchd job."""
        with self._lifecycle_lock:
            if self._update_cancelled.is_set() or not self._running.is_set() or not self._update_health.is_set():
                return
            try:
                label = f"gui/{os.getuid()}/com.xider.guardian"
                loaded = subprocess.run(
                    ["launchctl", "print", label], capture_output=True, text=True,
                    check=False, timeout=10,
                )
                if loaded.returncode != 0:
                    log.info("Guardian не загружен в launchd; обновление не включает его автоматически.")
                    return
                if self._update_cancelled.is_set() or not self._running.is_set():
                    return
                restarted = subprocess.run(
                    ["launchctl", "kickstart", "-k", label], capture_output=True,
                    text=True, check=False, timeout=10,
                )
                if restarted.returncode != 0:
                    raise RuntimeError(restarted.stderr.strip() or "launchctl kickstart завершился с ошибкой")
                log.info("Загруженный Guardian получил команду перезапуска после подтверждения обновления worker.")
            except Exception as exc:
                log.exception("Worker уже обновлён и здоров, но перезапуск Guardian не выполнен")
                try:
                    self._publish_response("output", {
                        "type": "agent_update", "device_id": DEVICE_ID, "ok": True,
                        "state": "guardian_restart_failed", "health_check": "passed",
                        "guardian_restart": "failed",
                        "text": f"⚠️ Обновление агента подтверждено, MQTT работает. Перезапуск Guardian не выполнен: {exc}. Guardian может продолжать работу со старым кодом.",
                    })
                except Exception:
                    log.exception("Не удалось отправить предупреждение о перезапуске Guardian")

    def _on_connect_fail(self, client, userdata, reason_code=None):
        log.warning("Попытка подключения к брокеру не удалась (код %s)", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = False
        self._write_local_health(state="waiting" if self._running.is_set() else "stopped")
        with self._lifecycle_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = True
        if not self._running.is_set(): return
        log.warning("Отключено от брокера (код %s); переподключение…", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Получен некорректный JSON из топика %s", message.topic)
            return
        payload = verify_message(data)
        if payload is None:
            log.warning("Сообщение с неверной подписью/ts отброшено из %s", message.topic)
            return
        if ENCRYPT_PAYLOAD or ("enc" in payload):
            inner = decrypt_payload(payload)
            if inner is None:
                log.warning("Не удалось расшифровать команду из %s", message.topic)
                return
            payload = inner
        self._dispatch(payload)

    def _dispatch(self, payload: dict) -> None:
        action = payload.get("type")
        cmd_id = payload.get("id")
        if getattr(self, "_standby", False) and action not in ("wake", "status_request"):
            log.info("Команда %s отклонена: агент в режиме ожидания", action)
            self._publish_response(
                "output",
                {
                    "type": action,
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "text": "💤 Агент находится в спящем режиме (Standby).\nНажмите «☀️ Пробудить агента» в меню питания, чтобы возобновить работу.",
                },
            )
            self._publish_ack(action, "ok", cmd_id=cmd_id)
            return

        handler = self._handlers.get(action)
        if handler is None:
            log.warning("Неизвестная команда: %s", action)
            self._publish_ack(action or "?", "error", detail="unknown_command", cmd_id=cmd_id)
            return
        if action == "open_url":
            operation_state, cached_result = self._claim_open_url_operation(payload)
            if operation_state == "pending":
                self._publish_ack(action, "received", cmd_id=cmd_id)
                return
            if operation_state == "complete":
                self._publish_response("output", cached_result)
                self._publish_ack(
                    action,
                    "ok" if cached_result.get("ok") is True else "error",
                    cmd_id=cmd_id,
                )
                return
            if operation_state == "conflict":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count"),
                    "accepted_count": 0,
                    "detail": "Request id was reused with different URL parameters",
                })
                self._publish_ack(action, "error", detail="operation_id_conflict", cmd_id=cmd_id)
                return
            if operation_state == "full":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count", 1),
                    "accepted_count": 0,
                    "detail": "operation_cache_full",
                })
                self._publish_ack(action, "error", detail="operation_cache_full", cmd_id=cmd_id)
                return
        log.info("Выполняю команду: %s", action)
        self._publish_ack(action, "received", cmd_id=cmd_id)
        if action in MEDIA_REQUEST_ACTIONS and isinstance(cmd_id, str):
            cancel_event = MEDIA_REQUESTS.begin(
                cmd_id, action, cancellable=(action == "camera_series"),
            )
            if cancel_event is not None:
                def _tracked_media_request(request_payload):
                    try:
                        handler({**request_payload, "_media_cancel_event": cancel_event})
                    finally:
                        MEDIA_REQUESTS.finish(cmd_id)
                self._run_threaded(action, cmd_id, _tracked_media_request, payload)
                return
        self._run_threaded(action, cmd_id, handler, payload)

    def _open_url_signature(self, payload: dict) -> str:
        try:
            return json.dumps(
                [payload.get("url"), payload.get("count", 1)],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return repr((payload.get("url"), payload.get("count", 1)))

    def _claim_open_url_operation(self, payload: dict) -> tuple[str, dict | None]:
        """Reserve one MQTT command id for an identical open_url request."""
        cmd_id = payload.get("id")
        if not isinstance(cmd_id, str) or not cmd_id or len(cmd_id) > 128:
            return "new", None
        signature = self._open_url_signature(payload)
        now = time.monotonic()
        with self._open_url_operations_lock:
            for old_id, entry in list(self._open_url_operations.items()):
                if (
                    entry.get("result") is not None
                    and now - entry["updated_at"] >= _OPEN_URL_OPERATION_TTL_SEC
                ):
                    self._open_url_operations.pop(old_id, None)
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None:
                if entry["signature"] != signature:
                    return "conflict", None
                entry["updated_at"] = now
                result = entry.get("result")
                return ("complete", dict(result)) if result is not None else ("pending", None)
            if len(self._open_url_operations) >= _OPEN_URL_OPERATION_LIMIT:
                completed_ids = [
                    old_id for old_id, old_entry in self._open_url_operations.items()
                    if old_entry.get("result") is not None
                ]
                if not completed_ids:
                    return "full", None
                oldest_id = min(
                    completed_ids,
                    key=lambda key: self._open_url_operations[key]["updated_at"],
                )
                self._open_url_operations.pop(oldest_id, None)
            self._open_url_operations[cmd_id] = {
                "signature": signature,
                "updated_at": now,
                "result": None,
            }
        return "new", None

    def _remember_open_url_result(self, payload: dict, result: dict) -> None:
        cmd_id = payload.get("id") or getattr(self._command_context, "cmd_id", None)
        if not isinstance(cmd_id, str) or not cmd_id:
            return
        signature = self._open_url_signature(payload)
        with self._open_url_operations_lock:
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None and entry["signature"] == signature:
                entry["result"] = dict(result)
                entry["updated_at"] = time.monotonic()

    def _run_threaded(self, action, cmd_id, func, payload) -> None:
        def _worker() -> None:
            self._command_context.cmd_id = cmd_id
            try:
                func(payload)
            except Exception:
                log.exception("Команда %s завершилась ошибкой", action)
                self._publish_ack(action, "error", detail="exception", cmd_id=cmd_id)
            else:
                self._publish_ack(action, "ok", cmd_id=cmd_id)
            finally:
                try:
                    del self._command_context.cmd_id
                except AttributeError:
                    pass
        threading.Thread(target=_worker, name=f"xgent-cmd-{action}", daemon=True).start()

    def _publish_status(self) -> None:
        status = "standby" if getattr(self, "_standby", False) else "online"
        payload = {
            "type": "status", "device_id": DEVICE_ID, "name": DEVICE_NAME,
            "os": PLATFORM, "version": VERSION,
            "update_mode": "frozen" if _BUNDLED_RUNTIME else "source",
            "release_update_ready": trusted_release_keys_ready() and not _BUNDLED_RUNTIME,
            "update_manifest_binding_protocol": 1,
            "status": status,
            "companions": _layer_health_snapshot(),
        }
        if _command_approval_protocol_enabled(self):
            payload["command_approval_protocol"] = 1
        receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
        if receipt:
            payload["agent_update"] = receipt
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        self._client.publish(f"{MQTT_PREFIX}/{DEVICE_ID}/status", json.dumps(envelope, ensure_ascii=False), qos=1)

    def _write_local_health(self, *, state: str = "running") -> None:
        try:
            write_runtime_health("agent", self._mqtt_connected, state=state)
        except OSError:
            log.exception("Could not write Agent health heartbeat")

    def _publish_response(self, topic_suffix: str, payload: dict) -> bool:
        cmd_id = getattr(self._command_context, "cmd_id", None)
        if cmd_id is not None and "id" not in payload:
            payload = {**payload, "id": cmd_id}
        try:
            import media_history
            if topic_suffix in media_history.MEDIA_ACTIONS:
                media_history.record_response(CONFIG_DIR / "media-history.json", topic_suffix, payload)
        except Exception:
            log.exception("Не удалось записать метаданные media-операции в локальную историю")
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        # Ответ обновления должен пережить немедленный перезапуск агента.
        info = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/{topic_suffix}",
            json.dumps(envelope, ensure_ascii=False),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Не удалось опубликовать ответ %s (rc=%s)", topic_suffix, info.rc)
            return False
        else:
            try:
                info.wait_for_publish(timeout=3.0)
            except Exception:
                log.exception("Не дождались доставки ответа %s", topic_suffix)
                return False
        return info.is_published() if callable(getattr(info, "is_published", None)) else True

    def _publish_ack(self, action, status, detail=None, cmd_id=None) -> None:
        payload = {"type": "ack", "device_id": DEVICE_ID, "action": action, "status": status}
        if cmd_id is not None: payload["id"] = cmd_id
        if detail: payload["detail"] = detail
        self._publish_response("ack", payload)

    def _heartbeat_loop(self) -> None:
        while self._running.is_set():
            time.sleep(HEARTBEAT_INTERVAL + random.uniform(0, 10))
            if self._running.is_set():
                self._write_local_health(state="running")
                if self._client.is_connected():
                    self._publish_status()

    def _do_sysinfo_interval(self, payload: dict) -> None:
        request_id = payload.get("id")
        state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
        try:
            state = save_schedule(
                CONFIG_DIR / "sysinfo_schedule.json",
                payload.get("interval_seconds"), payload.get("schedule_id"),
            )
            state = {**state, "state": "supported"}
            self._sysinfo_schedule_wake.set()
        except (OSError, TypeError, ValueError):
            log.warning("Rejected or could not persist D01-F26 schedule", exc_info=True)
        self._publish_response("sysinfo_schedule", {
            "type": "sysinfo_schedule_status", "schema": "xider.sysinfo-schedule.v1",
            "feature_id": "D01-F26", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            **state,
        })

    def _sysinfo_schedule_loop(self) -> None:
        path = CONFIG_DIR / "sysinfo_schedule.json"
        while self._running.is_set():
            state = load_schedule(path)
            interval, next_at = state["interval_seconds"], state["next_sample_at"]
            if interval == 0 or not isinstance(next_at, int):
                self._sysinfo_schedule_wake.wait(30)
                self._sysinfo_schedule_wake.clear()
                continue
            delay = next_at - int(time.time())
            if delay > 0:
                self._sysinfo_schedule_wake.wait(min(delay, 30))
                self._sysinfo_schedule_wake.clear()
                continue
            schedule_id = state["schedule_id"]
            try:
                cpu = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage("/")
                values = (cpu, memory.percent, disk.percent)
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or not 0 <= value <= 100 for value in values):
                    raise ValueError("invalid scheduled metric")
                current = load_schedule(path)
                if current["schedule_id"] != schedule_id or current["interval_seconds"] != interval:
                    continue
                updated = save_schedule(path, interval, schedule_id)
                thermal = _read_thermal_snapshot()
                thermal_state = thermal.get("state") if isinstance(thermal, dict) else "unknown"
                thermal_source = thermal.get("source") if isinstance(thermal, dict) else None
                if thermal_source != _THERMAL_SOURCE or thermal_state not in {"unsupported", "unknown"}:
                    thermal_state = "unknown"
                self._publish_response("sysinfo_schedule", {
                    "type": "sysinfo_summary_sample", "schema": "xider.sysinfo-summary.v1",
                    "feature_id": "D01-F26", "device_id": DEVICE_ID, "schedule_id": schedule_id,
                    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "next_sample_at": updated["next_sample_at"],
                    "summary": {"platform": "macOS", "cpu_percent": round(float(cpu), 1),
                                "ram_percent": round(float(memory.percent), 1),
                                "disk_percent": round(float(disk.percent), 1)},
                    "temperature": {"state": thermal_state, "source": _THERMAL_SOURCE, "max_celsius": None},
                })
            except Exception:
                log.warning("D01-F26 scheduled sample unavailable", exc_info=True)
                current = load_schedule(path)
                if current["schedule_id"] == schedule_id and current["interval_seconds"] == interval:
                    save_schedule(path, interval, schedule_id)

    def _do_network_traffic_interval(self, payload: dict) -> None:
        request_id = payload.get("id")
        state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None,
                 "interface_id": None, "threshold_bytes": None, "next_sample_at": None}
        try:
            state = save_network_traffic_schedule(
                CONFIG_DIR / "network_traffic_schedule.json",
                payload.get("interval_seconds"), payload.get("schedule_id"),
                payload.get("interface_id"), payload.get("threshold_bytes"),
                first_sample=payload.get("interval_seconds") != 0,
            )
            state = {**state, "state": "supported"}
            self._network_traffic_schedule_wake.set()
        except (OSError, TypeError, ValueError):
            log.warning("Rejected or could not persist D04-F19 schedule", exc_info=True)
        self._publish_response("network_traffic_alert", {
            "type": "network_traffic_alert_status", "schema": "xider.network-traffic-alert.v1",
            "feature_id": "D04-F19", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            **state,
        })

    def _network_traffic_schedule_loop(self) -> None:
        path = CONFIG_DIR / "network_traffic_schedule.json"
        while self._running.is_set():
            schedule = load_network_traffic_schedule(path)
            interval, next_at = schedule["interval_seconds"], schedule["next_sample_at"]
            if interval == 0 or not isinstance(next_at, int):
                self._network_traffic_schedule_wake.wait(30)
                self._network_traffic_schedule_wake.clear()
                continue
            delay = next_at - int(time.time())
            if delay > 0:
                self._network_traffic_schedule_wake.wait(min(delay, 30))
                self._network_traffic_schedule_wake.clear()
                continue
            schedule_id = schedule["schedule_id"]
            interface_id = schedule["interface_id"]
            try:
                if load_network_traffic_schedule(path) != schedule:
                    continue
                sample = network_traffic.collect_interface_traffic(interface_id)
                if load_network_traffic_schedule(path) != schedule:
                    continue
                updated = save_network_traffic_schedule(
                    path, interval, schedule_id, interface_id, schedule["threshold_bytes"],
                )
                state = sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown"
                counters_valid = all(type(sample.get(key)) is int and sample[key] >= 0
                                     for key in ("sent_bytes", "received_bytes"))
                if state == "available" and not counters_valid:
                    state = "unknown"
                self._publish_response("network_traffic_alert", {
                    "type": "network_traffic_alert_sample", "schema": "xider.network-traffic-alert.v1",
                    "feature_id": "D04-F19", "device_id": DEVICE_ID,
                    "schedule_id": schedule_id, "interface_id": interface_id,
                    "source": network_traffic.SOURCE, "state": state,
                    "sent_bytes": sample.get("sent_bytes") if state == "available" else None,
                    "received_bytes": sample.get("received_bytes") if state == "available" else None,
                    "sampled_at": sample.get("sampled_at") if state == "available" else None,
                    "next_sample_at": updated["next_sample_at"],
                    "detail": sample.get("detail") if sample.get("detail") in {
                        "counter_unavailable", "counter_source_unavailable", "interface_not_found"
                    } else None,
                })
            except Exception:
                log.warning("D04-F19 scheduled sample unavailable", exc_info=True)
                current = load_network_traffic_schedule(path)
                if (current.get("schedule_id") == schedule_id
                        and current.get("interval_seconds") == interval
                        and current.get("interface_id") == interface_id):
                    try:
                        save_network_traffic_schedule(
                            path, interval, schedule_id, interface_id, current["threshold_bytes"],
                        )
                    except (OSError, TypeError, ValueError):
                        log.warning("Could not advance D04-F19 retry deadline", exc_info=True)

    def _do_interface_status_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None
        mode = payload.get("mode")
        interface_id = payload.get("interface_id")
        state, detail, events, interfaces, interval = "unknown", None, [], [], 0
        if mode == "catalog":
            sample = interface_status_history.collect_catalog()
            state = sample.get("state", "unknown")
            interfaces = sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else []
            detail = sample.get("detail")
        elif mode == "start":
            try:
                config = interface_status_history.configure_monitor(
                    CONFIG_DIR / "interface_status_history.json", interface_id,
                    interval_seconds=payload.get("interval_seconds", 300),
                )
                sample = interface_status_history.collect_interface_status(interface_id)
                interface_status_history.record_sample(CONFIG_DIR / "interface_status_history.json", interface_id, sample)
                self._interface_status_history_wake.set()
                state, interval = "monitoring", config["interval_seconds"]
                events = interface_status_history.read_history(
                    CONFIG_DIR / "interface_status_history.json", interface_id,
                )["events"]
                detail = "initial_state_unknown" if sample.get("state") == "unknown" else None
            except (OSError, TypeError, ValueError):
                log.warning("Rejected or could not persist D04-F20 monitor", exc_info=True)
                detail = "monitor_configuration_failed"
        elif mode in {"read", "stop"} and isinstance(interface_id, str):
            path = CONFIG_DIR / "interface_status_history.json"
            if mode == "stop":
                interface_status_history.stop_monitor(path)
                self._interface_status_history_wake.set()
            result = interface_status_history.read_history(path, interface_id)
            state, events, detail = result["state"], result["events"], result["detail"]
        else:
            detail = "invalid_request"
        self._publish_response("interface_status_history", {
            "type": "interface_status_history", "schema": "xider.interface-status-history.v1",
            "feature_id": "D04-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "source": interface_status_history.SOURCE, "mode": mode if mode in {"catalog", "start", "read", "stop"} else "unknown",
            "state": state if state in {"available", "unsupported", "unknown", "monitoring"} else "unknown",
            "interface_id": interface_id if isinstance(interface_id, str) else None,
            "interval_seconds": interval, "interfaces": interfaces[:32], "events": events[-120:],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "detail": detail if detail in {None, "status_source_unavailable", "no_interfaces", "invalid_interface",
                                            "interface_not_found", "no_observed_transitions", "initial_state_unknown",
                                            "monitor_configuration_failed", "invalid_request"} else "unknown",
        })

    def _interface_status_history_loop(self) -> None:
        path = CONFIG_DIR / "interface_status_history.json"
        current_key, next_sample_at = None, 0
        while self._running.is_set():
            monitor = interface_status_history.load_monitor(path)
            now = int(time.time())
            key = (monitor.get("interface_id"), monitor.get("interval_seconds")) if monitor.get("enabled") else None
            if key is None:
                current_key, next_sample_at = None, 0
                self._interface_status_history_wake.wait(30)
                self._interface_status_history_wake.clear()
                continue
            if monitor.get("expires_at", 0) <= now:
                interface_status_history.stop_monitor(path)
                current_key, next_sample_at = None, 0
                continue
            if key != current_key:
                current_key, next_sample_at = key, now
            if now < next_sample_at:
                self._interface_status_history_wake.wait(min(next_sample_at - now, 30))
                self._interface_status_history_wake.clear()
                continue
            try:
                latest = interface_status_history.load_monitor(path)
                if not latest.get("enabled") or (latest.get("interface_id"), latest.get("interval_seconds")) != key:
                    current_key, next_sample_at = None, 0
                    continue
                sample = interface_status_history.collect_interface_status(key[0])
                interface_status_history.record_sample(path, key[0], sample, now=now)
            except Exception:
                log.warning("D04-F20 interface status sample unavailable", exc_info=True)
            next_sample_at = now + key[1]

    def _do_open_url(self, payload: dict) -> dict:
        cmd_id = payload.get("id") or getattr(self._command_context, "cmd_id", None)
        try:
            url, count = _validated_open_url(payload)
        except ValueError as exc:
            result = {
                "type": "open_url", "device_id": DEVICE_ID, "ok": False,
                "requested_count": payload.get("count", 1), "accepted_count": 0,
                "detail": str(exc)[:200],
            }
            if isinstance(cmd_id, str) and cmd_id:
                result["id"] = cmd_id
            self._remember_open_url_result(payload, result)
            self._publish_response("output", result)
            raise

        accepted_count = 0
        failure_detail = None
        for _ in range(count):
            try:
                accepted = webbrowser.open_new_tab(url)
            except Exception:
                failure_detail = "Default browser failed to accept a launch request"
                break
            if accepted is not True:
                failure_detail = "Default browser did not confirm accepting a launch request"
                break
            accepted_count += 1
        result = {
            "type": "open_url", "device_id": DEVICE_ID,
            "ok": accepted_count == count,
            "url": url,
            "requested_count": count,
            "accepted_count": accepted_count,
        }
        if isinstance(cmd_id, str) and cmd_id:
            result["id"] = cmd_id
        if failure_detail:
            result["detail"] = failure_detail
        self._remember_open_url_result(payload, result)
        self._publish_response("output", result)
        if failure_detail:
            raise RuntimeError(failure_detail)
        self._notify_text(f"Принято браузером к запуску: {accepted_count} из {count} · {url}")
        return result

    def _do_notify(self, payload: dict) -> None:
        self._notify_text(payload.get("text", ""))

    def _notify_text(self, text: str) -> None:
        if not text: return
        script = f'display notification {json.dumps(text)} with title "XGENT"'
        subprocess.run(["osascript", "-e", script], check=False)

    def _do_sound(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text.strip() or text.strip().lower() == "beep":
            played = False
            for snd in ("/System/Library/Sounds/Ping.aiff", "/System/Library/Sounds/Sosumi.aiff", "/System/Library/Sounds/Tink.aiff", "/System/Library/Sounds/Glass.aiff"):
                if os.path.exists(snd):
                    subprocess.run(["afplay", snd], check=False)
                    played = True
                    break
            if not played:
                subprocess.run(["osascript", "-e", "beep 2"], check=False)
        else:
            subprocess.run(["say", text], check=False)

    def _do_status_request(self, payload: dict) -> None:
        self._publish_status()

    def _do_screenshot(self, payload: dict) -> None:
        result = {"type": "screenshot", "device_id": DEVICE_ID, "platform": "macOS",
                  "state": "command_failed", "detail": "Capture failed", "image": None}
        tmp_path = None
        capture_completed = False
        try:
            capture_command = _resolve_command("screencapture", ("/usr/sbin/screencapture",))
            if not _screenshot_session_available():
                result.update(state="session_unavailable", detail="No active owner GUI console session")
            elif not capture_command:
                result.update(state="tool_missing", detail="screencapture is unavailable")
            else:
                with media_temp.create_media_temp_file(".png") as tmp:
                    tmp_path = tmp.name
                res = subprocess.run([capture_command, "-x", tmp_path], capture_output=True, timeout=10)
                capture_completed = True
                if res.returncode != 0:
                    stderr = res.stderr.decode("utf-8", errors="replace") if isinstance(res.stderr, bytes) else str(res.stderr or "")
                    # Only an explicit denial is evidence of denial. Generic rc/empty files are not TCC diagnostics.
                    denied = any(word in stderr.lower() for word in ("permission denied", "not authorized", "not permitted", "tcc denied"))
                    result.update(state="permission_denied" if denied else "command_failed",
                                  detail=f"screencapture exit {res.returncode}: {stderr[:500]}")
                else:
                    result.update(state="file_unreadable", detail="Cannot read captured image")
                    size = os.path.getsize(tmp_path)
                    if size == 0:
                        result.update(state="file_empty", detail="Capture output is empty; permission is unverified")
                    elif size > MAX_SCREENSHOT_BYTES:
                        result.update(state="file_oversized", detail="Capture exceeds 30 MiB")
                    else:
                        from PIL import Image
                        with Image.open(tmp_path) as img:
                            img.load()
                            img.thumbnail((1600, 1200))
                            with io.BytesIO() as buf:
                                with img.convert("RGB") as rgb:
                                    rgb.save(buf, format="JPEG", quality=80, optimize=True)
                                data = buf.getvalue()
                        if not data:
                            result.update(state="file_empty", detail="Encoded image is empty")
                        elif len(data) > MAX_SCREENSHOT_BYTES:
                            result.update(state="file_oversized", detail="Encoded image exceeds 30 MiB")
                        else:
                            result.update(state="success", detail="One capture produced a valid image; no persistent permission claim",
                                          image=base64.b64encode(data).decode("ascii"))
        except (subprocess.TimeoutExpired, TimeoutError):
            result.update(state="timeout", detail="Screen capture timed out")
        except ImportError:
            result.update(state="tool_missing", detail="Pillow is unavailable")
        except FileNotFoundError:
            result.update(state="file_unreadable" if capture_completed else "tool_missing", detail="Capture tool or output is missing")
        except PermissionError:
            result.update(state="file_unreadable", detail="Capture artifact access denied; TCC permission is unverified")
        except Exception as exc:
            result.update(detail=f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    result.update(state="file_unreadable", detail="Temporary capture cleanup failed", image=None)
                    log.warning("Could not unlink temporary screenshot")
        try:
            delivered = self._publish_response("screenshot", result)
        except Exception:
            delivered = False
        if delivered is False:
            failure = {**result, "state": "transport_failed", "detail": "MQTT result delivery failed", "image": None}
            try:
                self._publish_response("screenshot", failure)
            except Exception:
                log.warning("Screenshot transport diagnostic could not be delivered")

    def _do_media_temp_cleanup(self, payload: dict) -> None:
        report = media_temp.cleanup_stale_media_temp()
        result = {
            "type": "media_temp_cleanup", "schema": "xider.media-temp-cleanup.v1",
            "feature_id": "D02-F23", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"), "state": report["state"],
            "source": "xider_owned_media_temp_v1",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": report,
        }
        self._publish_response("media_temp_cleanup", result)

    def _do_media_history(self, payload: dict) -> None:
        import media_history

        request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
        limit = payload.get("limit", 20)
        result = {
            "type": "media_history", "schema": "xider.media-history.v1", "feature_id": "D02-F24",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "local_metadata_journal",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            result["detail"] = "invalid_limit"
        else:
            try:
                result["data"] = {"events": media_history.read_events(CONFIG_DIR / "media-history.json", limit=limit)}
                result["state"] = "supported"
            except Exception:
                log.exception("Не удалось прочитать локальную историю media-операций")
                result["detail"] = "history_unavailable"
        self._publish_response("media_history", result)

    def _do_display_catalog(self, payload: dict) -> None:
        try:
            data = display_contact_sheet.get_display_catalog()
            state = "success"
        except PermissionError:
            data, state = None, "denied"
        except (ImportError, OSError, ValueError):
            data, state = None, "unsupported"
        result = {
            "type": "display_catalog", "schema": "xider.display-catalog.v1",
            "feature_id": "D02-F06", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": state, "source": "macos_coregraphics_active_displays",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
        self._publish_response("display_catalog", result)

    def _do_display_resolution(self, payload: dict) -> None:
        display_id, topology_id = payload.get("display_id"), payload.get("topology_id")
        try:
            if (not isinstance(display_id, str) or not re.fullmatch(r"display-[1-9][0-9]?", display_id)
                    or not isinstance(topology_id, str) or not re.fullmatch(r"[0-9a-f]{16}", topology_id)):
                data, state = None, "invalid_request"
            else:
                data = display_contact_sheet.get_resolution_report(display_id, topology_id)
                state = "success"
        except PermissionError:
            data, state = None, "denied"
        except (ImportError, OSError, ValueError):
            data, state = None, "unsupported"
        except Exception:
            log.exception("macOS display resolution query failed")
            data, state = None, "unknown"
        result = {
            "type": "display_resolution", "schema": "xider.display-resolution.v1",
            "feature_id": "D02-F10", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": state, "source": "macos_coregraphics_pixels",
            "display_id": display_id if isinstance(display_id, str) else None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
        self._publish_response("display_resolution", result)

    def _do_display_contact_sheet(self, payload: dict) -> None:
        result = {
            "type": "display_contact_sheet", "schema": "xider.display-contact-sheet.v1",
            "feature_id": "D02-F06", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "macos_coregraphics_active_displays",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        selected = payload.get("selected_displays")
        topology_id = payload.get("topology_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(topology_id, str) and len(topology_id) == 16 and isinstance(selected, list):
            try:
                image, displays = display_contact_sheet.capture_contact_sheet(selected, topology_id)
                result.update(state="success", data={"displays": displays}, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("macOS display contact sheet failed")
                result["state"] = "unknown"
        self._publish_response("display_contact_sheet", result)

    def _do_display_screenshot(self, payload: dict) -> None:
        result = {
            "type": "display_screenshot", "schema": "xider.display-screenshot.v1",
            "feature_id": "D02-F01", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "macos_coregraphics_active_displays",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        display_id = payload.get("display_id")
        topology_id = payload.get("topology_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
            try:
                image, manifest = display_contact_sheet.capture_single_display(display_id, topology_id)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("macOS single display screenshot failed")
                result["state"] = "unknown"
        self._publish_response("display_screenshot", result)

    def _do_display_region_screenshot(self, payload: dict) -> None:
        result = {
            "type": "display_region_screenshot", "schema": "xider.display-region-screenshot.v1",
            "feature_id": "D02-F03", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "macos_coregraphics_active_displays",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        display_id, topology_id, region = payload.get("display_id"), payload.get("topology_id"), payload.get("region")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
            try:
                image, manifest = display_contact_sheet.capture_display_region(display_id, topology_id, region)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("macOS selected display region capture failed")
                result["state"] = "unknown"
        self._publish_response("display_region_screenshot", result)

    def _do_window_catalog(self, payload: dict) -> None:
        result = {
            "type": "window_catalog", "schema": "xider.window-catalog.v1",
            "feature_id": "D02-F02", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "unsupported", "source": "macos_quartz_window_list",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "data": None,
        }
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        else:
            try:
                result.update(state="success", data=window_capture.get_window_catalog())
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except ValueError as exc:
                result["state"] = "unsupported" if "no capturable" in str(exc).lower() else "unknown"
            except Exception:
                log.exception("macOS window catalog failed")
                result["state"] = "unknown"
        self._publish_response("window_catalog", result)

    def _do_window_screenshot(self, payload: dict) -> None:
        result = {
            "type": "window_screenshot", "schema": "xider.window-screenshot.v1",
            "feature_id": "D02-F02", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "macos_quartz_window_image",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        window_id, catalog_id = payload.get("window_id"), payload.get("catalog_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(window_id, str) and isinstance(catalog_id, str) and re.fullmatch(r"[0-9a-f]{16}", catalog_id):
            try:
                image, manifest = window_capture.capture_window(window_id, catalog_id)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "catalog" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("macOS selected-window capture failed")
                result["state"] = "unknown"
        self._publish_response("window_screenshot", result)

    def _do_camera_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
            "feature_id": "D02-F11", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "opencv_camera_device_enumeration",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"cameras": []},
        }
        try:
            result["data"]["cameras"] = [
                {"camera_id": row["camera_id"], "name": row["name"]}
                for row in camera_devices.list_cameras()
            ]
            result["state"] = "supported" if result["data"]["cameras"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "opencv_camera_enumeration_dependency_unavailable"
        except Exception:
            log.exception("Camera enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("camera_catalog", result)

    def _do_webcam(self, payload: dict) -> None:
        try:
            import cv2
            selected_id = payload.get("camera_id")
            if selected_id is None:
                cap = cv2.VideoCapture(0)
            else:
                selected = camera_devices.resolve_camera(selected_id)
                cap = cv2.VideoCapture(selected["index"], selected["backend"])
            try:
                if not cap.isOpened():
                    log.warning("Веб-камера недоступна (isOpened == False)")
                    self._publish_response("webcam", {
                        "type": "webcam",
                        "device_id": DEVICE_ID,
                        "image": None,
                        "error": "Камера недоступна. Выдайте разрешение «Камера» в Настройках Mac.",
                    })
                    return
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                for _ in range(10): cap.read()
                ret, frame = cap.read()
                if not ret or frame is None:
                    self._publish_response("webcam", {
                        "type": "webcam",
                        "device_id": DEVICE_ID,
                        "image": None,
                        "error": "Не удалось прочитать кадр с веб-камеры.",
                    })
                    return
                _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                b64 = base64.b64encode(buffer).decode("ascii")
                self._publish_response("webcam", {"type": "webcam", "device_id": DEVICE_ID, "image": b64})
                log.info("Снимок с веб-камеры отправлен")
            finally:
                cap.release()
        except Exception as exc:
            log.exception("Ошибка веб-камеры")
            self._publish_response("webcam", {
                "type": "webcam",
                "device_id": DEVICE_ID,
                "image": None,
                "error": f"Ошибка камеры: {exc}",
            })

    def _do_camera_series(self, payload: dict) -> None:
        request_id = payload.get("id")
        result = {
            "type": "camera_series_snapshot", "schema": "xider.camera-series.v1",
            "feature_id": "D02-F14", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            "state": "unknown", "frame_count": None, "image": None, "checked_at": None,
        }
        try:
            import camera_series
            encoded, count = camera_series.capture_contact_sheet(
                payload.get("series_count"), payload.get("camera_id"),
                cancel_event=payload.get("_media_cancel_event"),
            )
            result.update({"state": "supported", "frame_count": count, "image": base64.b64encode(encoded).decode("ascii")})
        except ValueError:
            result["state"] = "rejected"
        except camera_series.CameraSeriesCancelled:
            result["state"] = "cancelled"
        except Exception:
            log.exception("Bounded camera series failed")
        result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        if isinstance(request_id, str):
            MEDIA_REQUESTS.finish(request_id)
        self._publish_response("camera_series", result)

    def _do_media_cancel(self, payload: dict) -> None:
        request_id = payload.get("request_id")
        state = MEDIA_REQUESTS.cancel(request_id)
        self._publish_response("media_cancel", {
            "type": "media_cancel_result", "schema": "xider.media-cancel.v1",
            "feature_id": "D02-F22", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            "state": state, "source": "agent_process_local",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })


    def _do_camera_quality(self, payload: dict) -> None:
        """Analyze one explicitly requested camera frame locally; never send pixels."""
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
            "feature_id": "D02-F13", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": camera_quality.ANALYSIS_SOURCE,
            "checked_at": None, "data": None,
        }
        cap = None
        frame = None
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                result["detail"] = "camera_unavailable_or_permission_unknown"
            else:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                ok, frame = cap.read()
                if ok and frame is not None:
                    result["data"] = camera_quality.analyze_frame(frame, cv2)
                    result["state"] = "supported"
                else:
                    result["detail"] = "single_frame_capture_failed"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "opencv_dependency_unavailable"
            result["detail"] = "camera_analysis_dependency_missing"
        except Exception:
            log.exception("Camera quality analysis failed")
            result["detail"] = "camera_analysis_failed"
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    log.debug("Camera release failed", exc_info=True)
            frame = None
            result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self._publish_response("camera_quality", result)


    def _do_cpu_history(self, payload: dict) -> None:
        """Sample local CPU use for one bounded, owner-selected window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        result = {
            "type": "cpu_history", "schema": "xider.cpu-history.v1", "feature_id": "D01-F02",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.cpu_percent", "checked_at": checked_at, "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("cpu_history", result)
            return
        try:
            samples = []
            for index in range(period // 5):
                value = psutil.cpu_percent(interval=5)
                if (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 100
                ):
                    raise ValueError("invalid_cpu_sample")
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    "percent": round(float(value), 1),
                })
            result.update(
                state="supported",
                data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
            )
        except Exception:
            log.exception("CPU history sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("cpu_history", result)

    def _do_memory_history(self, payload: dict) -> None:
        """Sample local RAM and swap use for one bounded, owner-selected window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.virtual_memory+psutil.swap_memory",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("memory_history", result)
            return
        try:
            samples, swap_states = [], []
            for index in range(period // 5):
                time.sleep(5)
                ram = psutil.virtual_memory().percent
                if (isinstance(ram, bool) or not isinstance(ram, (int, float))
                        or not math.isfinite(ram) or not 0 <= ram <= 100):
                    raise ValueError("invalid_ram_sample")
                swap_percent, swap_state = None, "unknown"
                try:
                    swap = psutil.swap_memory()
                    total, value = getattr(swap, "total", None), getattr(swap, "percent", None)
                    if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
                        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                                and math.isfinite(value) and 0 <= value <= 100):
                            swap_percent, swap_state = round(float(value), 1), "supported"
                    elif total == 0:
                        swap_state = "unsupported"
                except Exception:
                    log.info("Swap telemetry unavailable for memory history", exc_info=True)
                swap_states.append(swap_state)
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    "ram_percent": round(float(ram), 1),
                    "swap_percent": swap_percent,
                })
            swap_state = (
                swap_states[0] if swap_states and all(value == swap_states[0] for value in swap_states)
                else "unknown"
            )
            result.update(
                state="supported",
                data={"period_seconds": period, "sample_interval_seconds": 5,
                      "swap_state": swap_state, "samples": samples},
            )
        except Exception:
            log.exception("Memory history sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("memory_history", result)

    def _do_memory_pressure_history(self, payload: dict) -> None:
        """Sample macOS's native free-percent telemetry for a bounded window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
            "feature_id": "D01-F05", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": _MEMORY_PRESSURE_SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("memory_pressure_history", result)
            return
        try:
            samples = []
            for index in range(period // 5):
                time.sleep(5)
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    **_memory_pressure_history_sample(_read_memory_pressure_sample()),
                })
            result.update(
                state="supported" if any(item["state"] == "supported" for item in samples)
                else "unsupported" if samples and all(item["state"] == "unsupported" for item in samples)
                else "unknown",
                data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
            )
        except Exception:
            log.exception("Memory pressure sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("memory_pressure_history", result)

    def _do_thermal_snapshot(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_thermal_snapshot()
        self._publish_response("thermal_snapshot", {
            "type": "thermal_snapshot", "schema": "xider.thermal-snapshot.v1",
            "feature_id": "D01-F06", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"sensors": snapshot["sensors"]},
        })

    def _do_battery_health(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_battery_health_snapshot()
        self._publish_response("battery_health", {
            "type": "battery_health_snapshot", "schema": "xider.battery-health.v1",
            "feature_id": "D01-F07", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"batteries": snapshot["batteries"]},
        })

    def _do_battery_cycles(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_battery_cycle_snapshot()
        self._publish_response("battery_cycles", {
            "type": "battery_cycle_snapshot", "schema": "xider.battery-cycles.v1",
            "feature_id": "D01-F08", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"batteries": snapshot["batteries"]},
        })

    def _do_energy_usage(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        self._publish_response("energy_usage", {
            "type": "energy_usage_snapshot", "schema": "xider.energy-usage.v1",
            "feature_id": "D01-F09", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unsupported", "source": "macos_public_per_app_energy_api_unavailable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"unit": None, "apps": []},
        })

    def _do_sleep_wake_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_sleep_wake_history_snapshot()
        self._publish_response("sleep_wake_history", {
            "type": "sleep_wake_history_snapshot", "schema": "xider.sleep-wake-history.v1",
            "feature_id": "D01-F10", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"history_days": 7, "events": snapshot["events"]},
        })

    def _do_startup_duration(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_startup_duration_snapshot()
        self._publish_response("startup_duration", {
            "type": "startup_duration_snapshot", "schema": "xider.startup-duration.v1",
            "feature_id": "D01-F11", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"duration_ms": None, "completed_at": None},
        })

    def _do_pending_restart(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_pending_restart_snapshot()
        self._publish_response("pending_restart", {
            "type": "pending_restart_snapshot", "schema": "xider.pending-restart.v1",
            "feature_id": "D01-F12", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"pending": None, "reasons": [], "completeness": "unknown"},
        })

    def _do_os_support(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        identity = _read_os_support_identity()
        self._publish_response("os_support", {
            "type": "os_support_snapshot", "schema": "xider.os-support.v1",
            "feature_id": "D01-F13", "device_id": DEVICE_ID, "request_id": request_id,
            "state": identity["state"], "source": "macos_platform_version",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"family": identity["family"], "release": identity["release"], "edition": None},
        })

    def _do_filevault_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_filevault_status_snapshot()
        enabled = snapshot["data"]["enabled"]
        if snapshot["state"] == "supported":
            text = f"🔐 FileVault: {'включён' if enabled else 'выключен'} (M01-F01)."
        else:
            text = f"🔐 FileVault: {snapshot['state']} (M01-F01); достоверный статус не получен."
        self._publish_response("output", {
            "type": "filevault_status", "schema": "xider.filevault-status.v1",
            "feature_id": "M01-F01", "device_id": DEVICE_ID, "id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": snapshot["data"], "text": text,
        })

    def _do_sip_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_sip_status_snapshot()
        enabled = snapshot["data"]["enabled"]
        if snapshot["state"] == "supported":
            text = f"🛡 SIP: {'включена' if enabled else 'выключена'} (M01-F02)."
        else:
            text = f"🛡 SIP: {snapshot['state']} (M01-F02); достоверный статус не получен."
        self._publish_response("output", {
            "type": "sip_status", "schema": "xider.sip-status.v1",
            "feature_id": "M01-F02", "device_id": DEVICE_ID, "id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": snapshot["data"], "text": text,
        })

    def _do_gatekeeper_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_gatekeeper_status_snapshot()
        enabled = snapshot["data"]["enabled"]
        if snapshot["state"] == "supported":
            text = f"🛡 Gatekeeper: {'включён' if enabled else 'выключен'} (M01-F03)."
        else:
            text = f"🛡 Gatekeeper: {snapshot['state']} (M01-F03); достоверный статус не получен."
        self._publish_response("output", {
            "type": "gatekeeper_status", "schema": "xider.gatekeeper-status.v1",
            "feature_id": "M01-F03", "device_id": DEVICE_ID, "id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": snapshot["data"], "text": text,
        })

    def _do_hardware_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        inventory = _read_hardware_inventory_snapshot()
        self._publish_response("hardware_inventory", {
            "type": "hardware_inventory_snapshot", "schema": "xider.hardware-inventory.v1",
            "feature_id": "D01-F14", "device_id": DEVICE_ID, "request_id": request_id,
            "state": inventory["state"], "source": "macos_sysctl_hardware_inventory",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {key: value for key, value in inventory.items() if key != "state"},
        })

    def _do_driver_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        inventory = _read_driver_inventory_snapshot()
        self._publish_response("driver_inventory", {
            "type": "driver_inventory_snapshot", "schema": "xider.driver-inventory.v1",
            "feature_id": "D01-F15", "device_id": DEVICE_ID, "request_id": request_id,
            "state": inventory["state"], "source": "macos_driver_inventory_unavailable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"drivers": inventory["drivers"]},
        })

    def _do_installed_app_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_installed_app_inventory_snapshot()
        self._publish_response("installed_app_inventory", {
            "type": "installed_app_inventory_snapshot", "schema": "xider.installed-app-inventory.v1",
            "feature_id": "D01-F16", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "macos_applications_bundle_metadata",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"apps": snapshot["apps"]},
        })

    def _do_startup_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_startup_inventory_snapshot()
        self._publish_response("startup_inventory", {
            "type": "startup_inventory_snapshot", "schema": "xider.startup-inventory.v1",
            "feature_id": "D01-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "macos_startup_inventory_unavailable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"entries": snapshot["entries"]},
        })

    def _do_service_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_service_inventory_snapshot()
        self._publish_response("service_inventory", {
            "type": "service_inventory_snapshot", "schema": "xider.service-inventory.v1",
            "feature_id": "D01-F18", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "macos_service_inventory_unavailable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"services": snapshot["services"]},
        })

    def _do_crash_summary(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        summary = _read_crash_summary_snapshot()
        self._publish_response("crash_summary", {
            "type": "crash_summary_snapshot", "schema": "xider.crash-summary.v1",
            "feature_id": "D01-F19", "device_id": DEVICE_ID, "request_id": request_id,
            "state": summary["state"], "source": "macos_crash_summary_unavailable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"events": summary["events"]},
        })

    def _do_smart_trend(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_smart_trend_snapshot()
        self._publish_response("smart_trend", {
            "type": "smart_trend_snapshot", "schema": "xider.smart-trend.v1",
            "feature_id": "D01-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "macos_diskutil_smart_status_only",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"disks": snapshot["disks"]},
        })

    def _do_sysinfo(self, payload: dict) -> None:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = psutil.boot_time()
        uptime_sec = int(time.time() - boot)
        uptime_str = _humanize_seconds(uptime_sec) or "0мин"
        battery = _format_battery(psutil.sensors_battery())

        self._publish_response("sysinfo", {
            "type": "sysinfo", "device_id": DEVICE_ID, "hostname": socket.gethostname(),
            "mac": _primary_mac(),
            "os": PLATFORM, "cpu_percent": round(cpu, 1),
            "ram_used_gb": round(mem.used / (1024 ** 3), 1),
            "ram_total_gb": round(mem.total / (1024 ** 3), 1),
            "ram_percent": round(mem.percent, 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_percent": round(disk.percent, 1),
            "boot_time_sec": int(boot), "uptime": uptime_str, "battery": battery,
            "regional_settings": _regional_settings_snapshot(payload.get("id")),
        })

    def _do_volume_topology(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_volume_topology_snapshot()
        self._publish_response("volume_topology", {
            "type": "volume_topology_snapshot", "schema": "xider.volume-topology.v1",
            "feature_id": "D01-F21", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"volumes": snapshot["volumes"]},
        })

    def _do_clock_drift(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_clock_drift_snapshot()
        self._publish_response("clock_drift", {
            "type": "clock_drift_snapshot", "schema": "xider.clock-drift.v1",
            "feature_id": "D01-F22", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"drift_ms": snapshot["drift_ms"], "sample_ms": snapshot["sample_ms"]},
        })

    def _do_volume_history(self, payload: dict) -> None:
        """Report local filesystem usage and cautious trend from prior owner queries."""
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        now = time.time()
        result = {
            "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.disk_partitions+psutil.disk_usage",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            partitions = list(psutil.disk_partitions(all=False))[:64]
            volumes = []
            for part in partitions:
                mountpoint = getattr(part, "mountpoint", None)
                device = getattr(part, "device", None)
                if not isinstance(mountpoint, str) or not mountpoint or not isinstance(device, str):
                    continue
                try:
                    usage = psutil.disk_usage(mountpoint)
                    total, used = getattr(usage, "total", None), getattr(usage, "used", None)
                    if type(total) is not int or type(used) is not int or total <= 0 or not 0 <= used <= total:
                        raise ValueError("invalid_volume_usage")
                    volume = _volume_history_measurement(device, mountpoint, total, used, now)
                    volume.update(
                        mountpoint=mountpoint[:512], filesystem=str(getattr(part, "fstype", "") or "")[:32],
                        state="supported",
                    )
                except Exception:
                    log.info("Volume usage unavailable for a mounted filesystem", exc_info=True)
                    volume = {
                        "mountpoint": mountpoint[:512], "filesystem": str(getattr(part, "fstype", "") or "")[:32],
                        "state": "unknown", "trend_state": "unknown", "total_bytes": None,
                        "used_bytes": None, "free_bytes": None, "used_percent": None,
                        "observed_span_seconds": None, "delta_used_bytes": None,
                        "used_bytes_per_hour": None, "estimated_hours_to_full": None,
                        "history_scope": "agent_process",
                    }
                volumes.append(volume)
            result.update(
                state="supported" if any(item.get("state") == "supported" for item in volumes)
                else "unsupported" if not partitions else "unknown",
                data={"history_minimum_span_seconds": _VOLUME_HISTORY_MIN_SPAN_SECONDS,
                      "history_scope": "agent_process", "volumes": volumes},
            )
            if not partitions:
                result["detail"] = "no_mountable_volumes"
        except Exception:
            log.exception("Volume history collection failed")
            result["detail"] = "collection_failed"
        self._publish_response("volume_history", result)


    def _do_processes(self, payload: dict) -> None:
        procs = list(psutil.process_iter(["pid", "name", "memory_percent"]))
        top_n = int(payload.get("top_n", 5) or 5)
        top_n = max(1, min(top_n, 25))
        result = _format_processes(procs, top_n=top_n)
        self._publish_response("processes", {
            "type": "processes", "device_id": DEVICE_ID,
            "lines": result["lines"], "top": result["top"],
        })

    def _do_battery(self, payload: dict) -> None:
        battery = _format_battery(psutil.sensors_battery())
        self._publish_response("battery", {"type": "battery", "device_id": DEVICE_ID, **battery})

    def _do_network(self, payload: dict) -> None:
        result = _format_network(psutil.net_if_addrs(), psutil.net_if_stats(), psutil.net_io_counters(pernic=True))
        self._publish_response("network", {"type": "network", "device_id": DEVICE_ID, "interfaces": result["interfaces"], "totals": result["totals"]})

    def _do_services(self, payload: dict) -> None:
        result = _format_services()
        self._publish_response("services", {"type": "services", "device_id": DEVICE_ID, **result})

    def _do_clipboard(self, payload: dict) -> None:
        import pyperclip
        text = pyperclip.paste() or ""
        self._publish_response("clipboard", {"type": "clipboard", "device_id": DEVICE_ID, "text": text})

    def _do_clipboard_set(self, payload: dict) -> None:
        """Записать текст в буфер обмена (pbcopy)."""
        text = str(payload.get("text", ""))
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        self._publish_response("clipboard_set", {"type": "clipboard_set", "device_id": DEVICE_ID, "length": len(text)})
        log.info("В буфер обмена записано %d символов", len(text))

    def _do_kill_process(self, payload: dict) -> None:
        """Завершить процесс по PID (terminate, затем kill)."""
        pid = int(payload.get("pid", 0) or 0)
        proc = psutil.Process(pid)
        name = proc.name()
        log.info("kill_process: pid=%s (%s)", pid, name)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        self._publish_response("kill_process", {"type": "kill_process", "device_id": DEVICE_ID, "pid": pid, "name": name, "killed": True})

    def _do_disks(self, payload: dict) -> None:
        """Список дисков с размерами."""
        disks = _format_disks(psutil.disk_partitions())
        self._publish_response("disks", {
            "type": "disks", "device_id": DEVICE_ID, "disks": disks,
            "lines": [f"{d['device']} {d['free_gb']}/{d['total_gb']} ГБ свободно ({d['percent']}%)" for d in disks],
        })
        log.info("Список дисков отправлен")

    def _do_volume_set(self, payload: dict) -> None:
        """Установить общую громкость 0-100 (osascript)."""
        level = max(0, min(100, int(payload.get("level", 50) or 50)))
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"], check=False)
        result = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, check=False)
        try:
            current = int(result.stdout.strip())
        except ValueError:
            current = level
        self._publish_response("volume_set", {"type": "volume_set", "device_id": DEVICE_ID, "level": level, "current": current})
        log.info("Громкость установлена: %d%% (стало %d%%)", level, current)

    def _do_microphone_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
            "feature_id": "D02-F15", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_query_devices",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"inputs": []},
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"]["inputs"] = microphone_devices.project_input_devices(sd.query_devices())
            result["state"] = "supported" if result["data"]["inputs"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Microphone input enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("microphone_catalog", result)

    def _do_microphone_level(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_level", "schema": "xider.microphone-level.v1",
            "feature_id": "D02-F16", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_input_stream_level",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            import sounddevice as sd
            import microphone_level
            levels = microphone_level.measure_input_level(sd, payload.get("input_device_id"), duration_seconds=1.0)
            if levels is None:
                result["state"] = "unavailable"
                result["detail"] = "selected_microphone_unavailable"
            else:
                result["state"] = "supported"
                result["data"] = {**levels, "observation_ms": 1000}
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Microphone input-level measurement failed")
            result["state"] = "unknown"
            result["detail"] = "microphone_level_measurement_failed"
        self._publish_response("microphone_level", result)

    def _do_microphone_balance(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_balance", "schema": "xider.microphone-balance.v1",
            "feature_id": "D02-F21", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_stereo_input_rms",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"] = microphone_devices.measure_stereo_channel_balance(
                sd, input_device_id=payload.get("input_device_id"),
                input_device_name=payload.get("input_device_name"),
            )
            result["data"].pop("state", None)
            result["state"] = "supported"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            if str(exc) == "selected_stereo_microphone_unavailable":
                result["state"] = "unavailable"
                result["detail"] = str(exc)
            else:
                result["detail"] = "stereo_balance_measurement_failed"
        except Exception:
            log.exception("Microphone stereo-balance measurement failed")
            result["detail"] = "stereo_balance_measurement_failed"
        self._publish_response("microphone_balance", result)

    def _do_audio_output_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
            "feature_id": "D02-F19", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_query_devices",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"outputs": []},
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"]["outputs"] = microphone_devices.project_output_devices(sd.query_devices())
            result["state"] = "supported" if result["data"]["outputs"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Audio output enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("audio_output_catalog", result)

    def _do_audio_output_switch(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "audio_output_switch", "schema": "xider.audio-output-switch.v1",
            "feature_id": "D02-F18", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "data": None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            import sounddevice as sd
            import audio_output_control
            data = audio_output_control.switch_system_output(
                sd, output_id=payload.get("output_device_id"),
                output_name=payload.get("output_device_name"),
            )
            result["state"] = data.get("state", "unknown")
            if result["state"] in {"changed", "unchanged"}:
                result["data"] = {
                    "output_name": data["output_name"],
                    "previous_output_name": data["previous_output_name"],
                }
        except ImportError:
            result["state"] = "unsupported"
        except Exception:
            log.exception("System audio output switch failed")
        self._publish_response("audio_output_switch", result)

    def _do_microphone_test(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_test", "schema": "xider.microphone-test.v1",
            "feature_id": "D02-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "local_audio_roundtrip", "data": None,
        }
        try:
            import sounddevice as sd
            import soundfile as sf
            import microphone_devices
            data = microphone_devices.run_microphone_roundtrip(
                sd, sf, input_device_id=payload.get("input_device_id"),
                output_device_id=payload.get("output_device_id"),
                input_device_name=payload.get("input_device_name"),
                output_device_name=payload.get("output_device_name"),
            )
            result["state"] = data["state"]
            result["data"] = {"duration_ms": data["duration_ms"], "sample_rate": data["sample_rate"]}
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            result["state"] = "unavailable"
            result["detail"] = str(exc) if str(exc) in {"selected_audio_device_unavailable", "selected_audio_device_changed"} else "audio_roundtrip_failed"
        except Exception:
            log.exception("Local microphone/output test failed")
            result["state"] = "unknown"
            result["detail"] = "audio_roundtrip_failed"
        self._publish_response("microphone_test", result)

    def _do_microphone_preview(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_preview", "schema": "xider.microphone-preview.v1",
            "feature_id": "D02-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "local_playback_before_delivery", "audio": None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            import sounddevice as sd
            import soundfile as sf
            import microphone_devices
            duration = payload.get("duration")
            audio = microphone_devices.preview_microphone_recording(
                sd, sf, input_device_id=payload.get("input_device_id"),
                output_device_id=payload.get("output_device_id"), duration_seconds=duration,
                input_device_name=payload.get("input_device_name"),
                output_device_name=payload.get("output_device_name"),
            )
            result["state"] = "previewed"
            result["duration_ms"] = duration * 1000
            result["audio"] = base64.b64encode(audio).decode("ascii")
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            result["state"] = "unavailable"
            allowed = {"selected_audio_device_unavailable", "selected_audio_device_changed",
                       "microphone_preview_duration_invalid", "microphone_preview_audio_invalid"}
            result["detail"] = str(exc) if str(exc) in allowed else "microphone_preview_failed"
        except Exception:
            log.exception("Local microphone preview failed")
            result["state"] = "unknown"
            result["detail"] = "microphone_preview_failed"
        self._publish_response("microphone_preview", result)

    def _do_mic(self, payload: dict) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
            if "input_device_id" in payload:
                import microphone_devices
                input_index = microphone_devices.resolve_input_index(sd.query_devices(), payload.get("input_device_id"))
                if input_index is None:
                    self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID,
                                                    "audio": None, "state": "unavailable",
                                                    "error": "selected_microphone_unavailable"})
                    return
            else:
                input_index = None
            duration = int(payload.get("duration", 5))
            if duration <= 0 or duration > 60: duration = 5
            fs = 44100
            kwargs = {"samplerate": fs, "channels": 1}
            if input_index is not None:
                kwargs["device"] = input_index
            recording = sd.rec(int(duration * fs), **kwargs)
            sd.wait()
            with media_temp.create_media_temp_file(".ogg") as tmp:
                tmp_path = tmp.name
            try:
                sf.write(tmp_path, recording, fs)
                with open(tmp_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID, "audio": b64})
                log.info("Запись микрофона отправлена (%d сек)", duration)
            finally:
                if os.path.exists(tmp_path): os.unlink(tmp_path)
        except Exception as exc:
            log.exception("Ошибка микрофона")
            self._publish_response("mic", {
                "type": "mic",
                "device_id": DEVICE_ID,
                "audio": None,
                "error": f"Ошибка микрофона: {exc}",
            })

    def _do_shell(self, payload: dict) -> None:
        if not _reserve_shell_approval(self, payload):
            self._publish_response("shell", {
                "type": "shell", "device_id": DEVICE_ID, "ok": False,
                "error": "approval_invalid_or_replayed", "returncode": -1, "status": "rejected",
            })
            log.warning("Shell request rejected by approval or replay guard")
            return
        cmd = payload["command"]
        if not cmd.strip():
            return
        try:
            execution = _load_command_approval_component("shell_execution")
        except ImportError:
            result = {"status": "error", "returncode": -2,
                      "output": "Execution component unavailable"}
        else:
            approved_id = (payload.get("approval") or {}).get("request_id")
            result = execution.execute_shell(cmd, payload.get("timeout"), request_id=approved_id)
        self._publish_response("shell", {"type": "shell", "device_id": DEVICE_ID, **result})
        log.info("Shell executed (rc=%s)", result["returncode"])

    def _do_shell_cancel(self, payload: dict) -> None:
        """Stop only a currently running, previously approved shell request."""
        target_id = payload.get("target_request_id")
        try:
            execution = _load_command_approval_component("shell_execution")
            status = execution.cancel_shell(target_id)
        except Exception:
            status = "unknown"
        if status not in {"cancelled", "unknown"}:
            status = "unknown"
        self._publish_response("shell_cancel", {
            "type": "shell_cancel", "device_id": DEVICE_ID,
            "target_request_id": target_id if isinstance(target_id, str) and len(target_id) <= 128 else "",
            "status": status,
        })

    def _do_open_app(self, payload: dict) -> None:
        app = (payload.get("app") or "").strip()
        if app: subprocess.run(["open", app], check=False)

    def _do_permission_settings(self, payload: dict) -> dict:
        """Open one fixed macOS privacy pane; this never grants permission."""
        settings = {
            "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            "camera": "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
            "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            "location": "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices",
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "file_access": "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
        }
        permission = payload.get("permission")
        uri = settings.get(permission) if isinstance(permission, str) else None
        result = {
            "type": "permission_settings",
            "device_id": DEVICE_ID,
            "permission": permission if uri else "unknown",
            "ok": False,
        }
        if uri:
            try:
                opened = subprocess.run(
                    ["open", uri], check=False, capture_output=True, text=True, timeout=10
                )
                result["ok"] = opened.returncode == 0
                result["text"] = (
                    "Открыл нужный раздел Системных настроек. Выдай разрешение приложению XIDER вручную; агент сам его не включает."
                    if result["ok"] else
                    "macOS не подтвердила открытие настроек. Проверь активный сеанс пользователя и открой раздел вручную."
                )
            except (OSError, subprocess.SubprocessError):
                result["text"] = "Не удалось открыть настройки macOS. Проверь активный сеанс пользователя и открой раздел вручную."
        else:
            result["text"] = "Неизвестный раздел настроек; команда отклонена."
        command_id = payload.get("id") or getattr(self._command_context, "cmd_id", None)
        if isinstance(command_id, str) and command_id:
            result["id"] = command_id
        self._publish_response("permission_settings", result)
        return result

    def _do_capabilities(self, payload: dict) -> None:
        feature_status = _detect_feature_status()
        response = {
            "type": "capabilities", "device_id": DEVICE_ID, "platform": PLATFORM,
            "version": VERSION, "hostname": socket.gethostname(),
            "commands": sorted(SUPPORTED_COMMANDS),
            # `features` stays for older bot builds. It means prerequisites
            # were detected, not that hardware/TCC permissions were exercised.
            "features": {
                "battery": feature_status["battery"] == "supported",
                "shell": feature_status["shell"] == "supported",
                "open_app": feature_status["open_app"] == "supported",
                "mic": feature_status["microphone"] == "permission_unverified",
                "clipboard": feature_status["clipboard"] == "device_unverified",
            },
            "feature_status": feature_status,
            **_capability_snapshot(feature_status),
            "shell_runner": _shell_runner_diagnostic(self),
            "companions": _layer_health_snapshot(),
        }
        if _command_approval_protocol_enabled(self):
            response["command_approval_protocol"] = 1
        self._publish_response("capabilities", response)

    def _do_agent_logs(self, payload: dict) -> None:
        """Return bounded tails of both macOS agent log streams on owner request."""
        result = {
            "type": "agent_logs", "device_id": DEVICE_ID, "ok": False,
            "state": "unavailable", "lines": 0, "text": "",
        }
        sources = (
            ("xgent.log", CONFIG_DIR / "xgent.log"),
            ("agent.log", _agent_install_dir() / "agent.log"),
            # Frozen LaunchAgents capture Guardian stdout/stderr in CONFIG_DIR;
            # older source installs wrote the same log beside the checkout.
            ("guardian.log", CONFIG_DIR / "guardian.log"),
            ("guardian-runtime.log", _agent_install_dir() / "guardian.log"),
            ("safari.log", CONFIG_DIR / "safari.log"),
            ("siri.log", CONFIG_DIR / "siri.log"),
        )
        secret_patterns = (
            re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization|private[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"),
            re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        )
        clean_rows = []
        unavailable_sources = []
        truncated = False
        for label, log_path in sources:
            try:
                with log_path.open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 16 * 1024), os.SEEK_SET)
                    raw = stream.read(16 * 1024)
            except FileNotFoundError:
                continue
            except OSError:
                unavailable_sources.append(label)
                continue
            truncated = truncated or size > 16 * 1024
            text = raw.decode("utf-8", errors="replace")
            all_rows = text.splitlines()
            truncated = truncated or len(all_rows) > 40
            for row in all_rows[-40:]:
                for pattern in secret_patterns:
                    row = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex and match.lastindex >= 3 else "Bearer [REDACTED]", row)
                clean_rows.append(f"[{label}] {row[-480:]}")
        if not clean_rows and unavailable_sources:
            result.update(state="unavailable", text="Не удалось прочитать локальные журналы XIDER-компонентов.")
        elif not clean_rows:
            result.update(state="missing", text="Локальные журналы XIDER-компонентов ещё не созданы.")
        else:
            truncated = truncated or len(clean_rows) > 80
            selected = clean_rows[-80:]
            joined = "\n".join(selected)
            truncated = truncated or len(joined) > 8000
            if unavailable_sources:
                truncated = True
            result.update(
                ok=True, state="available", lines=len(selected), truncated=truncated,
                partial=bool(unavailable_sources), text=joined[-8000:],
            )
        self._publish_response("output", result)

    def _do_lock_screen(self, payload: dict) -> None:
        subprocess.run(["pmset", "displaysleepnow"], check=False)

    def _do_volume_toggle(self, payload: dict) -> None:
        result = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"], capture_output=True, text=True, check=False)
        is_muted = result.stdout.strip().lower() == "true"
        new_state = "false" if is_muted else "true"
        subprocess.run(["osascript", "-e", f"set volume output muted {new_state}"], check=False)

    def _do_proc_kill_name(self, payload: dict) -> None:
        low = (payload.get("name") or "").strip().lower()
        if not low:
            self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                      "device_id": DEVICE_ID, "ok": False,
                                                      "error": "empty name", "count": 0})
            return
        killed, errors = [], []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["pid"] == os.getpid():
                    continue
                if low in (proc.info.get("name") or "").lower():
                    proc.kill()
                    killed.append(proc.info["pid"])
            except Exception as exc:
                errors.append(str(exc))
        log.info("proc_kill_name(%s): %s", low, killed)
        self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                  "device_id": DEVICE_ID, "ok": True,
                                                  "count": len(killed), "killed": killed[:20],
                                                  "errors": errors[:5]})

    def _do_wifi_info(self, payload: dict) -> None:
        ssid = signal = None
        try:
            out = subprocess.check_output(
                ["ipconfig", "getsummary", "en0"], text=True,
                stderr=subprocess.DEVNULL, timeout=10,
            )
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID") and ":" in stripped and " SSID " not in stripped:
                    ssid = stripped.split(":", 1)[1].strip() or None
                if stripped.startswith("agrCtlRSSI"):
                    signal = stripped.split(":", 1)[1].strip() + " dBm"
        except Exception:
            pass
        text = f"📶 Wi-Fi: {ssid or '—'} (сигнал: {signal or '—'})"
        self._publish_response("wifi_info", {"type": "wifi_info", "device_id": DEVICE_ID,
                                             "ok": bool(ssid), "text": text,
                                             "ssid": ssid, "signal": signal})

    def _do_wifi_quality_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_quality.collect_wifi_quality()
        except Exception:
            sample = {"state": "unknown", "metric": None, "value": None,
                      "source": wifi_quality.MACOS_SOURCE, "detail": "wifi_query_failed"}
        metric, value = sample.get("metric"), sample.get("value")
        if sample.get("state") != "available":
            metric = value = None
        result = {
            "type": "wifi_quality_history", "schema": "xider.wifi-quality.v1", "feature_id": "D04-F15",
            "device_id": DEVICE_ID, "request_id": request_id, "source": wifi_quality.MACOS_SOURCE,
            "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
            "metric": metric, "value": value,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else "wifi_query_failed",
        }
        self._publish_response("wifi_quality_history", result)

    def _do_wifi_channel_summary(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_channel.collect_wifi_channel()
        except Exception:
            sample = {"state": "unknown", "channel": None, "width_mhz": None,
                      "width_state": "unknown", "source": wifi_channel.MACOS_SOURCE,
                      "detail": "wifi_query_failed"}
        channel = sample.get("channel") if sample.get("state") == "available" else None
        width = sample.get("width_mhz") if channel is not None else None
        width_state = sample.get("width_state") if width is None else "available"
        if width_state not in {"available", "unsupported", "unknown"}:
            width_state = "unknown"
        result = {
            "type": "wifi_channel_summary", "schema": "xider.wifi-channel.v1", "feature_id": "D04-F16",
            "device_id": DEVICE_ID, "request_id": request_id, "source": wifi_channel.MACOS_SOURCE,
            "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
            "channel": channel, "width_mhz": width, "width_state": width_state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else None,
        }
        self._publish_response("wifi_channel_summary", result)

    def _do_wifi_connection_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_connection_history.collect_wifi_connection_history()
        except Exception:
            sample = {"state": "unknown", "events": [], "detail": "event_log_unavailable"}
        state = sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown"
        events = []
        if state == "available" and isinstance(sample.get("events"), list):
            for event in sample["events"][-20:]:
                if (isinstance(event, dict) and isinstance(event.get("at"), str)
                        and event.get("kind") in {"connected", "disconnected"}):
                    events.append({"at": event["at"], "kind": event["kind"]})
        if state == "available" and not events:
            state = "unknown"
        detail = sample.get("detail")
        allowed_details = {"no_connection_events", "event_log_unavailable", "invalid_event_log"}
        result = {
            "type": "wifi_connection_history", "schema": "xider.wifi-connection-history.v1",
            "feature_id": "D04-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "source": wifi_connection_history.MACOS_SOURCE, "state": state, "events": events,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": detail if detail in allowed_details else ("invalid_event_log" if state == "unknown" else None),
        }
        self._publish_response("wifi_connection_history", result)

    def _do_proxy_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = proxy_status.collect_proxy_status()
        except Exception:
            sample = {"state": "unknown", "sources": []}
        state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
        sources = sample.get("sources") if state == "available" else []
        allowed = {"http", "https", "socks", "pac", "auto_detect"}
        if not isinstance(sources, list) or any(item not in allowed for item in sources) or len(set(sources)) != len(sources):
            state, sources = "unknown", []
        result = {
            "type": "proxy_status", "schema": "xider.proxy-status.v1", "feature_id": "D04-F23",
            "device_id": DEVICE_ID, "request_id": request_id, "source": proxy_status.MACOS_SOURCE,
            "state": state, "sources": sources,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if sample.get("detail") in {"scutil_unavailable", "scutil_query_failed"} else None,
        }
        self._publish_response("proxy_status", result)

    def _do_vpn_route_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = vpn_route.collect_vpn_summary()
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
        result = {
            "type": "vpn_route_status", "schema": "xider.vpn-route.v1", "feature_id": "D04-F24",
            "device_id": DEVICE_ID, "request_id": request_id, "source": vpn_route.MACOS_SOURCE,
            "state": state, "up_tunnel_candidates": up, "down_tunnel_candidates": down,
            "route_assessment": "not_verified",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if sample.get("detail") in {"psutil_unavailable", "interface_query_failed"} else None,
        }
        self._publish_response("vpn_route_status", result)

    def _do_network_traffic(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        interface_id = payload.get("interface_id")
        if interface_id is None:
            sample = network_traffic.collect_catalog()
            result = {
                "type": "network_traffic", "schema": "xider.network-traffic.v1",
                "feature_id": "D04-F18", "device_id": DEVICE_ID, "request_id": request_id,
                "source": network_traffic.SOURCE, "mode": "catalog",
                "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
                "interfaces": sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else [],
                "detail": sample.get("detail") if sample.get("detail") in {"counter_source_unavailable", "no_interfaces"} else None,
            }
        else:
            sample = network_traffic.collect_interface_traffic(interface_id)
            result = {
                "type": "network_traffic", "schema": "xider.network-traffic.v1",
                "feature_id": "D04-F18", "device_id": DEVICE_ID, "request_id": request_id,
                "source": network_traffic.SOURCE, "mode": "sample",
                "state": sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown",
                "interface_id": sample.get("interface_id"), "label": sample.get("label"),
                "sent_bytes": sample.get("sent_bytes"), "received_bytes": sample.get("received_bytes"),
                "sampled_at": sample.get("sampled_at"),
                "detail": sample.get("detail") if sample.get("detail") in {"counter_unavailable", "counter_source_unavailable", "interface_not_found"} else None,
            }
        result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        self._publish_response("network_traffic", result)

    def _do_usb_devices(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"],
                text=True, stderr=subprocess.DEVNULL, timeout=25,
            )
            items = [ln.strip() for ln in out.splitlines()
                     if ln.strip() and not ln.startswith("        ")]
            items = list(dict.fromkeys(items))[:25]
            text = "🔌 USB:\n" + "\n".join(f"• {i}" for i in items)
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ USB: {exc}", False
        self._publish_response("usb_devices", {"type": "usb_devices", "device_id": DEVICE_ID,
                                               "ok": ok, "text": text, "items": items})

    def _do_startup_list(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["osascript", "-e",
                 'tell application "System Events" to get the name of every login item'],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            items = [i.strip() for i in out.strip().split(",") if i.strip()]
            text = "🚀 Автозагрузка (login items):\n" + ("\n".join(f"• {i}" for i in items) or "(пусто)")
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ Автозагрузка: {exc}", False
        self._publish_response("startup_list", {"type": "startup_list", "device_id": DEVICE_ID,
                                                "ok": ok, "text": text, "items": items})

    def _do_env_get(self, payload: dict) -> None:
        SAFE_KEYS = ("PATH", "USER", "HOME", "SHELL", "TMPDIR", "LANG", "LOGNAME")
        raw = payload.get("names") or payload.get("name")
        wanted = {raw.upper()} if isinstance(raw, str) else {k.upper() for k in (raw or SAFE_KEYS)}
        env = {k: (v if len(v) < 400 else v[:397] + "...") for k, v in os.environ.items()
               if k.upper() in wanted}
        text = "\n".join(f"{k}={v}" for k, v in sorted(env.items())) or "(пусто)"
        self._publish_response("env_get", {"type": "env_get", "device_id": DEVICE_ID,
                                           "ok": True, "text": text[:3500], "env": env})

    def _do_netstat(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netstat", "-an", "-p", "tcp"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            lines = [ln for ln in out.splitlines() if ln.strip()][:40]
            text = "🕸 TCP-соединения (первые 40):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ netstat: {exc}", False
        self._publish_response("netstat", {"type": "netstat", "device_id": DEVICE_ID,
                                           "ok": ok, "text": text[:3500]})

    def _do_download_url(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        dest = (payload.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Downloads",
            os.path.basename(url.split("?")[0]) or "xgent_download.bin",
        )
        try:
            result = subprocess.run(
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

    def _do_power(self, payload: dict) -> None:
        action = payload.get("power_action") or payload.get("action", "")
        commands = {
            "shutdown": (["osascript", "-e", 'tell app "System Events" to shut down'], "⚡ Выключение Mac инициировано..."),
            "reboot": (["osascript", "-e", 'tell app "System Events" to restart'], "🔄 Перезагрузка Mac инициирована..."),
            "sleep": (["pmset", "sleepnow"], "😴 Mac переводится в режим сна..."),
        }
        if action not in commands:
            self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": action, "ok": False, "error": "unknown power action"})
            return
        command, text = commands[action]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
            ok = result.returncode == 0
            if not ok:
                text = (result.stderr or result.stdout or "команда macOS завершилась с ошибкой").strip()[:600]
        except Exception as exc:
            ok, text = False, str(exc)
        self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": action, "ok": ok, "error": None if ok else text})
        self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_stop(self, payload: dict) -> None:
        try:
            set_guardian_desired_running(False)
        except Exception as exc:
            log.exception("Не удалось сохранить явную остановку для Guardian")
            self._publish_response("output", {
                "type": "stop", "device_id": DEVICE_ID, "ok": False, "state": "stop_failed",
                "text": f"❌ Не удалось сохранить намерение остановки для Guardian: {exc}. Агент продолжает работу.",
            })
            return
        try:
            self._cancel_pending_update()
        except Exception:
            log.exception("Не удалось подготовить явную остановку агента")
        # KeepAlive у LaunchAgent иначе мгновенно поднимет процесс обратно.
        # При явной команде «Остановить агента» отключаем автозапуск заранее.
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        try:
            if os.path.exists(plist_path):
                os.remove(plist_path)
            label = f"gui/{os.getuid()}/com.xgent.agent"
            result = subprocess.run(
                ["launchctl", "bootout", label], capture_output=True, check=False,
            )
            if result.returncode != 0 and subprocess.run(
                ["launchctl", "print", label], capture_output=True, check=False,
            ).returncode == 0:
                raise RuntimeError("LaunchAgent остался загружен; остановка процесса вызвала бы повторный запуск.")
        except Exception:
            log.exception("Не удалось отключить LaunchAgent перед остановкой")
            self._publish_response("output", {
                "type": "stop", "device_id": DEVICE_ID, "ok": False,
                "text": "❌ Не удалось выгрузить LaunchAgent. Агент продолжает работу; проверьте agent.log.",
            })
            return
        self.request_stop()

    # ---------- волна 1: файлы, система, приколы (паритет с WDS) ----------

    def _do_dir_list(self, payload: dict) -> None:
        base = os.path.expandvars(os.path.expanduser((payload.get("path") or "~").strip()))
        try:
            entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
            files = [("📁 " if e.is_dir() else "📄 ") + e.name for e in entries[:60]]
            text = f"✅ {base} (всего {len(entries)}):\n" + ("\n".join(files) or "(пусто)")
            self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, "ok": True, "path": base, "text": text})
        except OSError as exc:
            self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, "ok": False, "path": base, "text": f"❌ {exc}"})

    def _do_file_get(self, payload: dict) -> None:
        path = os.path.expanduser((payload.get("path") or "").strip())
        try:
            size = os.path.getsize(path)
            if size > 45 * 1024 * 1024:
                self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": False, "error": f"Файл слишком большой: {size} байт (лимит 45 МБ)"})
                return
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": True,
                                                "path": path, "filename": os.path.basename(path), "data": data,
                                                # Aliases for older Windows bot builds.
                                                "name": os.path.basename(path), "b64": data})
        except Exception as exc:
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_file_put(self, payload: dict) -> None:
        try:
            content = _decode_file_put_payload(payload)
            path = os.path.expanduser((payload.get("path") or "").strip())
            if not path:
                name = os.path.basename((payload.get("name") or "file.bin").strip())
                base = os.path.expanduser((payload.get("dir") or "~").strip())
                path = os.path.join(base, name)
            _write_file_put_atomic(path, content)
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID, "ok": True, "path": path})
        except (OSError, TypeError, ValueError) as exc:
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_file_del(self, payload: dict) -> None:
        path = os.path.expanduser((payload.get("path") or "").strip())
        try:
            os.remove(path)
            self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID, "ok": True, "path": path})
        except Exception as exc:
            self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_find_file(self, payload: dict) -> None:
        pattern = (payload.get("pattern") or "").strip()
        root = os.path.expanduser((payload.get("root") or "~").strip())
        matches = []
        for current, dirs, files in os.walk(root):
            if current[len(root):].count(os.sep) >= 4:
                dirs[:] = []
                continue
            for name in files:
                if pattern.lower() in name.lower():
                    matches.append(os.path.join(current, name))
                    if len(matches) >= 30: break
            if len(matches) >= 30: break
        text = "\n".join(matches[:30]) if matches else "Ничего не найдено"
        self._publish_response("find_file", {"type": "find_file", "device_id": DEVICE_ID, "ok": True, "total": len(matches), "text": text})

    def _do_path_open(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if path:
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._publish_response("path_open", {"type": "path_open", "device_id": DEVICE_ID, "ok": True, "path": path})

    def _do_ext_ip(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "ext_ip", "schema": "xider.external-ip.v1", "feature_id": "D04-F30",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "ipify", "ip": "",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        for source, url in (("ipify", "https://api.ipify.org"), ("ifconfig_me", "https://ifconfig.me/ip")):
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    candidate = resp.read(65).decode("ascii", "ignore").strip()
                    address = ipaddress.ip_address(candidate)
                    if address.is_global and str(address) == candidate:
                        result.update(state="available", source=source, ip=candidate)
                        break
            except Exception:
                continue
        result["ok"] = result["state"] == "available"
        result["text"] = result["ip"] if result["ok"] else "Не удалось подтвердить внешний IP"
        self._publish_response("ext_ip", result)

    def _do_geo_location(self, payload: dict) -> None:
        """One explicit request; IP estimation requires the exact boolean opt-in."""
        from location_adapter import read_system_location, read_ip_estimate, location_text, compare_location_results
        value = read_system_location()
        if payload.get("compare_sources") is True:
            comparison = compare_location_results(value, read_ip_estimate())
            comparison.update(
                type="geo_location", device_id=DEVICE_ID, feature_id="P01-F25",
                schema="xider.location-comparison.v1", request_id=payload.get("id"),
                ok=(value.get("state") == "granted" or comparison["ip_estimate"].get("state") == "granted"),
                text=location_text(comparison),
            )
            self._publish_response("geo_location", comparison)
            return
        if value["state"] != "granted" and payload.get("ip_fallback") is True:
            system_state = value["state"]
            value = read_ip_estimate()
            value["system_state"] = system_state
        self._publish_response("geo_location", {
            "type": "geo_location", "device_id": DEVICE_ID,
            "ok": value["state"] == "granted", **value, "text": location_text(value),
        })

    def _do_wallpaper_set(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        b64 = (payload.get("b64") or "").strip()
        random_meme = payload.get("random_meme", False)
        MEME_URLS = [
            "https://i.imgflip.com/4/30b1gx.jpg",
            "https://i.imgflip.com/4/1g8my4.jpg",
            "https://i.imgflip.com/4/1bij.jpg",
            "https://i.imgflip.com/4/26am.jpg",
            "https://i.imgflip.com/4/1ur9b0.jpg",
            "https://cataas.com/cat?width=1920&height=1080",
            "https://picsum.photos/1920/1080",
        ]
        try:
            tmp = os.path.join(tempfile.gettempdir(), "xgent_wallpaper.jpg")
            if b64:
                raw_bytes = base64.b64decode(b64)
                with open(tmp, "wb") as f:
                    f.write(raw_bytes)
            elif random_meme or not url:
                target_url = random.choice(MEME_URLS)
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(tmp, "wb") as out:
                    out.write(resp.read())
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(tmp, "wb") as out:
                    out.write(resp.read())
            script = f'tell application "System Events" to tell every desktop to set picture to POSIX file "{tmp}"'
            result = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
            ok = result.returncode == 0
            text = "🖼 Обои успешно обновлены на Mac!" if ok else (result.stderr.strip() or "Не удалось установить обои")
            self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": ok, "error": None if ok else text})
            self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": ok, "text": text})
        except Exception as exc:
            self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})
            self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": False, "text": f"⚠️ Ошибка: {exc}"})

    def _do_msgbox_spam(self, payload: dict) -> None:
        count = min(int(payload.get("count", 5) or 5), 20)
        text = str(payload.get("text") or "Hello from XGENT!")[:200].replace('"', '\\"')
        def _spam():
            for _ in range(count):
                subprocess.run(["osascript", "-e", f'display dialog "{text}" buttons {{"OK"}} default button "OK" giving up after 10'], capture_output=True)
        threading.Thread(target=_spam, name="xgent-msgbox", daemon=True).start()
        self._publish_response("msgbox_spam", {"type": "msgbox_spam", "device_id": DEVICE_ID, "ok": True, "count": count})

    def _do_type_text(self, payload: dict) -> None:
        text = (payload.get("text") or "")[:500].replace("\\", "\\\\").replace('"', '\\"')
        def _typer():
            subprocess.run(["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'], capture_output=True)
        threading.Thread(target=_typer, name="xgent-typer", daemon=True).start()
        self._publish_response("type_text", {"type": "type_text", "device_id": DEVICE_ID, "ok": True, "len": len(text)})

    def _do_hotkey(self, payload: dict) -> None:
        spec = (payload.get("keys") or "").strip()
        mapping = {"ctrl": "control", "cmd": "command", "alt": "option", "shift": "shift",
                   "esc": "escape", "enter": "return", "space": "space", "tab": "tab", "del": "delete"}
        parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
        if not parts or any(p not in mapping and not (len(p) == 1 and p.isalnum()) for p in parts):
            self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID, "ok": False, "error": f"unknown keys: {spec}"})
            return
        mods = [mapping[p] for p in parts[:-1] if p in mapping]
        key = mapping.get(parts[-1], parts[-1])
        def _press():
            if mods:
                using = ", ".join(m + " down" for m in mods)
                script = f'tell application "System Events" to keystroke "{key}" using {{{using}}}'
            else:
                script = f'tell application "System Events" to keystroke "{key}"'
            subprocess.run(["osascript", "-e", script], capture_output=True)
        threading.Thread(target=_press, name="xgent-hotkey", daemon=True).start()
        self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID, "ok": True, "keys": spec})

    def _do_screen_off(self, payload: dict) -> None:
        result = subprocess.run(["pmset", "displaysleepnow"], capture_output=True, text=True, check=False)
        ok = result.returncode == 0
        self._publish_response("screen_off", {"type": "screen_off", "device_id": DEVICE_ID, "ok": ok,
                                               "error": None if ok else (result.stderr.strip() or "pmset failed")})

    def _do_screensaver_on(self, payload: dict) -> None:
        result = subprocess.run(["open", "-a", "ScreenSaverEngine"], capture_output=True, text=True, check=False)
        ok = result.returncode == 0
        self._publish_response("screensaver_on", {"type": "screensaver_on", "device_id": DEVICE_ID, "ok": ok,
                                                   "error": None if ok else (result.stderr.strip() or "ScreenSaverEngine failed")})

    def _do_prank_screamer(self, payload: dict) -> None:
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            subprocess.Popen(["open", url])
            for _ in range(5):
                subprocess.run(["osascript", "-e", "beep 3"], check=False)
        threading.Thread(target=_run, name="xgent-screamer", daemon=True).start()
        self._publish_response("prank_screamer", {"type": "prank_screamer", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_rickroll(self, payload: dict) -> None:
        tabs = min(int(payload.get("tabs", 10) or 10), 50)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        def _run():
            for _ in range(tabs):
                subprocess.Popen(["open", url])
                time.sleep(0.1)
        threading.Thread(target=_run, name="xgent-rickroll", daemon=True).start()
        self._publish_response("prank_rickroll", {"type": "prank_rickroll", "device_id": DEVICE_ID, "ok": True, "tabs": tabs})

    def _do_prank_matrix(self, payload: dict) -> None:
        script = 'tell application "Terminal" to do script "clear; echo -e \\"\\\\033[32m\\"; while true; do echo -n \\"1 0 1 1 0 0 1 0 1 0 1 \\"; done"'
        subprocess.Popen(["osascript", "-e", script])
        self._publish_response("prank_matrix", {"type": "prank_matrix", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_siren(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            deadline = time.time() + duration
            while time.time() < deadline:
                subprocess.run(["afplay", "/System/Library/Sounds/Sosumi.aiff"], check=False)
                time.sleep(0.2)
        threading.Thread(target=_run, name="xgent-siren", daemon=True).start()
        self._publish_response("prank_siren", {"type": "prank_siren", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_shout_tts(self, payload: dict) -> None:
        text = str(payload.get("text") or "ВНИМАНИЕ! СИСТЕМА ОБНАРУЖИЛА ОШИБКУ!")[:300].replace('"', '\\"')
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            subprocess.run(["say", "-v", "Milena", text], check=False)
        threading.Thread(target=_run, name="xgent-shout", daemon=True).start()
        self._publish_response("prank_shout_tts", {"type": "prank_shout_tts", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_swap_mouse(self, payload: dict) -> None:
        self._publish_response("prank_swap_mouse", {"type": "prank_swap_mouse", "device_id": DEVICE_ID, "ok": False,
                                                    "text": "⚠️ Смена кнопок мыши отключена: macOS не позволяет безопасно менять её удалённо без системного разрешения."})

    def _do_prank_crazy_cursor(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        try:
            import Quartz  # noqa: F401
        except Exception:
            self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID,
                                                           "ok": False, "text": "⚠️ Для движения курсора нужен PyObjC Quartz и разрешение Accessibility."})
            return
        def _run():
            try:
                import Quartz
                center_x, center_y = 700, 450
                deadline = time.time() + duration
                angle = 0.0
                import math
                while time.time() < deadline:
                    x = center_x + int(200 * math.cos(angle))
                    y = center_y + int(200 * math.sin(angle))
                    Quartz.CGWarpMouseCursorPosition((x, y))
                    angle += 0.2
                    time.sleep(0.02)
            except Exception as e:
                log.warning("Quartz crazy cursor failed: %s", e)
        threading.Thread(target=_run, name="xgent-crazycursor", daemon=True).start()
        self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_hide_desktop(self, payload: dict) -> None:
        try:
            hide = bool(payload.get("hide", True))
            val = "false" if hide else "true"
            subprocess.run(["defaults", "write", "com.apple.finder", "CreateDesktop", "-bool", val], check=False)
            subprocess.run(["killall", "Finder"], check=False)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_hide_desktop", {"type": "prank_hide_desktop", "device_id": DEVICE_ID, "ok": ok})

    def _do_prank_dancing_windows(self, payload: dict) -> None:
        script = '''
        tell application "System Events"
            set procList to every process whose visible is true
            repeat 10 times
                repeat with proc in procList
                    try
                        tell proc
                            set currentPos to position of window 1
                            set item 1 of currentPos to (item 1 of currentPos + 20)
                            set position of window 1 to currentPos
                            delay 0.05
                            set item 1 of currentPos to (item 1 of currentPos - 20)
                            set position of window 1 to currentPos
                        end tell
                    end try
                end repeat
            end repeat
        end tell
        '''
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", script], check=False), daemon=True).start()
        self._publish_response("prank_dancing_windows", {"type": "prank_dancing_windows", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_black_screen(self, payload: dict) -> None:
        subprocess.run(["pmset", "displaysleepnow"], check=False)
        self._publish_response("prank_black_screen", {"type": "prank_black_screen", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_random_site(self, payload: dict) -> None:
        SITES = [
            "https://hackertyper.net/",
            "https://pointerpointer.com/",
            "https://theuselessweb.com/",
            "https://cat-bounce.com/",
            "https://zoomquilt.org/",
        ]
        import random
        site = random.choice(SITES)
        subprocess.Popen(["open", site])
        self._publish_response("prank_random_site", {"type": "prank_random_site", "device_id": DEVICE_ID, "ok": True, "url": site})

    def _do_display_brightness(self, payload: dict) -> None:
        lvl = max(0, min(100, int(payload.get("level", 50))))
        if not shutil.which("brightness"):
            text = "⚠️ На macOS нет команды brightness. Установите её (brew install brightness) или используйте системный ползунок."
            ok = False
        else:
            try:
                subprocess.run(["brightness", str(lvl / 100)], check=True, timeout=8)
                text, ok = f"🔆 Яркость установлена на {lvl}%", True
            except Exception as exc:
                text, ok = f"⚠️ Не удалось изменить яркость: {exc}", False
        self._publish_response("display_brightness", {"type": "display_brightness", "device_id": DEVICE_ID, "ok": ok, "level": lvl, "text": text})
        self._publish_response("output", {"type": "display_brightness", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_display_night_light(self, payload: dict) -> None:
        text = "⚠️ Ночной свет macOS не имеет стабильного публичного CLI в агенте; переключите его в Пункте управления."
        self._publish_response("display_night_light", {"type": "display_night_light", "device_id": DEVICE_ID, "ok": False, "text": text})
        self._publish_response("output", {"type": "display_night_light", "device_id": DEVICE_ID, "ok": False, "text": text})

    def _do_display_rotate(self, payload: dict) -> None:
        angle = int(payload.get("angle", 0) or 0) % 360
        if angle not in (0, 90, 180, 270):
            text = "Угол должен быть 0, 90, 180 или 270 градусов."
            ok = False
        elif not shutil.which("displayplacer"):
            text = "Для поворота установите один раз: brew install displayplacer"
            ok = False
        else:
            try:
                listed = subprocess.run(["displayplacer", "list"], capture_output=True, text=True, timeout=10, check=False)
                import re
                match = re.search(r"displayplacer '([^']+)'", listed.stdout or "")
                if listed.returncode != 0 or not match:
                    raise RuntimeError((listed.stderr or listed.stdout or "не найден дисплей").strip())
                config = re.sub(r"\bdegree:\d+", f"degree:{angle}", match.group(1))
                result = subprocess.run(["displayplacer", config], capture_output=True, text=True, timeout=15, check=False)
                ok = result.returncode == 0
                text = f"Экран повернут на {angle}°" if ok else (result.stderr.strip() or "displayplacer завершился с ошибкой")
            except Exception as exc:
                ok, text = False, f"Не удалось повернуть экран: {exc}"
        self._publish_response("display_rotate", {"type": "display_rotate", "device_id": DEVICE_ID, "ok": ok, "text": text, "angle": angle})
        self._publish_response("output", {"type": "display_rotate", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_wifi_passwords(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["networksetup", "-getairportnetwork", "en0"], text=True, stderr=subprocess.DEVNULL)
            text = f"📶 Wi-Fi (macOS):\n{out.strip()}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Wi-Fi: {exc}", False
        self._publish_response("net_wifi_passwords", {"type": "net_wifi_passwords", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_bluetooth_list(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["system_profiler", "SPBluetoothDataType", "-detailLevel", "mini"], text=True, stderr=subprocess.DEVNULL, timeout=15)
            lines = [l.strip() for l in out.splitlines() if l.strip() and ("Connected" in l or "Device" in l or "Name" in l)][:20]
            text = "🔵 Bluetooth (macOS):\n" + ("\n".join(lines) or "(нет активных устройств)")
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Bluetooth: {exc}", False
        self._publish_response("net_bluetooth_list", {"type": "net_bluetooth_list", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_storage_smart(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["diskutil", "info", "/"], text=True, stderr=subprocess.DEVNULL, timeout=10)
            lines = [l.strip() for l in out.splitlines() if "SMART" in l or "Total Size" in l or "Free Space" in l or "Device / Media Name" in l]
            text = "🩺 SMART & Диск (macOS):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ SMART: {exc}", False
        self._publish_response("storage_smart", {"type": "storage_smart", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_installed_apps(self, payload: dict) -> None:
        try:
            apps = [a[:-4] for a in os.listdir("/Applications") if a.endswith(".app")][:35]
            text = "📦 Установленные приложения (/Applications):\n" + "\n".join(f"• {a}" for a in sorted(apps))
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения /Applications: {exc}", False
        self._publish_response("sys_installed_apps", {"type": "sys_installed_apps", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_history_cmd(self, payload: dict) -> None:
        lines = int(payload.get("lines", 30) or 30)
        hist_file = os.path.expanduser("~/.zsh_history")
        if not os.path.exists(hist_file):
            hist_file = os.path.expanduser("~/.bash_history")
        try:
            if os.path.exists(hist_file):
                with open(hist_file, "r", encoding="utf-8", errors="ignore") as f:
                    history = [ln.strip().split(";")[-1] for ln in f.readlines() if ln.strip()][-lines:]
                text = "🕘 История консоли (последние):\n" + "\n".join(f"• {h}" for h in history)
                ok = True
            else:
                text, ok = "История терминала не найдена.", True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения истории: {exc}", False
        self._publish_response("sys_history_cmd", {"type": "sys_history_cmd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    # ---------- Режим ожидания (Watchdog) и автозапуск ----------

    def _do_standby_sleep(self, payload: dict) -> None:
        """Перевод агента в спящий режим ожидания (Watchdog)."""
        self._standby = True
        self._publish_status()
        text = "💤 Агент переведён в спящий режим (Standby).\nБольшинство функций приостановлено. Для возобновления используйте кнопку «☀️ Пробудить агента»."
        self._publish_response("output", {"type": "standby_sleep", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _do_wake(self, payload: dict) -> None:
        """Пробуждение агента из спящего режима."""
        self._standby = False
        self._publish_status()
        text = "☀️ Агент успешно пробуждён и готов к приёму всех команд!"
        self._publish_response("output", {"type": "wake", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _do_autorun_status(self, payload: dict) -> None:
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        label = "gui/" + str(os.getuid()) + "/com.xgent.agent"
        loaded = subprocess.run(["launchctl", "print", label], capture_output=True, text=True, check=False).returncode == 0
        try:
            state_path = CONFIG_DIR / "guardian.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            desired = bool(state.get("startup_enabled", True))
        except Exception:
            desired = True
        enabled = os.path.exists(plist_path) and loaded and desired
        text = f"🚀 Автозапуск macOS: {'✅ ВКЛЮЧЕН' if enabled else '❌ ВЫКЛЮЧЕН'}\nФайл: {plist_path}"
        self._publish_response("output", {"type": "autorun_status", "device_id": DEVICE_ID, "ok": True, "text": text, "enabled": enabled})

    def _do_autorun_enable(self, payload: dict) -> None:
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist_path = os.path.join(plist_dir, "com.xgent.agent.plist")
        script_dir = str(_agent_install_dir())
        try:
            plist = {
                "Label": "com.xgent.agent",
                "ProgramArguments": _worker_launch_command(),
                "WorkingDirectory": script_dir,
                "RunAtLoad": True,
                # Guardian owns recovery; launchd only starts the worker at login.
                "KeepAlive": False,
                "ProcessType": "Background",
                "StandardOutPath": os.path.join(script_dir, "agent.log"),
                "StandardErrorPath": os.path.join(script_dir, "agent.log"),
            }
            with open(plist_path, "wb") as f:
                f.write(plistlib.dumps(plist))
            domain = f"gui/{os.getuid()}"
            subprocess.run(["launchctl", "enable", f"{domain}/com.xgent.agent"], capture_output=True, check=False)
            current = subprocess.run(
                ["launchctl", "print", f"{domain}/com.xgent.agent"],
                capture_output=True, text=True, check=False,
            )
            if current.returncode != 0:
                loaded = subprocess.run(["launchctl", "bootstrap", domain, plist_path], capture_output=True, text=True, check=False)
                if loaded.returncode != 0:
                    raise RuntimeError(loaded.stderr.strip() or "launchctl bootstrap failed")
            set_guardian_startup_enabled(True)
            text = f"✅ Автозапуск macOS включен и загружен.\nФайл: {plist_path}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка создания автозапуска: {exc}"
            ok = False
        self._publish_response("output", {"type": "autorun_enable", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_autorun_disable(self, payload: dict) -> None:
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        try:
            set_guardian_startup_enabled(False)
            if os.path.exists(plist_path):
                domain = f"gui/{os.getuid()}"
                disabled = subprocess.run(
                    ["launchctl", "disable", f"{domain}/com.xgent.agent"],
                    capture_output=True, text=True, check=False,
                )
                if disabled.returncode != 0:
                    raise RuntimeError(disabled.stderr.strip() or "launchctl disable failed")
                os.remove(plist_path)
                text = "🛑 Автозапуск macOS отключён. Текущий агент продолжит работать до остановки."
            else:
                text = "ℹ️ Автозапуск macOS уже был отключен."
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка отключения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {"type": "autorun_disable", "device_id": DEVICE_ID, "ok": ok, "text": text})

    # ---------- Системные утилиты и диагностика ----------

    def _do_sys_uptime(self, payload: dict) -> None:
        try:
            boot_ts = psutil.boot_time()
            uptime_secs = int(time.time() - boot_ts)
            days, rem = divmod(uptime_secs, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, secs = divmod(rem, 60)
            boot_dt = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(boot_ts))
            text = f"⏱ Время работы системы macOS (Uptime):\n• Аптайм: {days} дн. {hours} ч. {minutes} мин. {secs} сек.\n• Загрузка: {boot_dt}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка uptime: {exc}"
            ok = False
        self._publish_response("output", {"type": "sys_uptime", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_clean_temp(self, payload: dict) -> None:
        try:
            cache_dir = os.path.expanduser("~/Library/Caches")
            cleaned_files = 0
            freed_bytes = 0
            for root, dirs, files in os.walk(cache_dir, topdown=False):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        cleaned_files += 1
                        freed_bytes += sz
                    except Exception:
                        pass
            freed_mb = freed_bytes / (1024 * 1024)
            text = f"🧹 Очистка кэша macOS завершена!\n• Удалено файлов: {cleaned_files}\n• Освобождено: {freed_mb:.2f} МБ"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка очистки: {exc}"
            ok = False
        self._publish_response("output", {"type": "sys_clean_temp", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_ping(self, payload: dict) -> None:
        host = payload.get("host", "8.8.8.8")
        import re
        host = re.sub(r"[^\w\.-]", "", str(host)) or "8.8.8.8"
        try:
            res = subprocess.run(["ping", "-c", "4", host], capture_output=True, text=True, timeout=10)
            text = f"🏓 Результаты Ping к {host}:\n```\n{(res.stdout or res.stderr).strip()[:3500]}\n```"
            ok = (res.returncode == 0)
        except Exception as exc:
            text = f"⚠️ Ошибка ping: {exc}"
            ok = False
        self._publish_response("output", {"type": "net_ping", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_dns_resolution(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dns_resolution",
            "schema": "xider.dns-resolution.v1",
            "feature_id": "D04-F05",
            "device_id": DEVICE_ID,
            "request_id": request_id,
            "state": "supported",
            "source": "system_getaddrinfo",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None,
            "detail": None,
        }
        try:
            result["data"] = dns_diagnostics.resolve_dns(
                payload.get("hostname"), payload.get("expected_addresses"),
            )
        except ValueError:
            result.update(state="invalid_request", detail="invalid_dns_request")
        except TimeoutError:
            result.update(state="unknown", detail="dns_lookup_timeout_or_busy")
        except (LookupError, socket.gaierror):
            result.update(state="unavailable", detail="dns_name_unavailable")
        except OSError:
            result.update(state="unavailable", detail="system_resolver_unavailable")
        self._publish_response("dns_resolution", result)

    def _do_dns_settings(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            snapshot = dns_settings.read_dns_settings()
            resolvers = snapshot.get("resolvers", [])
            state = snapshot.get("state", "supported" if resolvers else "unknown")
            if state not in {"supported", "unknown", "unsupported", "unavailable"}:
                state, resolvers = "unknown", []
            if not isinstance(resolvers, list):
                state, resolvers = "unknown", []
        except Exception:
            snapshot = {"source": dns_settings.SOURCE}
            state, resolvers = "unknown", []
        result = {
            "type": "dns_settings",
            "schema": "xider.dns-settings.v1",
            "feature_id": "D04-F06",
            "device_id": DEVICE_ID,
            "request_id": request_id,
            "state": state,
            "source": snapshot.get("source", dns_settings.SOURCE),
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": {"resolvers": resolvers},
        }
        self._publish_response("dns_settings", result)

    def _do_dns_comparison(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dns_comparison", "schema": "xider.dns-comparison.v1", "feature_id": "D04-F07",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "supported",
            "source": dns_comparison.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": None,
        }
        try:
            result["data"] = dns_comparison.compare_resolvers(payload.get("hostname"), payload.get("resolvers"))
            if result["data"].get("comparison") == "unknown":
                result["state"] = "unknown"
        except ValueError:
            result.update(state="invalid_request", detail="invalid_dns_comparison_request")
        except (OSError, TimeoutError):
            result.update(state="unknown", detail="dns_resolver_unavailable")
        self._publish_response("dns_comparison", result)

    def _do_dnssec_check(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dnssec_check", "schema": "xider.dnssec-check.v1", "feature_id": "D04-F26",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": dnssec_check.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "hostname": None, "resolver": None, "ad_bit": None, "rcode": None,
        }
        try:
            checked = dnssec_check.check_domain(payload.get("hostname"), payload.get("resolver"))
            result.update(hostname=checked["hostname"], resolver=checked["resolver"],
                          state=checked["assessment"], ad_bit=checked["ad_bit"], rcode=checked["rcode"])
        except ValueError:
            result.update(state="invalid_request")
        except (OSError, TimeoutError):
            result.update(state="unknown")
        self._publish_response("dnssec_check", result)

    def _do_route_trace(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": route_trace.MACOS_SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": None,
        }
        try:
            traced = route_trace.trace_route(payload.get("target"), platform="macos")
            result["data"] = {key: traced[key] for key in ("target", "reached", "hops") if key in traced}
            result["source"] = traced.get("source", route_trace.MACOS_SOURCE)
            result["state"] = traced.get("state", "unknown")
            result["detail"] = traced.get("detail")
        except ValueError:
            result.update(state="invalid_request", detail="invalid_route_trace_target")
        except Exception:
            result.update(state="unknown", detail="route_trace_failed")
        self._publish_response("route_trace", result)

    def _do_path_mtu(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {"type": "path_mtu", "schema": "xider.path-mtu.v1", "feature_id": "D04-F10",
                  "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
                  "source": path_mtu.MACOS_SOURCE,
                  "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                  "data": None, "detail": None}
        try:
            snapshot = path_mtu.diagnose_path_mtu(payload.get("target"), platform="macos")
            result["source"], result["state"] = snapshot.get("source", path_mtu.MACOS_SOURCE), snapshot.get("state", "unknown")
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
        self._publish_response("path_mtu", result)

    def _do_captive_portal(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "captive_portal", "schema": "xider.captive-portal.v1", "feature_id": "D04-F11",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": captive_portal.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": "probe_failed",
        }
        try:
            snapshot = captive_portal.detect_captive_portal()
            result["state"] = snapshot.get("state", "unknown")
            result["source"] = snapshot.get("source", captive_portal.SOURCE)
            origin = snapshot.get("portal_origin")
            result["data"] = {"portal_origin": origin} if origin is not None else None
            result["detail"] = snapshot.get("detail")
        except Exception:
            pass
        self._publish_response("captive_portal", result)

    def _do_ipv6_connectivity(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "ipv6_connectivity", "schema": "xider.ipv6-connectivity.v1", "feature_id": "D04-F12",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": ipv6_connectivity.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": "probe_failed",
        }
        try:
            snapshot = ipv6_connectivity.probe_ipv6()
            state = snapshot.get("state")
            result["state"] = state if state in {"reachable", "no_route", "unknown", "unsupported"} else "unknown"
            result["source"] = snapshot.get("source", ipv6_connectivity.SOURCE)
            result["detail"] = snapshot.get("detail")
        except Exception:
            pass
        self._publish_response("ipv6_connectivity", result)

    def _do_tls_certificate(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        raw_host = payload.get("host")
        parsed = tls_certificate.parse_target(
            f"[{raw_host}]:{payload.get('port')}" if isinstance(raw_host, str) and ":" in raw_host
            else f"{raw_host}:{payload.get('port')}"
        )
        host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
        result = {
            "type": "tls_certificate", "schema": "xider.tls-certificate.v1", "feature_id": "D04-F13",
            "device_id": DEVICE_ID, "request_id": request_id, "host": host, "port": port,
            "source": tls_certificate.SOURCE, "state": "invalid_request" if not parsed else "unknown",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": "invalid_endpoint" if not parsed else "tls_probe_failed", "data": None,
        }
        if parsed:
            try:
                snapshot = tls_certificate.probe_certificate(host, port)
                if snapshot.get("state") in {"valid", "expired", "not_yet_valid", "chain_invalid", "unknown"}:
                    result.update(state=snapshot["state"], detail=snapshot.get("detail"), data=snapshot.get("data"))
            except Exception:
                pass
        self._publish_response("tls_certificate", result)

    def _do_service_probe(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        raw_host = payload.get("host")
        parsed = tls_certificate.parse_target(
            f"[{raw_host}]:{payload.get('port')}" if isinstance(raw_host, str) and ":" in raw_host
            else f"{raw_host}:{payload.get('port')}"
        )
        protocol = payload.get("protocol")
        valid = parsed is not None and protocol in {"tcp", "tls"}
        host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
        result = {
            "type": "service_probe", "schema": "xider.service-probe.v1", "feature_id": "D04-F14",
            "device_id": DEVICE_ID, "request_id": request_id, "host": host, "port": port,
            "protocol": protocol if protocol in {"tcp", "tls"} else None,
            "source": service_probe.SOURCE, "state": "unknown" if valid else "invalid_request",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": "probe_failed" if valid else "invalid_endpoint_or_protocol",
        }
        if valid:
            try:
                snapshot = service_probe.probe_service(host, port, protocol)
                if snapshot.get("state") in {"reachable", "reachable_untrusted", "unreachable", "unknown"}:
                    result.update(state=snapshot["state"], detail=snapshot.get("detail"))
            except Exception:
                pass
        self._publish_response("service_probe", result)

    def _do_latency_series(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        host, port = payload.get("host"), payload.get("port")
        valid = latency_series._valid_endpoint(host, port)
        result = {
            "type": "latency_series", "schema": "xider.latency-series.v1", "feature_id": "D04-F01",
            "device_id": DEVICE_ID, "request_id": request_id,
            "host": host if valid else None, "port": port if valid else None,
            "source": latency_series.SOURCE, "state": "unknown" if valid else "invalid_request",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "samples": [], "summary": None,
            "detail": "probe_failed" if valid else "invalid_endpoint",
        }
        if valid:
            try:
                snapshot = latency_series.measure_latency_series(host, port)
                if snapshot.get("source") == latency_series.SOURCE and snapshot.get("state") in {"available", "partial", "unknown"}:
                    result.update(state=snapshot["state"], samples=snapshot["samples"],
                                  summary=snapshot["summary"], detail=snapshot["detail"])
            except Exception:
                pass
        self._publish_response("latency_series", result)

    def _do_packet_loss_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        address = payload.get("address")
        valid = packet_loss_history._literal_address(address)
        snapshot = None
        if valid:
            try:
                snapshot = packet_loss_history.measure_packet_loss(address=valid)
            except Exception:
                pass
        result = {
            "type": "packet_loss_history", "schema": "xider.packet-loss-history.v1", "feature_id": "D04-F02",
            "device_id": DEVICE_ID, "request_id": request_id,
            "address": valid, "source": packet_loss_history.SOURCE,
            "state": snapshot.get("state") if snapshot and snapshot.get("state") in {"complete", "partial", "unknown"} else "invalid_request" if not valid else "unknown",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": snapshot if snapshot and snapshot.get("source") == packet_loss_history.SOURCE else {
                "address": valid, "state": "unknown", "source": packet_loss_history.SOURCE,
                "detail": "probe_failed" if valid else "invalid_literal_address", "sent": 10,
                "observed": 0, "received": 0, "lost": 0, "loss_percent": None, "samples": [],
            },
        }
        self._publish_response("packet_loss_history", result)

    def _do_jitter_measurement(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        valid = packet_loss_history._literal_address(payload.get("address"))
        snapshot = None
        if valid:
            try:
                snapshot = jitter_measurement.measure_jitter(address=valid)
            except Exception:
                pass
        state = snapshot.get("state") if snapshot else "unknown"
        if state not in {"available", "partial", "unknown"}:
            state = "invalid_request" if not valid else "unknown"
        data = snapshot if snapshot and snapshot.get("source") == jitter_measurement.SOURCE else {
            "address": valid, "state": "unknown", "source": jitter_measurement.SOURCE,
            "detail": "probe_failed" if valid else "invalid_literal_address",
            "attempted": 10, "received": 0, "lost": 0, "unknown": 10,
            "pairs": 0, "jitter_ms": None, "samples": [],
        }
        self._publish_response("network_jitter", {
            "type": "network_jitter", "schema": "xider.network-jitter.v1", "feature_id": "D04-F03",
            "device_id": DEVICE_ID, "request_id": request_id, "address": valid,
            "source": jitter_measurement.SOURCE, "state": state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": data,
        })

    def _do_network_throughput(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        endpoint = network_throughput.parse_endpoint(payload.get("endpoint"))
        result = network_throughput.measure_throughput(endpoint["url"]) if endpoint else {
            "state": "invalid_request", "source": network_throughput.SOURCE,
            "detail": "invalid_test_endpoint",
        }
        state = result.get("state") if result.get("state") in {"available", "unknown", "limit_exceeded", "invalid_request"} else "unknown"
        self._publish_response("network_throughput", {
            "type": "network_throughput", "schema": "xider.network-throughput.v1", "feature_id": "D04-F04",
            "device_id": DEVICE_ID, "request_id": request_id,
            "endpoint": endpoint["url"] if endpoint else None,
            "source": network_throughput.SOURCE, "state": state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": result,
        })

    def _do_network_link_speed(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        interface_id = payload.get("interface_id")
        catalog = network_link_speed.collect_catalog()
        if interface_id is None:
            data = {"state": catalog["state"], "interfaces": catalog["interfaces"], "detail": catalog["detail"]}
            mode = "catalog"
        else:
            data = network_link_speed.collect_link_speed(interface_id)
            mode = "measure"
        state = data.get("state") if data.get("state") in {"available", "unsupported", "unknown"} else "unknown"
        self._publish_response("network_link_speed", {
            "type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
            "device_id": DEVICE_ID, "request_id": request_id, "mode": mode,
            "source": network_link_speed.SOURCE, "state": state,
            "interface_id": interface_id if isinstance(interface_id, str) else None,
            "data": data,
        })

    def _do_route_lookup(self, payload: dict) -> None:
        request_id = str(payload.get("id", ""))[:128]
        destination = payload.get("destination")
        try:
            normalized = route_lookup.normalize_destination(destination)
            data = route_lookup.lookup_route(normalized)
        except (ValueError, TypeError):
            data = {"state": "invalid_request"}
        self._publish_response("route_lookup", {
            "type": "route_lookup", "schema": "xider.route-lookup.v1", "feature_id": "D04-F22",
            "request_id": request_id, "device_id": DEVICE_ID,
            "source": route_lookup.MACOS_SOURCE, "state": data.get("state", "unknown"),
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"), "data": data,
        })

    def _do_endpoint_batch(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        ids = ("example", "cloudflare", "github")
        try:
            sample = endpoint_batch.check_public_set()
        except Exception:
            sample = {"preset": endpoint_batch.PRESET_ID, "state": "unknown", "results": []}
        state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
        rows = sample.get("results")
        results = []
        if (sample.get("preset") != endpoint_batch.PRESET_ID or not isinstance(rows, list)
                or len(rows) != len(ids)):
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
        result = {
            "type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
            "device_id": DEVICE_ID, "request_id": request_id, "source": endpoint_batch.MACOS_SOURCE,
            "preset": endpoint_batch.PRESET_ID, "state": state, "results": results,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._publish_response("endpoint_batch", result)

    def _do_gateway_probe(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            snapshot = gateway_probe.probe_gateway()
        except Exception:
            snapshot = {"state": "unknown", "gateway": None,
                        "source": gateway_probe.SOURCE, "detail": "gateway_probe_failed"}
        state = snapshot.get("state")
        if state not in {"reachable", "unknown", "no_gateway", "unsupported"}:
            state = "unknown"
        gateway = snapshot.get("gateway")
        try:
            gateway = str(ipaddress.IPv4Address(gateway)) if gateway is not None else None
        except (ipaddress.AddressValueError, TypeError):
            gateway, state = None, "unknown"
        result = {
            "type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
            "device_id": DEVICE_ID, "request_id": request_id, "state": state,
            "source": gateway_probe.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": {"gateway": gateway} if gateway else None,
            "detail": snapshot.get("detail") if isinstance(snapshot.get("detail"), str) else None,
        }
        self._publish_response("gateway_probe", result)

    # ---------- Расширенная матрица приколов ----------

    def _do_prank_bsod(self, payload: dict) -> None:
        try:
            html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Kernel Panic</title>
<style>body{background:#000;color:#ccc;font-family:monospace;padding:50px;user-select:none;}</style>
</head><body><p>panic(cpu 0 caller 0xffffff801e0a6d1b): "Kernel trap at 0xffffff801e0a6d1b, type 14=page fault"</p>
<p>Debugger called: &lt;panic&gt;</p><p>Backtrace (CPU 0), Frame : Return Address</p>
<p>0xffffff924619b9e0 : 0xffffff801dfb0cb7</p><p>BSD process name corresponding to current thread: kernel_task</p>
<p>Mac OS version: 24.1.0</p><p>System uptime in nanoseconds: 129481928412</p>
<script>setTimeout(()=>window.close(),4000);document.addEventListener('click',()=>window.close());</script>
</body></html>"""
            tf = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
            tf.write(html)
            tf.close()
            subprocess.Popen(["open", tf.name])
            text = "💀 Фейковый экран Kernel Panic открыт на экране Mac!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_bsod", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_fake_update(self, payload: dict) -> None:
        scene_id = uuid.uuid4().hex
        try:
            command = [sys.executable]
            if not _BUNDLED_RUNTIME:
                command.append(str(Path(__file__).resolve()))
            command.append("--xider-fake-update")
            child = subprocess.Popen(command, close_fds=True)
            if child.poll() is not None:
                raise RuntimeError("дочерний процесс сразу завершился")
            self._register_prank_cancel(scene_id, lambda: self._terminate_owned_child(child))
            threading.Thread(
                target=self._forget_prank_child,
                args=(scene_id, child),
                name="xgent-fake-update-waiter",
                daemon=True,
            ).start()
            text = "⏳ Запускаю локальный полноэкранный демо-экран macOS. Он закроется через 30 секунд; можно закрыть кнопкой."
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_fake_update", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_toast_spam(self, payload: dict) -> None:
        def _spam():
            for i in range(1, 6):
                subprocess.run(["osascript", "-e", f'display notification "Вторжение печенья #{i}!" with title "XGENT Security"'], check=False)
                time.sleep(0.7)
        threading.Thread(target=_spam, daemon=True).start()
        self._publish_response("output", {"type": "prank_toast_spam", "device_id": DEVICE_ID, "ok": True, "text": "🔔 Серия из 5 уведомлений macOS отправлена!"})

    def _do_prank_keyboard_disco(self, payload: dict) -> None:
        def _disco():
            for _ in range(5):
                subprocess.run(["osascript", "-e", 'beep 2'], check=False)
                time.sleep(0.3)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_keyboard_disco", "device_id": DEVICE_ID, "ok": True, "text": "💃 Звуковая дискотека запущена на Mac!"})

    def _do_prank_beep_morse(self, payload: dict) -> None:
        def _morse():
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep'], check=False)
                time.sleep(0.2)
            time.sleep(0.4)
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep 2'], check=False)
                time.sleep(0.4)
            time.sleep(0.4)
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep'], check=False)
                time.sleep(0.2)
        threading.Thread(target=_morse, daemon=True).start()
        self._publish_response("output", {"type": "prank_beep_morse", "device_id": DEVICE_ID, "ok": True, "text": "📻 Сигнал Морзе (S.O.S) прозвучал на Mac!"})

    def _do_prank_open_notepad_type(self, payload: dict) -> None:
        msg = payload.get("text") or "Привет... Я наблюдаю за тобой через веб-камеру 👁️"
        clean = msg.replace('"', '\\"')
        sc = f'''tell application "TextEdit"
activate
make new document
tell application "System Events"
keystroke "{clean}"
end tell
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_open_notepad_type", "device_id": DEVICE_ID, "ok": True, "text": f"📝 TextEdit открыт, текст '{msg[:35]}...' печатается!"})

    def _do_prank_hacker_typer(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://hackertyper.net"])
        self._publish_response("output", {"type": "prank_hacker_typer", "device_id": DEVICE_ID, "ok": True, "text": "🧑‍💻 Hacker Typer открыт в браузере!"})

    def _do_prank_sound_spooky(self, payload: dict) -> None:
        def _say():
            subprocess.run(["say", "-v", "Bad News", "System compromised, prepare for landing"], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_spooky", "device_id": DEVICE_ID, "ok": True, "text": "👻 Жуткий потусторонний голос воспроизведён на Mac!"})

    def _do_prank_sound_fart(self, payload: dict) -> None:
        def _snd():
            subprocess.run(["afplay", "/System/Library/Sounds/Basso.aiff"], check=False)
        threading.Thread(target=_snd, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_fart", "device_id": DEVICE_ID, "ok": True, "text": "💨 Смешной звуковой эффект воспроизведён на Mac!"})

    def _do_prank_minimize_all(self, payload: dict) -> None:
        sc = 'tell application "System Events" to set miniaturized of every window of (every process whose visible is true) to true'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_minimize_all", "device_id": DEVICE_ID, "ok": True, "text": "📉 Все окна свернуты на рабочий стол Mac!"})

    def _do_prank_open_calc_spam(self, payload: dict) -> None:
        for _ in range(5):
            subprocess.Popen(["open", "-n", "-a", "Calculator"])
            time.sleep(0.2)
        self._publish_response("output", {"type": "prank_open_calc_spam", "device_id": DEVICE_ID, "ok": True, "text": "🔢 5 калькуляторов открыты каскадом!"})

    def _do_prank_invert_screen(self, payload: dict) -> None:
        sc = 'tell application "System Events" to display dialog "Режим рентгеновского зрения активирован! 🕶️" with title "macOS Display" buttons {"OK"} default button "OK"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Реальная инверсия экрана не применена: macOS не даёт надёжного безопасного CLI. Показано только шуточное окно."})

    def _do_prank_slow_mouse(self, payload: dict) -> None:
        key = "com.apple.mouse.scaling"
        try:
            original = subprocess.run(
                ["defaults", "read", "-g", key], capture_output=True, text=True, check=True,
            ).stdout.strip()
            original_value = float(original)
            if not math.isfinite(original_value):
                raise ValueError("mouse scaling is not finite")
            changed = subprocess.run(
                ["defaults", "write", "-g", key, "0.1"], capture_output=True, text=True, check=False,
            )
            if changed.returncode != 0:
                raise RuntimeError(changed.stderr.strip() or "defaults write failed")
        except Exception as exc:
            self._publish_response("output", {"type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": False,
                                                "text": f"⚠️ Скорость мыши не менялась: исходное значение не удалось прочитать или применить ({exc})."})
            return

        scene_id = uuid.uuid4().hex
        done = threading.Event()
        restore_lock = threading.Lock()

        def restore() -> None:
            with restore_lock:
                if done.is_set():
                    return
                done.set()
                restored = subprocess.run(
                    ["defaults", "write", "-g", key, str(original_value)],
                    capture_output=True, text=True, check=False,
                )
                if restored.returncode != 0:
                    log.error("Не удалось восстановить исходную скорость мыши: %s", restored.stderr.strip())
            self._finish_prank_cancel(scene_id)

        self._register_prank_cancel(scene_id, restore)

        def _slow_timer() -> None:
            if not done.wait(8):
                restore()

        threading.Thread(target=_slow_timer, name="xgent-slow-mouse-timer", daemon=True).start()
        self._publish_response("output", {"type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": True,
                                            "text": "🐌 Скорость мыши снижена на 8 секунд; остановка восстановит исходное значение."})

    def _do_prank_random_clicks(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_random_clicks", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Случайные клики не выполняются: функция не реализована безопасно."})

    def _do_prank_paste_clipboard_spam(self, payload: dict) -> None:
        meme = "( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%"
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(meme.encode("utf-8"))
        self._publish_response("output", {"type": "prank_paste_clipboard_spam", "device_id": DEVICE_ID, "ok": True, "text": f"📋 В буфер обмена помещён мем:\n'{meme}'"})

    def _do_prank_type_reversed(self, payload: dict) -> None:
        try:
            cur = subprocess.check_output(["pbpaste"], text=True) or "Привет мир"
            rev = cur[::-1]
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(rev.encode("utf-8"))
            text = f"🔄 Буфер обмена инвертирован задом наперёд:\n{rev[:100]}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка: {exc}", False
        self._publish_response("output", {"type": "prank_type_reversed", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_volume_jump(self, payload: dict) -> None:
        def _jump():
            for _ in range(3):
                subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
                time.sleep(0.4)
                subprocess.run(["osascript", "-e", "set volume output volume 15"], check=False)
                time.sleep(0.4)
            subprocess.run(["osascript", "-e", "set volume output volume 50"], check=False)
        threading.Thread(target=_jump, daemon=True).start()
        self._publish_response("output", {"type": "prank_volume_jump", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Скачки громкости (100% <-> 15%) запущены на Mac!"})

    def _do_prank_say_whisper(self, payload: dict) -> None:
        phrase = payload.get("text") or "Обернись... Сзади никого нет..."
        def _say():
            subprocess.run(["say", "-v", "Whisper", phrase], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_say_whisper", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Шёпот воспроизведён: '{phrase}'"})

    def _do_prank_fake_virus(self, payload: dict) -> None:
        sc = 'display alert "Критическая ошибка!" message "Вирус кваса проник в систему macOS." as critical'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_fake_virus", "device_id": DEVICE_ID, "ok": True, "text": "🦠 Шуточное предупреждение о вирусе показано!"})

    def _do_prank_open_cd(self, payload: dict) -> None:
        try:
            subprocess.run(["drutil", "tray", "open"], check=False)
            text = "💿 Команда открытия привода отправлена!"
            ok = True
        except Exception as exc:
            text = f"💿 Привод не обнаружен: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_cd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_change_wallpaper(self, payload: dict) -> None:
        backup = CONFIG_DIR / "wallpaper_backup.json"
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get POSIX path of picture of every desktop'],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "не удалось прочитать текущие обои")
            paths = [p.strip() for p in result.stdout.strip().split(",") if p.strip()]
            if not paths:
                raise RuntimeError("macOS не вернула текущие обои")
            backup.write_text(json.dumps(paths, ensure_ascii=False), encoding="utf-8")
            self._do_wallpaper_set({"random_meme": True})
            self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": True, "text": "🖼️ Обои заменены; исходные сохранены для восстановления."})
        except Exception as exc:
            self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": False, "text": f"⚠️ Обои не изменены: {exc}"})

    def _restore_saved_prank_wallpaper(self) -> tuple[bool, str]:
        backup = CONFIG_DIR / "wallpaper_backup.json"
        try:
            paths = json.loads(backup.read_text(encoding="utf-8"))
            if not isinstance(paths, list) or not paths:
                raise RuntimeError("резервная копия обоев пуста")
            for path in paths:
                script = f'tell application "System Events" to tell every desktop to set picture to POSIX file {json.dumps(path)}'
                result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "osascript failed")
            backup.unlink(missing_ok=True)
            return True, "✅ Исходные обои восстановлены."
        except Exception as exc:
            return False, f"⚠️ Не удалось восстановить обои: {exc}"

    def _do_prank_restore_wallpaper(self, payload: dict) -> None:
        ok, text = self._restore_saved_prank_wallpaper()
        self._publish_response("output", {"type": "prank_restore_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_speak_time(self, payload: dict) -> None:
        now = time.strftime("%H часов %M минут")
        phrase = f"Внимание! Точное время на вашей планете: {now}"
        def _say():
            subprocess.run(["say", phrase], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_speak_time", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Время озвучено: '{phrase}'"})

    def _do_prank_rickroll_terminal(self, payload: dict) -> None:
        sc = 'tell application "Terminal" to do script "curl -s parrot.live"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_rickroll_terminal", "device_id": DEVICE_ID, "ok": True, "text": "🕺 Терминал с ASCII анимацией запущен на Mac!"})

    def _do_prank_screen_off_brief(self, payload: dict) -> None:
        subprocess.Popen(["pmset", "displaysleepnow"])
        self._publish_response("output", {"type": "prank_screen_off_brief", "device_id": DEVICE_ID, "ok": True, "text": "🌑 Экран временно погашен на 3 секунды!"})

    def _do_prank_alert_loop(self, payload: dict) -> None:
        sc = 'tell application "System Events" to display dialog "Вы действительно хотите удалить Интернет?" with title "Apple Finder" buttons {"Да", "Точно да"} default button "Да"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_alert_loop", "device_id": DEVICE_ID, "ok": True, "text": "❓ Шуточный диалог показан на Mac!"})

    def _do_prank_open_browser_memes(self, payload: dict) -> None:
        urls = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://cat-bounce.com/", "https://heeeeeeeey.com/"]
        for u in urls:
            subprocess.Popen(["open", u])
            time.sleep(0.3)
        self._publish_response("output", {"type": "prank_open_browser_memes", "device_id": DEVICE_ID, "ok": True, "text": "🌐 3 вкладки с мемами открыты в Safari/браузере!"})

    def _do_prank_glitch_cursor(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_glitch_cursor", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Глючный курсор отключён: macOS не даёт безопасно рисовать курсор без Quartz/Accessibility."})

    def _do_prank_shake_window(self, payload: dict) -> None:
        sc = '''tell application "System Events"
set frontApp to name of first application process whose frontmost is true
tell process frontApp
try
set {x, y} to position of window 1
repeat 6 times
set position of window 1 to {x + 20, y}
delay 0.05
set position of window 1 to {x - 20, y}
delay 0.05
end repeat
set position of window 1 to {x, y}
end try
end tell
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_shake_window", "device_id": DEVICE_ID, "ok": True, "text": "🫨 Активное окно трясётся на Mac!"})

    def _do_prank_meme_wallpaper(self, payload: dict) -> None:
        payload["random_meme"] = True
        self._do_wallpaper_set(payload)

    def _do_prank_caps_disco(self, payload: dict) -> None:
        def _disco():
            for _ in range(8):
                subprocess.run(["osascript", "-e", 'beep'], capture_output=True)
                time.sleep(0.3)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_caps_disco", "device_id": DEVICE_ID, "ok": True, "text": "✨ Дискотека сигналов запущена на Mac!"})

    def _do_prank_cursor_circle(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_cursor_circle", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Кружение курсора недоступно в текущей сборке macOS."})

    def _do_prank_fake_error_spam(self, payload: dict) -> None:
        def _errs():
            for _ in range(8):
                subprocess.run(["afplay", "/System/Library/Sounds/Basso.aiff"], capture_output=True)
                time.sleep(0.2)
        threading.Thread(target=_errs, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_error_spam", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Спам звуками критической ошибки запущен на Mac!"})

    def _do_prank_fake_delete_sys32(self, payload: dict) -> None:
        sc = '''tell application "Terminal"
activate
do script "echo '[CRITICAL] Purging /System/Library...'; sleep 1; ls -R /System; clear; echo '[FATAL] macOS Core System Destroyed!'; sleep 3; echo 'JUST A PRANK BY XGENT :)'; sleep 5; exit"
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_fake_delete_sys32", "device_id": DEVICE_ID, "ok": True, "text": "💻 Фейковое удаление системы запущено в Terminal Mac!"})

    def _do_prank_ghost_typer(self, payload: dict) -> None:
        sc = 'tell application "System Events" to keystroke " 👻 Привет... Я живу внутри твоего Mac... "'
        threading.Thread(target=lambda: (time.sleep(1), subprocess.run(["osascript", "-e", sc], capture_output=True)), daemon=True).start()
        self._publish_response("output", {"type": "prank_ghost_typer", "device_id": DEVICE_ID, "ok": True, "text": "👻 Призрачный набор текста активирован на Mac!"})

    def _do_prank_fbi_lock(self, payload: dict) -> None:
        sc = 'display alert "ВНИМАНИЕ! IP-адрес зафиксирован Федеральной Службой Безопасности." message "Веб-камера ведет запись." as critical'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_fbi_lock", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Предупреждение от спецслужб выведено на Mac!"})

    def _do_prank_cat_invaders(self, payload: dict) -> None:
        urls = ["https://cataas.com/cat/says/HELLO%20HUMAN", "https://cataas.com/cat/gif", "https://cataas.com/cat"]
        for u in urls:
            subprocess.Popen(["open", u])
            time.sleep(0.3)
        self._publish_response("output", {"type": "prank_cat_invaders", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Нашествие котиков открыто в Safari!"})

    def _do_prank_fake_ransom_cats(self, payload: dict) -> None:
        sc = 'display dialog "🐱 ВСЕ ВАШИ ФАЙЛЫ ЗАХВАЧЕНЫ КОТАМИ!\\n\\nПоложите 3 банки тунца перед экраном." buttons {"Погладить кота"} default button 1 with icon caution'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_ransom_cats", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Кошачий шуточный вымогатель выведен на Mac!"})

    def _do_prank_nyan_stream(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://www.youtube.com/watch?v=QH2-TGUlwu4"])
        self._publish_response("output", {"type": "prank_nyan_stream", "device_id": DEVICE_ID, "ok": True, "text": "🌈 Nyan Cat с музыкой запущен на Mac!"})

    def _do_prank_random_beeps(self, payload: dict) -> None:
        def _beeps():
            for _ in range(6):
                subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], capture_output=True)
                time.sleep(0.2)
        threading.Thread(target=_beeps, daemon=True).start()
        self._publish_response("output", {"type": "prank_random_beeps", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Серия сигналов запущена на Mac!"})

    def _do_prank_confetti_winner(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://cat-bounce.com/"])
        self._publish_response("output", {"type": "prank_confetti_winner", "device_id": DEVICE_ID, "ok": True, "text": "🎰 Экран с выигрышем открыт на Mac!"})

    def _do_prank_low_battery_fake(self, payload: dict) -> None:
        sc = 'display alert "Критический разряд батареи" message "Осталось 0% заряда. Mac выключится через 10 секунд." as warning'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_low_battery_fake", "device_id": DEVICE_ID, "ok": True, "text": "🪫 Оповещение о 0% батареи показано на Mac!"})

    def _do_prank_earthquake(self, payload: dict) -> None:
        self._do_prank_shake_window(payload)

    def _do_prank_laugh_track(self, payload: dict) -> None:
        subprocess.Popen(["say", "-v", "Milena", "Ха ха ха ха! Мва ха ха ха! Я управляю этим компьютером!"])
        self._publish_response("output", {"type": "prank_laugh_track", "device_id": DEVICE_ID, "ok": True, "text": "😈 Зловещий смех запущен на Mac!"})

    def _prank_cancel_registry(self):
        lock = getattr(self, "_active_prank_cancels_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._active_prank_cancels_lock = lock
            self._active_prank_cancels = {}
        return lock, self._active_prank_cancels

    def _register_prank_cancel(self, scene_id: str, callback) -> None:
        if not isinstance(scene_id, str) or not scene_id or not callable(callback):
            raise ValueError("prank cancellation registration is invalid")
        lock, registry = self._prank_cancel_registry()
        with lock:
            registry[scene_id] = callback

    def _finish_prank_cancel(self, scene_id: str) -> None:
        lock, registry = self._prank_cancel_registry()
        with lock:
            registry.pop(scene_id, None)

    @staticmethod
    def _terminate_owned_child(child) -> None:
        if child.poll() is None:
            child.terminate()

    def _forget_prank_child(self, scene_id: str, child) -> None:
        try:
            child.wait()
        except Exception:
            log.exception("Не удалось дождаться XIDER prank child %s", scene_id)
        finally:
            self._finish_prank_cancel(scene_id)

    def _cancel_active_pranks(self) -> list[str]:
        lock, registry = self._prank_cancel_registry()
        with lock:
            entries = list(registry.items())
            registry.clear()
        cancelled = []
        for scene_id, callback in entries:
            try:
                callback()
                cancelled.append(scene_id)
            except Exception:
                log.exception("Не удалось отменить сценку XIDER %s", scene_id)
        return cancelled

    def _do_prank_stop_all(self, payload: dict) -> None:
        """Cancel only XIDER-owned scenes and restore persisted prank state."""
        log.info("Остановка активных приколов XIDER на Mac (prank_stop_all)...")
        cancelled = self._cancel_active_pranks()
        _wallpaper_ok, wallpaper_text = self._restore_saved_prank_wallpaper()
        self._publish_response("output", {
            "type": "prank_stop_all",
            "device_id": DEVICE_ID,
            "ok": False,
            "state": "partial",
            "text": (
                f"🛑 Отправлена отмена для сценок XIDER: {len(cancelled)}. {wallpaper_text} "
                "Итог частичный: старые короткие эффекты завершаются по таймеру. "
                "Сторонние процессы не трогал."
            ),
        })

    def _do_agent_update(self, payload: dict) -> None:
        """Обновление агента с GitHub по команде из бота, без запуска команды на Mac."""
        pid = os.getpid()
        if not payload.get("update", True):
            text = (f"🔄 <b>Агент XGENT {VERSION}</b>\n"
                    f"• Платформа: macOS ({platform.mac_ver()[0]})\n"
                    f"• PID: <code>{pid}</code>\n"
                    f"• Команд: <b>{len(SUPPORTED_COMMANDS)}</b>\n"
                    "• Статус: 🟢 онлайн")
            self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})
            return

        if _BUNDLED_RUNTIME:
            self._publish_response("output", {
                "type": "agent_update",
                "device_id": DEVICE_ID,
                "ok": False,
                "state": "unsupported_package",
                "operation_id": update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                "update_state": "failed",
                "text": (
                    "⚠️ Автообнова этой сборки XGENT пока не поддерживается. "
                    "Ничего не менял: нужен проверенный установщик PyInstaller-пакета."
                ),
            })
            return

        if not isinstance(payload.get("manifest_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", payload["manifest_digest"]):
            self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID,
                "ok": False, "state": "manifest_pin_required", "update_state": "failed",
                "operation_id": update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                "text": "Обновление не начато: требуется точный manifest digest."})
            return
        if not self._update_lock.acquire(blocking=False):
            self._publish_response("output", {
                "type": "agent_update", "device_id": DEVICE_ID, "ok": False,
                "state": "update_busy", "text": "⏳ Обновление уже выполняется; дождитесь результата.",
                "operation_id": update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                "update_state": "failed",
            })
            return
        restart_scheduled = False
        try:
            with self._lifecycle_lock:
                if not self._running.is_set() or self._update_cancelled.is_set():
                    self._publish_response("output", {
                        "type": "agent_update", "device_id": DEVICE_ID, "ok": False,
                        "state": "update_cancelled", "text": "Агент остановлен; установка и перезапуск отменены.",
                        "operation_id": update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                        "update_state": "failed",
                    })
                    return
            restart_scheduled = self._install_source_update(payload)
        finally:
            if not restart_scheduled:
                self._update_lock.release()

    def _install_source_update(self, payload: dict) -> bool:
        """Install under the update guard, kept until restart or cancellation."""
        cmd_id = payload.get("id", getattr(getattr(self, "_command_context", None), "cmd_id", None))
        operation_id = update_operation_id(payload, cmd_id)
        self._publish_update_progress(operation_id, "queued")
        script_dir = Path(__file__).resolve().parent
        backup_dir = CONFIG_DIR / "agent-backups" / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
        # Replace the recovery helper and launcher first. If power is lost after
        # that point, the next launch can restore the journal before loading the
        # remaining files.
        files = (
            "release_signature.py", "update_package.py", "guardian_recovery.py", "start_agent.sh",
            "command_module_loader.py", "command_signing.py", "command_replay.py", "shell_execution.py",
            "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
            "fake_update_screen.py", "location_adapter.py", "media_history.py", "media_request_control.py",
            "xider_guardian.py", "requirements.txt", "setup_mac.py",
            "stop_agent.sh", "start_guardian.sh",
        )
        installed = False
        try:
            with tempfile.TemporaryDirectory(prefix="xgent-update-") as tmp:
                archive = Path(tmp) / "XIDER-source.zip"
                manifest = Path(tmp) / "release-manifest.json"
                self._publish_update_progress(operation_id, "downloading")
                release_tag = download_verified_source_archive(
                    manifest,
                    archive,
                    release_tag=payload.get("release_tag"),
                    expected_manifest_digest=payload.get("manifest_digest"),
                )
                self._publish_update_progress(operation_id, "verifying")
                source = extract_agent_files(archive, Path(tmp) / "agent-stage", files)
                for name in files:
                    candidate = source / name
                    if candidate.suffix == ".py":
                        compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")
                with self._lifecycle_lock:
                    if self._update_cancelled.is_set() or not self._running.is_set():
                        raise RuntimeError("Установка отменена командой остановки или удаления.")
                    validate_source_update_requirements(source, script_dir)
                    recovery_slot = stage_recovery_package(
                        manifest, archive, CONFIG_DIR / "recovery", activate=False,
                    )
                    self._publish_update_progress(operation_id, "installing")
                    backup_dir = install_agent_files(
                        source,
                        script_dir,
                        backup_dir,
                        files,
                        transaction_path=CONFIG_DIR / UPDATE_STATE_NAME,
                        recovery_slot=recovery_slot,
                        operation_id=operation_id, release_tag=release_tag,
                        manifest_digest=payload.get("manifest_digest"),
                    )
                    installed = True
            text = (
                f"📦 Файлы агента из релиза {release_tag} установлены после сверки размера и SHA-256 "
                f"с release manifest. Резервная копия: {backup_dir.name}. Перезапускаю; "
                "здоровье нового процесса ещё не подтверждено."
            )
            with self._lifecycle_lock:
                if self._update_cancelled.is_set() or not self._running.is_set():
                    raise RuntimeError("Перезапуск отменён командой остановки или удаления.")
                self._publish_response("output", {
                    "type": "agent_update", "device_id": DEVICE_ID, "ok": True,
                    "state": "restarting", "release_tag": release_tag,
                    "health_check": "pending", "text": text,
                    "operation_id": operation_id, "update_state": "reconnecting",
                })
                threading.Thread(
                    target=self._restart_after_update, args=(script_dir, cmd_id, operation_id),
                    name="xgent-update-restart", daemon=True,
                ).start()
            return True
        except Exception as exc:
            log.exception("Agent self-update failed")
            outcome = "failed"
            if installed:
                try:
                    with self._lifecycle_lock:
                        rollback = rollback_unhealthy_agent(CONFIG_DIR / UPDATE_STATE_NAME, script_dir)
                        if rollback["restored"] or rollback["removed"]:
                            outcome = "rolled_back"
                except Exception:
                    log.exception("Не удалось завершить откат после ошибки подготовки перезапуска")
            receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
            if receipt and receipt.get("operation_id") == operation_id and receipt.get("update_state") in {"rolled_back", "failed"}:
                outcome = receipt["update_state"]
            self._publish_response("output", {
                "type": "agent_update", "device_id": DEVICE_ID, "ok": False,
                "state": "dependency_install_required" if isinstance(exc, DependencyUpdateRequired) else "update_failed",
                "operation_id": operation_id, "update_state": outcome,
                "text": f"❌ Не удалось завершить обновление: {exc}. Проверьте состояние файлов и каталог резервной копии.",
            })
            return False

    def _publish_update_progress(self, operation_id: str, state: str) -> None:
        self._publish_response("update_progress", {
            "type": "agent_update_progress", "device_id": DEVICE_ID,
            "operation_id": operation_id, "update_state": state,
        })

    def _restart_after_update(self, script_dir: Path, cmd_id=None, operation_id=None) -> None:
        try:
            time.sleep(1.5)
            with self._lifecycle_lock:
                if self._update_cancelled.is_set() or not self._running.is_set():
                    return
                # Exec preserves launchd ownership/PID and releases the old
                # non-inheritable singleton descriptor before the new prelude.
                # Starting a child first races its lock against this live worker.
                os.execv(sys.executable, [
                    sys.executable, str(script_dir / "xgent_mcs.py"), *sys.argv[1:],
                ])
        except Exception as exc:
            log.exception("Не удалось перезапустить обновлённого агента; возвращаю предыдущие файлы")
            rollback_detail = ""
            if operation_id:
                self._publish_update_progress(operation_id, "rolling_back")
            try:
                with self._lifecycle_lock:
                    rollback_unhealthy_agent(CONFIG_DIR / UPDATE_STATE_NAME, script_dir)
            except Exception as rollback_error:
                rollback_detail = f" Автоматический откат неполон: {rollback_error}"
                log.exception("Не удалось завершить откат после ошибки перезапуска")
            receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
            outcome = (
                receipt["update_state"] if receipt and receipt.get("operation_id") == operation_id
                and receipt.get("update_state") in {"rolled_back", "failed"} else "failed"
            )
            self._publish_response("output", {
                "type": "agent_update", "device_id": DEVICE_ID, "ok": False,
                "state": "restart_failed", "text": f"❌ Перезапуск не выполнен: {exc}.{rollback_detail}",
                **({"id": cmd_id} if cmd_id is not None else {}),
                "operation_id": operation_id,
                "update_state": outcome,
            })
        finally:
            self._update_lock.release()

    def _rollback_if_update_unhealthy(self, timeout: float = 120) -> None:
        if self._update_health.wait(timeout):
            return
        with self._lifecycle_lock:
            # Recheck after acquiring the same guard used by MQTT confirmation
            # and explicit stop. A boundary-time reconnect must win over rollback.
            if self._update_health.is_set() or self._update_cancelled.is_set() or not self._running.is_set():
                return
            try:
                result = rollback_unhealthy_agent(
                    CONFIG_DIR / UPDATE_STATE_NAME, Path(__file__).resolve().parent,
                )
                if not result["restored"] and not result["removed"]:
                    return
                log.error("Новая версия не подключилась к MQTT за %s секунд; возвращаю предыдущую.", timeout)
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
            except Exception:
                log.exception("Критическая ошибка: откат или перезапуск предыдущего агента не завершён")

    def _do_uninstall_agent(self, payload: dict) -> None:
        """Снять автозапуск, удалить локальные данные и подготовить завершение."""
        cmd_id = payload.get("id", getattr(getattr(self, "_command_context", None), "cmd_id", None))
        log.warning("Получена команда удаления автозапуска и локальных данных агента!")
        self._cancel_pending_update(rollback=False)
        try:
            set_guardian_desired_running(False)
        except Exception:
            log.exception("Could not persist Guardian stop intent before uninstall")
        layer_scripts = {
            role: _agent_install_dir() / f"start_{role}.sh"
            for role in ("siri", "safari")
        }
        layer_binary = _agent_install_dir() / "XIDER-Layers.app" / "Contents" / "MacOS" / "XIDER-Layers"

        def control_layer(role: str, action: str) -> subprocess.CompletedProcess[str]:
            if _BUNDLED_RUNTIME:
                if not layer_binary.is_file():
                    raise FileNotFoundError("Скомпилированный XIDER-Layers.app не найден.")
                return subprocess.run(
                    [str(layer_binary), "--service", role, action],
                    capture_output=True, text=True, check=False, timeout=35,
                )
            script = layer_scripts[role]
            if not script.is_file():
                raise FileNotFoundError(f"Исходный start_{role}.sh не найден.")
            command = ["/bin/bash", str(script)]
            if action == "stop":
                command.append("--stop")
            return subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=35,
            )

        stopped_layers = []
        for role in ("siri", "safari"):
            try:
                stopped = control_layer(role, "stop")
                if stopped.returncode != 0:
                    raise RuntimeError(stopped.stderr.strip() or stopped.stdout.strip() or f"{role.title()} did not stop")
                stopped_layers.append(role)
            except Exception as exc:
                log.exception("Could not stop XIDER %s before uninstall", role)
                for previous in reversed(stopped_layers):
                    try:
                        control_layer(previous, "start")
                    except Exception:
                        log.exception("Could not restore XIDER %s after uninstall was cancelled", previous)
                self._publish_response("output", {
                    "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                    "text": f"❌ {role.title()} не остановился; удаление отменено: {exc}",
                })
                return
        # Unload the visible Guardian first: removing only the worker plist
        # leaves Guardian alive and able to relaunch the worker being removed.
        try:
            la_dir = Path.home() / "Library" / "LaunchAgents"
            domain = f"gui/{os.getuid()}"
            guardian_plist = la_dir / "com.xider.guardian.plist"
            bootout = subprocess.run(
                ["launchctl", "bootout", domain, str(guardian_plist)],
                capture_output=True, text=True, check=False,
            )
            if bootout.returncode != 0:
                still_loaded = subprocess.run(
                    ["launchctl", "print", f"{domain}/com.xider.guardian"],
                    capture_output=True, text=True, check=False,
                )
                if still_loaded.returncode == 0:
                    raise RuntimeError(
                        bootout.stderr.strip() or "Guardian is still loaded in launchd"
                    )
            guardian_plist.unlink(missing_ok=True)
            # Do not bootout the worker job from inside its own callback: launchd
            # may terminate this process before it can acknowledge the removal.
            (la_dir / "com.xgent.agent.plist").unlink(missing_ok=True)
        except Exception as exc:
            log.exception("Could not fully unload XIDER Guardian before uninstall")
            try:
                set_guardian_desired_running(True)
            except Exception:
                log.exception("Could not restore Guardian intent after failed uninstall")
            for role in reversed(stopped_layers):
                try:
                    restored = control_layer(role, "start")
                    if restored.returncode != 0:
                        raise RuntimeError(restored.stderr.strip() or restored.stdout.strip() or "restart failed")
                except Exception:
                    log.exception("Could not restart XIDER %s after failed Guardian unload", role)
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Guardian остался загружен; удаление отменено, настройки сохранены: {exc}",
            })
            return

        # Удалить конфиг директорию
        try:
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR)
            if CONFIG_DIR.exists() or CONFIG_DIR.is_symlink():
                raise RuntimeError("Каталог локальных данных остался после удаления.")
        except Exception as exc:
            log.exception("Не удалось удалить локальные данные агента")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "state": "uninstall_incomplete",
                "text": f"❌ Файлы автозапуска удалены, но локальные данные удалены не полностью: {exc}. Процесс продолжает работу.",
            })
            return

        text = (
            "🛑 <b>Удаление автозапуска XGENT подготовлено</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Файлы LaunchAgents удалены, Guardian выгружен\n"
            "• Каталог локальных настроек и данных удалён\n"
            "• Подготовлены выгрузка worker LaunchAgent и завершение процесса\n"
            "• Исходники, .env и agent.log в каталоге установки могут оставаться."
        )
        self._publish_response("output", {"type": "uninstall_agent", "device_id": DEVICE_ID, "ok": True,
                                          "state": "uninstall_prepared", "text": text})

        def _bye():
            time.sleep(1.5)
            # Removing a plist does not unload the existing KeepAlive job.
            # Unload by label after the MQTT reply has had time to leave; doing
            # this earlier can terminate the callback before it acknowledges.
            try:
                unloaded = subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/com.xgent.agent"],
                    capture_output=True, text=True, check=False,
                )
                if unloaded.returncode != 0:
                    still_loaded = subprocess.run(
                        ["launchctl", "print", f"gui/{os.getuid()}/com.xgent.agent"],
                        capture_output=True, text=True, check=False,
                    )
                    if still_loaded.returncode == 0:
                        raise RuntimeError("Worker LaunchAgent остался загружен.")
            except Exception:
                log.exception("Не удалось выгрузить worker LaunchAgent после удаления")
                self._publish_response("output", {
                    "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                    "state": "uninstall_incomplete",
                    "text": "❌ Настройки удалены, но LaunchAgent остался загружен. Процесс сохранён до ручной остановки, чтобы launchd не запустил его снова.",
                    **({"id": cmd_id} if cmd_id is not None else {}),
                })
                return
            os._exit(0)
        threading.Thread(target=_bye, daemon=True).start()


def setup_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(CONFIG_DIR / "xgent.log", maxBytes=500_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[handler])


def _manage_agent_launch_agent(action: str) -> bool:
    """Manage the visible Agent LaunchAgent from the compiled app or source."""
    if action not in {"start", "stop", "status"}:
        raise ValueError("service action must be start, stop, or status")
    label = MAC_SERVICE_LABELS["agent"]
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if action == "status":
        return launch_agent_running(label)
    if action == "stop":
        set_guardian_desired_running(False)
        stop_launch_agent(label)
        return False

    install_dir = _agent_install_dir().resolve()
    env_file = install_dir / ".env"
    marker = install_dir / ".xider-signed-release"
    if env_file.is_symlink() or not env_file.is_file():
        raise FileNotFoundError("Файл .env не найден; Agent не регистрирую.")
    if _BUNDLED_RUNTIME:
        executable = Path(sys.executable).resolve()
        try:
            install_dir = mac_app_install_root(executable)
        except RuntimeError as exc:
            raise RuntimeError("Не удалось определить папку установленного XIDER.app.") from exc
        arguments = [str(executable)]
        if marker.is_file() and not executable.is_file():
            raise FileNotFoundError("XIDER.app не найден.")
    else:
        executable = Path(sys.executable).resolve()
        arguments = [str(executable), str(Path(__file__).resolve())]
    set_guardian_desired_running(True)
    set_guardian_startup_enabled(True)
    plist_path = write_mac_launch_agent(
        label=label, program_arguments=arguments, working_directory=install_dir,
        stdout_path=CONFIG_DIR / "xgent.log", keep_alive=False,
    )
    start_launch_agent(label, plist_path)
    return True

def _worker_launch_command() -> list[str]:
    if _BUNDLED_RUNTIME:
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def _agent_install_dir() -> Path:
    if _BUNDLED_RUNTIME:
        app = next((path for path in Path(sys.executable).resolve().parents if path.suffix == ".app"), None)
        if app is not None:
            return app.parent
    return Path(__file__).resolve().parent


def _layer_health_snapshot() -> dict:
    """Read only fresh, schema-checked Safari/Siri health markers for the bot."""
    result = {}
    allowed_states = {"running", "waiting", "error", "stopped"}
    for role in ("safari", "siri"):
        path = CONFIG_DIR / f"{role}-health.json"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("component") != role:
            continue
        version = payload.get("version")
        stamp = payload.get("updated_at")
        pid = payload.get("pid")
        state = payload.get("state")
        if (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or stamp <= 0 or stamp > time.time() + 5
                or type(pid) is not int or pid <= 0 or not isinstance(state, str)
                or state not in allowed_states):
            continue
        result[role] = {
            "installed": True,
            "version": version,
            "version_source": role,
            "state": state,
            "pid": pid,
            "last_seen": stamp,
        }
    return result


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--service":
        parser = argparse.ArgumentParser(description="XIDER macOS Agent LaunchAgent control")
        parser.add_argument("--service", choices=("start", "stop", "status"), required=True)
        args = parser.parse_args()
        try:
            running = _manage_agent_launch_agent(args.service)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"XIDER Agent {args.service}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if args.service == "status":
            print(f"[{'OK' if running else 'STOPPED'}] XIDER Agent LaunchAgent")
            print(f"[LOG] {CONFIG_DIR / 'xgent.log'}")
            raise SystemExit(0 if running else 1)
        print(f"[OK] XIDER Agent: {args.service}")
        raise SystemExit(0)
    if not acquire_instance_lock():
        sys.stderr.write("FATAL: XGENT-MCS уже запущен на этой машине! Второй экземпляр остановлен.\n")
        raise SystemExit(1)
    update_state = EARLY_UPDATE_STATE
    setup_logging()
    from location_adapter import initialize_runtime
    location_runtime = initialize_runtime()
    client = XgentClient()
    client.start()
    if update_state == "pending":
        threading.Thread(target=client._rollback_if_update_unhealthy, name="xgent-update-health", daemon=True).start()
    try:
        while client.is_running:
            if location_runtime is not None:
                location_runtime.pump()
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        client.request_stop()
    finally: client.stop()

if __name__ == "__main__":
    main()
