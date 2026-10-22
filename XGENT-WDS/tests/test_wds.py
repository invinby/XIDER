"""Юнит-тесты новых Windows-функций XGENT-WDS.

Проверяют чистые функции форматирования и маршрутизацию/ack без
реального подключения к MQTT или оборудованию.
"""

import collections
import datetime
import json
import os
import re
import sys
from pathlib import Path

import psutil
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import xgent_wds as wds
from release_signature import release_key_id, sign_manifest


# ---------------------------------------------------------------------------
#  Чистые функции форматирования
# ---------------------------------------------------------------------------

def test_windows_executable_metadata_matches_the_agent_release_version():
    import config

    resource = Path(wds.__file__).with_name("version_info.txt").read_text(encoding="utf-8")
    expected = ".".join([config.VERSION, "0"])
    parts = tuple(int(part) for part in (*config.VERSION.split("."), "0"))
    assert re.search(r"filevers=\(" + r"\s*,\s*".join(map(str, parts)) + r"\)", resource)
    assert f"StringStruct(u'FileVersion', u'{expected}')" in resource
    assert f"StringStruct(u'ProductVersion', u'{expected}')" in resource


def test_runtime_health_marker_is_atomic_and_contains_no_credentials(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    path = config.write_runtime_health("agent", True)
    marker = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "agent-health.json"
    assert marker["component"] == "agent"
    assert marker["connected"] is True
    assert marker["pid"] == os.getpid()
    assert marker["device_id"] == config.DEVICE_ID
    assert marker["version"] == config.VERSION
    assert marker["updated_at"] > 0
    assert not list(tmp_path.glob("*.tmp"))
    assert not {"SHARED_KEY", "MQTT_USERNAME", "MQTT_PASSWORD"}.intersection(marker)


def test_agent_mqtt_callbacks_write_connected_and_disconnected_health(client, monkeypatch):
    written = []
    monkeypatch.setattr(wds, "write_runtime_health", lambda component, connected: written.append((component, connected)))
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)

    client._client.subscribe.side_effect = [(0, 101), (0, 102)]
    client._on_connect(client._client, None, None, 0)
    assert written == []
    client._on_subscribe(client._client, None, 101, [0])
    assert written == []
    client._on_subscribe(client._client, None, 102, [0])
    client._on_disconnect(client._client, None, None, 7)

    assert written == [("agent", True), ("agent", False)]


def test_windows_sleep_wake_snapshot_is_bounded_redacted_and_registered(client, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    sleep_at = (now - datetime.timedelta(hours=2)).isoformat()
    wake_at = (now - datetime.timedelta(hours=1)).isoformat()

    class Result:
        returncode = 0
        stdout = json.dumps([
            {"Id": 42, "ProviderName": "Microsoft-Windows-Kernel-Power",
             "TimeCreated": sleep_at, "Message": "private machine detail"},
            {"Id": 107, "ProviderName": "Microsoft-Windows-Kernel-Power",
             "TimeCreated": wake_at, "Message": "private machine detail"},
        ])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "sleep_wake_history" in wds.SUPPORTED_COMMANDS
    snapshot = wds._read_sleep_wake_history_snapshot()
    client._do_sleep_wake_history({"id": "sleep-ticket"})

    assert len(calls) == 2
    assert all(call[1]["timeout"] <= 15 for call in calls)
    assert all("Get-WinEvent" in call[0][-1] for call in calls)
    assert all("Microsoft-Windows-Kernel-Power" in call[0][-1] for call in calls)
    assert all("Microsoft-Windows-Power-Troubleshooter" in call[0][-1] for call in calls)
    assert all("Select-Object -Property Id,ProviderName" in call[0][-1] for call in calls)
    assert all("Message" not in call[0][-1] for call in calls)
    assert snapshot["state"] == "supported" and len(snapshot["events"]) == 2
    assert "private machine detail" not in repr(snapshot)
    assert published[0][0] == "sleep_wake_history"
    assert published[0][1]["feature_id"] == "D01-F10"
    assert published[0][1]["request_id"] == "sleep-ticket"
    assert published[0][1]["data"]["events"] == snapshot["events"]


def test_windows_startup_duration_uses_diagnostics_event_100_only(client, monkeypatch):
    completed_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    class Result:
        returncode = 0
        stdout = json.dumps({"duration_ms": 42_500, "completed_at": completed_at.isoformat(), "message": "private"})
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "startup_duration" in wds.SUPPORTED_COMMANDS
    snapshot = wds._read_startup_duration_snapshot()
    client._do_startup_duration({"id": "boot-ticket"})

    assert calls and all(call[1]["timeout"] <= 15 for call in calls)
    script = calls[0][0][-1]
    assert "Microsoft-Windows-Diagnostics-Performance/Operational" in script
    assert "Id=100" in script and "BootTime" in script
    assert "EventData.Data" in script and ".Message" not in script
    assert snapshot["state"] == "supported" and snapshot["duration_ms"] == 42_500
    assert published[0][0] == "startup_duration"
    assert published[0][1]["feature_id"] == "D01-F11"
    assert published[0][1]["request_id"] == "boot-ticket"
    assert "private" not in repr(published[0][1])


def test_windows_pending_restart_reads_only_fixed_markers_and_publishes_correlated_state(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"checks": {
            "component_servicing": False, "windows_update": True,
            "pending_file_operations": False, "update_exe_volatile": False,
        }})
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "pending_restart" in wds.SUPPORTED_COMMANDS
    snapshot = wds._read_pending_restart_snapshot()
    client._do_pending_restart({"id": "restart-ticket"})

    assert len(calls) == 2
    assert all(call[1]["timeout"] <= 12 for call in calls)
    script = calls[0][0][-1]
    assert "Component Based Servicing" in script and "RebootRequired" in script
    assert "PendingFileRenameOperations" in script and "UpdateExeVolatile" in script
    assert "GetValue" in script and "ConvertTo-Json" in script
    assert snapshot["pending"] is True and snapshot["reasons"] == ["windows_update"]
    assert published[0][0] == "pending_restart"
    assert published[0][1]["feature_id"] == "D01-F12"
    assert published[0][1]["request_id"] == "restart-ticket"


def test_windows_os_support_identity_reads_release_and_edition_without_raw_registry(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"product_name": "Windows 11 Pro", "display_version": "26H2", "build": "26300", "edition": "Professional"})
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())

    assert "os_support" in wds.SUPPORTED_COMMANDS
    identity = wds._read_os_support_identity()

    assert identity == {"state": "supported", "family": "windows", "release": "11|26H2", "edition": "Pro"}
    assert calls and calls[0][1]["timeout"] <= 12
    assert "DisplayVersion" in calls[0][0][-1] and "CurrentBuild" in calls[0][0][-1]
    assert "ProductName" in calls[0][0][-1]
    assert "ConvertTo-Json" in calls[0][0][-1]
    assert "Windows 11 Pro" not in repr(identity)


def test_windows_hardware_inventory_projects_model_fields_without_serials(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({
            "manufacturer": "Example Inc", "model": "Board A", "cpu": "CPU Model",
            "memory_bytes": "17179869184", "graphics": ["GPU One", "GPU Two"],
            "serial_number": "MUST_NOT_ESCAPE",
        })
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    inventory = wds._read_hardware_inventory_snapshot()
    client._do_hardware_inventory({"id": "hardware-ticket"})

    assert inventory == {
        "state": "supported", "manufacturer": "Example Inc", "model": "Board A",
        "cpu": "CPU Model", "architecture": None, "memory_gib": 16,
        "graphics": ["GPU One", "GPU Two"],
    }
    assert "serial_number" not in inventory and "MUST_NOT_ESCAPE" not in json.dumps(published)
    assert "SerialNumber" not in calls[0][0][-1]
    assert published[0][0] == "hardware_inventory"
    assert published[0][1]["feature_id"] == "D01-F14"
    assert published[0][1]["request_id"] == "hardware-ticket"


def test_windows_driver_inventory_projects_only_names_providers_and_versions(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([
            {"device_name": "Display Adapter", "provider": "Vendor A", "version": "1.2.3", "driver_date": "2026-04-01"},
        ])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    rows = wds._read_driver_inventory_snapshot()
    client._do_driver_inventory({"id": "driver-ticket"})

    assert rows == {"state": "supported", "drivers": [
        {"device_name": "Display Adapter", "provider": "Vendor A", "version": "1.2.3", "driver_date": "2026-04-01"},
    ]}
    script = calls[0][0][-1]
    assert "Win32_PnPSignedDriver" in script and "ConvertTo-Json" in script
    assert all(term not in script for term in ("HardwareID", "DeviceID", "SerialNumber", "InfName"))
    assert calls[0][1]["timeout"] <= 20
    assert published[0][0] == "driver_inventory"
    assert published[0][1]["feature_id"] == "D01-F15"
    assert published[0][1]["request_id"] == "driver-ticket"


def test_windows_installed_app_inventory_projects_only_name_publisher_and_version(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([{"name": "Example App", "publisher": "Vendor", "version": "2.1"}])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    snapshot = wds._read_installed_app_inventory_snapshot()
    assert snapshot == {"state": "supported", "apps": [{"name": "Example App", "publisher": "Vendor", "version": "2.1"}]}
    script = calls[0][0][-1]
    assert "DisplayName" in script and "DisplayVersion" in script and "Publisher" in script
    assert all(term not in script for term in ("InstallLocation", "UninstallString", "InstallSource"))
    assert calls[0][1]["timeout"] <= 20
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_installed_app_inventory({"id": "apps-ticket"})
    assert published[0][0] == "installed_app_inventory"
    assert published[0][1]["feature_id"] == "D01-F16"
    assert published[0][1]["request_id"] == "apps-ticket"


def test_windows_startup_inventory_exports_entry_labels_and_scopes_not_commands(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([{"name": "Updater", "scope": "user_run"}])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    snapshot = wds._read_startup_inventory_snapshot()
    assert snapshot == {"state": "supported", "entries": [{"name": "Updater", "scope": "user_run"}]}
    script = calls[0][0][-1]
    assert all(scope in script for scope in ("user_run", "machine_run", "user_folder", "machine_folder"))
    assert "UninstallString" not in script
    assert calls[0][1]["timeout"] <= 20
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_startup_inventory({"id": "startup-ticket"})
    assert published[0][0] == "startup_inventory"
    assert published[0][1]["feature_id"] == "D01-F17"
    assert published[0][1]["request_id"] == "startup-ticket"


def test_windows_service_inventory_projects_names_states_and_start_types_only(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([{"name": "ExampleSvc", "state": "Running", "start_type": "Auto"}])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    snapshot = wds._read_service_inventory_snapshot()
    assert snapshot == {"state": "supported", "services": [{"name": "ExampleSvc", "state": "Running", "start_type": "Auto"}]}
    script = calls[0][0][-1]
    assert "Win32_Service" in script and all(field in script for field in ("Name", "State", "StartMode"))
    assert all(field not in script for field in ("PathName", "StartName", "ProcessId"))
    assert calls[0][1]["timeout"] <= 20
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_service_inventory({"id": "service-ticket"})
    assert published[0][0] == "service_inventory"
    assert published[0][1]["feature_id"] == "D01-F18"
    assert published[0][1]["request_id"] == "service-ticket"


def test_windows_crash_summary_aggregates_only_provider_event_level_and_count(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([{"provider": "Application Error", "event_id": 1000, "level": 2, "count": 3}])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    summary = wds._read_crash_summary_snapshot()
    assert summary == {"state": "supported", "events": [{"provider": "Application Error", "event_id": 1000, "level": 2, "count": 3}]}
    script = calls[0][0][-1]
    assert "Get-WinEvent" in script and "Message" not in script
    assert calls[0][1]["timeout"] <= 20
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_crash_summary({"id": "crash-ticket"})
    assert published[0][0] == "crash_summary"
    assert published[0][1]["feature_id"] == "D01-F19"
    assert published[0][1]["request_id"] == "crash-ticket"


def test_windows_smart_trend_snapshot_projects_only_supported_numeric_counters(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([
            {"FriendlyName": "Model A", "MediaType": "SSD", "Temperature": 38,
             "Wear": 7, "PowerOnHours": 1200, "ReadErrorsTotal": 3,
             "SerialNumber": "MUST_NOT_LEAVE_DEVICE"},
            {"FriendlyName": "Model B", "MediaType": "HDD", "Temperature": None,
             "Wear": "bad", "PowerOnHours": -1, "SerialNumber": "ALSO_PRIVATE"},
        ])
        stderr = ""

    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda *a, **k: Result())

    snapshot = wds._read_smart_trend_snapshot()

    assert snapshot["state"] == "supported"
    assert snapshot["source"] == "windows_storage_reliability_counters"
    assert snapshot["disks"] == [
        {"label": "Model A [SSD]", "counters": {
            "temperature_c": 38.0, "wear_percent": 7.0,
            "power_on_hours": 1200.0, "read_errors_total": 3.0,
        }},
    ]
    assert "MUST_NOT_LEAVE_DEVICE" not in json.dumps(snapshot)
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_smart_trend({"id": "smart-ticket"})
    topic, payload = published[-1]
    assert topic == "smart_trend"
    assert payload["feature_id"] == "D01-F20"
    assert payload["request_id"] == "smart-ticket"
    assert payload["data"] == {"disks": snapshot["disks"]}


def test_windows_volume_topology_hashes_volume_ids_and_keeps_mount_aliases(client, monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps([
            {"UniqueId": "\\\\?\\Volume{private-guid-a}\\", "AccessPaths": ["C:\\\\", "C:\\\\Mounts\\\\Data", "\\\\?\\Volume{private-guid-a}\\"]},
            {"UniqueId": "\\\\?\\Volume{private-guid-b}\\", "AccessPaths": ["D:\\\\"]},
        ])
        stderr = ""

    calls = []
    monkeypatch.setattr(wds.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(wds.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    snapshot = wds._read_volume_topology_snapshot()
    client._do_volume_topology({"id": "topology-ticket"})

    assert snapshot["state"] == "supported"
    assert len(snapshot["volumes"]) == 2
    assert snapshot["volumes"][0]["mount_paths"] == ["C:\\\\", "C:\\\\Mounts\\\\Data"]
    assert snapshot["volumes"][0]["volume_key"].startswith("vol-")
    assert "private-guid" not in json.dumps(snapshot)
    assert "Get-Partition" in calls[0][0][-1]
    assert "UniqueId" in calls[0][0][-1]
    assert "FileSystemLabel" not in calls[0][0][-1]
    topic, result = published[0]
    assert topic == "volume_topology"
    assert result["feature_id"] == "D01-F21"
    assert result["request_id"] == "topology-ticket"


def test_windows_clock_drift_compares_wall_and_monotonic_elapsed_without_setting_time(monkeypatch):
    wall = iter([10_000_000_000, 12_123_000_000])
    monotonic = iter([50_000, 2_000_050_000])
    monkeypatch.setattr(wds.time, "time_ns", lambda: next(wall))
    monkeypatch.setattr(wds.time, "perf_counter_ns", lambda: next(monotonic))
    monkeypatch.setattr(wds.time, "sleep", lambda _seconds: None)

    result = wds._read_clock_drift_snapshot()

    assert result["state"] == "supported"
    assert result["source"] == "wall_vs_monotonic_elapsed"
    assert result["drift_ms"] == 123
    assert result["sample_ms"] == 2000
    assert "clock_drift" in wds.SUPPORTED_COMMANDS


def test_windows_clock_drift_command_envelope_is_correlated(client, monkeypatch):
    monkeypatch.setattr(wds, "_read_clock_drift_snapshot", lambda: {
        "state": "supported", "source": "wall_vs_monotonic_elapsed", "drift_ms": 123, "sample_ms": 2000,
    })
    monkeypatch.setattr(wds, "DEVICE_ID", "win-1")
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    client._do_clock_drift({"id": "clock-ticket"})

    topic, payload = published[0]
    assert topic == "clock_drift"
    assert payload["feature_id"] == "D01-F22"
    assert payload["schema"] == "xider.clock-drift.v1"
    assert payload["device_id"] == "win-1"
    assert payload["request_id"] == "clock-ticket"
    assert payload["checked_at"].endswith("+00:00")
    assert payload["data"] == {"drift_ms": 123, "sample_ms": 2000}


def test_humanize_seconds():
    assert wds._humanize_seconds(0) == "0мин"
    assert wds._humanize_seconds(60) == "1мин"
    assert wds._humanize_seconds(3600) == "1ч 0мин"
    assert wds._humanize_seconds(19920) == "5ч 32мин"
    assert wds._humanize_seconds(None) is None
    assert wds._humanize_seconds(-1) is None
    assert wds._humanize_seconds("bad") is None


def test_windows_agent_uses_shared_ed25519_manifest_verifier():
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    manifest = sign_manifest({"schema": 1, "release": "v4.5.0", "assets": []}, private_pem)
    key_id = release_key_id(public_bytes)

    assert wds.verify_manifest_signature(manifest, {key_id: public_bytes}) == key_id


def test_format_battery_none():
    assert wds._format_battery(None) == {"available": False}


def test_format_battery_present():
    Battery = collections.namedtuple("sbattery", ["percent", "secsleft", "power_plugged"])
    b = Battery(percent=87.4, secsleft=12345, power_plugged=False)
    out = wds._format_battery(b)
    assert out["available"] is True
    assert out["percent"] == 87.4
    assert out["power_plugged"] is False
    assert out["secsleft"] == 12345
    assert out["time_left"] == "3ч 25мин"
    assert out["state"] == "on_battery"


def test_format_battery_unlimited():
    Battery = collections.namedtuple("sbattery", ["percent", "secsleft", "power_plugged"])
    b = Battery(percent=100.0, secsleft=psutil.POWER_TIME_UNLIMITED, power_plugged=True)
    out = wds._format_battery(b)
    assert out["secsleft"] is None
    assert out["time_left"] is None
    assert out["state"] == "charging"


class _Addr:
    def __init__(self, family, address):
        self.family = family
        self.address = address


class _Stats:
    def __init__(self, isup, speed):
        self.isup = isup
        self.speed = speed


class _Io:
    def __init__(self, sent, recv):
        self.bytes_sent = sent
        self.bytes_recv = recv


def test_format_network():
    import socket

    addrs = {
        "Ethernet": [_Addr(socket.AF_INET, "10.0.0.5"), _Addr(psutil.AF_LINK, "AA-BB-CC")],
        "Loopback": [_Addr(socket.AF_INET6, "::1%lo")],
    }
    stats = {"Ethernet": _Stats(isup=True, speed=1000), "Loopback": _Stats(isup=True, speed=0)}
    io = {"Ethernet": _Io(100, 200), "Loopback": _Io(5, 5)}
    out = wds._format_network(addrs, stats, io)
    eth = next(i for i in out["interfaces"] if i["name"] == "Ethernet")
    assert eth["ipv4"] == "10.0.0.5"
    assert eth["mac"] == "AA-BB-CC"
    assert eth["up"] is True
    assert eth["speed_mbps"] == 1000
    lo = next(i for i in out["interfaces"] if i["name"] == "Loopback")
    assert lo["ipv6"] == "::1"
    assert out["totals"]["bytes_sent"] == 105
    assert out["totals"]["bytes_recv"] == 205


class _Service:
    def __init__(self, name, display_name, status, start_type):
        self._name = name
        self._display = display_name
        self._status = status
        self._start = start_type

    def name(self):
        return self._name

    def display_name(self):
        return self._display

    def status(self):
        return self._status

    def start_type(self):
        return self._start


def test_format_services():
    svcs = [
        _Service("WinDefend", "Windows Defender", "running", "auto"),
        _Service("Spooler", "Print Spooler", "running", "auto"),
        _Service("BrokenSvc", "Broken", "stopped", "auto"),
        _Service("ManualSvc", "Manual", "stopped", "manual"),
    ]
    out = wds._format_services(svcs)
    assert out["total"] == 4
    assert out["running"] == 2
    assert out["stopped"] == 2
    assert out["failed_auto_start"] == [
        {"name": "BrokenSvc", "display_name": "Broken", "status": "stopped"}
    ]


class _Proc:
    def __init__(self, info):
        self.info = info


def test_format_processes():
    procs = [
        _Proc({"pid": 1, "name": "svchost.exe", "memory_percent": 12.3}),
        _Proc({"pid": 2, "name": "chrome.exe", "memory_percent": 30.1}),
        _Proc({"pid": 3, "name": "explorer.exe", "memory_percent": 5.0}),
    ]
    out = wds._format_processes(procs, top_n=2)
    assert out["lines"][0] == "chrome.exe: 30.1%"
    assert out["lines"][1] == "svchost.exe: 12.3%"
    assert len(out["top"]) == 2
    assert out["top"][0]["name"] == "chrome.exe"


# ---------------------------------------------------------------------------
#  Маршрутизация и acknowledgement
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    c = wds.XgentClient()
    mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock.publish.return_value.rc = 0
    monkeypatch.setattr(c, "_client", mock)
    return c


def test_publish_response_journals_only_media_metadata_locally(client, monkeypatch, tmp_path):
    import media_history

    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds, "sign_message", lambda value: value)
    monkeypatch.setattr(wds, "ENCRYPT_PAYLOAD", False)
    client._client.publish.return_value.is_published.return_value = True

    assert client._publish_response("mic", {
        "type": "mic", "device_id": "device-secret-id", "id": "request-secret-id",
        "audio": "private-audio-content",
    }) is True

    path = tmp_path / "media-history.json"
    assert media_history.read_events(path)[0]["action"] == "mic"
    saved = path.read_text(encoding="utf-8")
    assert "private-audio-content" not in saved
    assert "device-secret-id" not in saved and "request-secret-id" not in saved


def test_windows_remote_update_does_not_claim_success_without_signed_release(client, monkeypatch, tmp_path):
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        wds,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("В релизе нет единственной пары source ZIP и release manifest.")
        ),
    )
    client._do_agent_update({"update": True, "manifest_digest": "a" * 64})

    responses = [
        payload
        for topic, payload in _publish_calls(client)
        if topic.endswith("/output") and payload.get("type") == "agent_update"
    ]
    assert len(responses) == 1
    assert responses[0]["ok"] is False
    assert responses[0]["state"] == "failed"
    assert "Обновление не подтверждено" in responses[0]["text"]
    assert not (tmp_path / "agent-backups").exists()


def test_windows_status_advertises_source_update_mode(client, monkeypatch):
    import json

    monkeypatch.setattr(wds, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    assert wds.trusted_release_keys_ready() is True
    monkeypatch.setattr(wds, "trusted_release_keys_ready", lambda: False)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = wds.verify_message(envelope)
    assert payload["update_mode"] == "source"
    assert payload["release_update_ready"] is False


def test_windows_status_advertises_release_key_readiness(client, monkeypatch):
    monkeypatch.setattr(wds, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    monkeypatch.setattr(wds, "trusted_release_keys_ready", lambda: True)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = wds.verify_message(envelope)
    assert payload["release_update_ready"] is True


def test_windows_frozen_status_advertises_non_updatable_package(client, monkeypatch):
    import json

    monkeypatch.setattr(wds, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = wds.verify_message(envelope)
    assert payload["update_mode"] == "frozen"


def test_dispatch_unknown_command(client):
    client._dispatch({"type": "no_such_command", "id": "abc"})
    # Должен уйти ack с ошибкой unknown_command.
    topics = [call.args[0] for call in client._client.publish.call_args_list]
    assert any(t.endswith("/ack") for t in topics)
    payloads = [
        __import__("json").loads(call.args[1])["payload"]
        for call in client._client.publish.call_args_list
    ]
    ack = next(p for p in payloads if p.get("type") == "ack")
    assert ack["status"] == "error"
    assert ack["detail"] == "unknown_command"
    assert ack["id"] == "abc"


def test_dispatch_known_command_acks_received_and_ok(client, monkeypatch):
    # open_url не требует оборудования, кроме webbrowser.open_new_tab — замокаем.
    monkeypatch.setattr(wds.webbrowser, "open_new_tab", lambda u: True)
    monkeypatch.setattr(wds, "ctypes_windll_user32_message_box", lambda t: None)

    client._dispatch({"type": "open_url", "id": "x1", "url": "https://example.com"})
    import json
    import time

    def _acks():
        return [
            json.loads(call.args[1])["payload"]
            for call in client._client.publish.call_args_list
            if call.args[0].endswith("/ack")
        ]

    # Первый ack — received (сразу).
    acks = _acks()
    assert acks and acks[0]["status"] == "received"
    # Итоговый ok придёт из потока — дождёмся.
    for _ in range(50):
        if any(p["status"] == "ok" for p in _acks()):
            break
        time.sleep(0.05)
    assert any(p["status"] == "ok" for p in _acks())


def test_open_url_opens_the_requested_number_of_web_tabs(client, monkeypatch):
    opened = []
    monkeypatch.setattr(wds.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    monkeypatch.setattr(client, "_notify_text", lambda _text: None)

    client._do_open_url({"url": "https://example.com/path", "count": 3})

    assert opened == ["https://example.com/path"] * 3


@pytest.mark.parametrize("payload", [
    {"url": "javascript:alert(1)"},
    {"url": "file:///C:/Windows/win.ini"},
    {"url": "https://example.com", "count": 0},
    {"url": "https://example.com", "count": 6},
])
def test_open_url_rejects_non_web_schemes_and_out_of_range_counts(client, monkeypatch, payload):
    monkeypatch.setattr(wds.webbrowser, "open_new_tab", lambda _url: pytest.fail("must not open"))
    with pytest.raises(ValueError):
        client._do_open_url(payload)


def test_handler_map_covers_supported_commands(client):
    for name in wds.SUPPORTED_COMMANDS:
        assert name in client._handlers
        assert callable(client._handlers[name])


# ---------------------------------------------------------------------------
#  DEEPSEEK: чистые функции глубоких команд (экран/сеть/реестр/Night Light)
# ---------------------------------------------------------------------------

def test_clamp_brightness():
    assert wds._clamp("50", 0, 100, 30) == 50
    assert wds._clamp("150", 0, 100, 30) == 100
    assert wds._clamp("-5", 0, 100, 30) == 0
    assert wds._clamp("abc", 0, 100, 30) == 30
    assert wds._clamp(None, 0, 100, 30) == 30


def test_orientation_value():
    assert wds._orientation_value(0) == 0
    assert wds._orientation_value(90) == 1
    assert wds._orientation_value(180) == 2
    assert wds._orientation_value(270) == 3
    assert wds._orientation_value(450) == 1  # 450 % 360 = 90
    assert wds._orientation_value(45) is None
    assert wds._orientation_value("abc") is None


def test_parse_wifi_profile_names():
    text = (
        "Profiles on interface Wi-Fi:\n\n"
        "Group policy profiles (read only)\n"
        "---------------------------------\n"
        "    <None>\n\n"
        "User profiles\n"
        "------------\n"
        "    All User Profile     : HomeNet\n"
        "    All User Profile     : Office_5G\n"
    )
    assert wds._parse_wifi_profile_names(text) == ["HomeNet", "Office_5G"]


def test_parse_wifi_profile_names_russian():
    text = "    Профиль всех пользователей : Дом\n    Профиль всех пользователей : Работа\n"
    assert wds._parse_wifi_profile_names(text) == ["Дом", "Работа"]


def test_parse_key_content():
    text = "    Key Content            : mysecret123\n    Cost                    : 1\n"
    assert wds._parse_key_content(text) == "mysecret123"


def test_night_light_helpers():
    # Проверяем переключение blob'а в обе стороны без реального реестра.
    enabled = bytearray(43)
    enabled[18] = 0x15
    assert wds._night_light_enabled(bytes(enabled)) is True

    off = wds._night_light_toggle_bytes(bytes(enabled))
    assert wds._night_light_enabled(off) is False
    assert len(off) in (41, 43)

    back = wds._night_light_toggle_bytes(off)
    assert wds._night_light_enabled(back) is True


# ---------------------------------------------------------------------------
#  Безопасность: тесты на критические сценарии (функция warn удалена —
#  fail-closed теперь делает сама config.py, см. тесты ниже)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
#  Чувствительные команды: routing + безопасный shell (без реального микрофона/сети)
# ---------------------------------------------------------------------------

def _publish_calls(client):
    import json
    return [
        (call.args[0], json.loads(call.args[1])["payload"])
        for call in client._client.publish.call_args_list
    ]


def test_remove_current_worker_task_does_not_end_acknowledgement_process(monkeypatch):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "deleted", "stderr": ""})()

    monkeypatch.setattr(wds.subprocess, "run", fake_run)

    ok, detail = wds._remove_scheduled_task(
        wds.WINDOWS_TASK_NAME, stop_running=False,
    )

    assert ok is True
    assert detail == "deleted"
    assert calls == [[
        "schtasks.exe", "/Delete", "/TN", wds.WINDOWS_TASK_NAME, "/F",
    ]]


def test_windows_uninstall_unregisters_worker_without_killing_ack(client, monkeypatch, tmp_path):
    from types import SimpleNamespace

    config_dir = tmp_path / ".xgent"
    config_dir.mkdir()
    (config_dir / "state.json").write_text("state", encoding="utf-8")
    monkeypatch.setattr(wds, "CONFIG_DIR", config_dir)
    desired = []
    monkeypatch.setattr(wds, "set_guardian_desired_running", desired.append)
    removed = []
    monkeypatch.setattr(
        wds, "_remove_scheduled_task",
        lambda task, *, stop_running: (removed.append((task, stop_running)) or (True, "")),
    )

    class NoStartThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(wds.threading, "Thread", NoStartThread)
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=0, KEY_SET_VALUE=0,
        OpenKey=lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    ))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    client._do_uninstall_agent({})

    assert desired == [False]
    assert removed == [
        (wds.WINDOWS_TASK_NAME, False),
        (wds.WINDOWS_GUARDIAN_TASK_NAME, True),
    ]
    assert not config_dir.exists()
    responses = [
        payload for topic, payload in _publish_calls(client)
        if topic.endswith("/output") and payload.get("type") == "uninstall_agent"
    ]
    assert len(responses) == 1 and responses[0]["ok"] is True


def test_windows_uninstall_preserves_state_when_worker_task_cannot_be_removed(
    client, monkeypatch, tmp_path
):
    config_dir = tmp_path / ".xgent"
    config_dir.mkdir()
    marker = config_dir / "state.json"
    marker.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(wds, "CONFIG_DIR", config_dir)
    desired = []
    monkeypatch.setattr(wds, "set_guardian_desired_running", desired.append)
    calls = []

    def remove_task(task, *, stop_running):
        calls.append((task, stop_running))
        return False, "task scheduler denied removal"

    monkeypatch.setattr(wds, "_remove_scheduled_task", remove_task)
    client._do_uninstall_agent({})

    assert calls == [(wds.WINDOWS_TASK_NAME, False)]
    assert desired == [False, True]
    assert marker.read_text(encoding="utf-8") == "preserve"
    responses = [
        payload for topic, payload in _publish_calls(client)
        if topic.endswith("/output") and payload.get("type") == "uninstall_agent"
    ]
    assert len(responses) == 1 and responses[0]["ok"] is False


def _wait_for_topic(client, suffix, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        topics = _publish_calls(client)
        if any(topic.endswith(suffix) for topic, _ in topics):
            return topics
        time.sleep(0.05)
    return _publish_calls(client)


def test_clipboard_routing(client, monkeypatch):
    import pyperclip
    monkeypatch.setattr(pyperclip, "paste", lambda: "hello world")
    client._dispatch({"type": "clipboard", "id": "c1"})
    topics = _wait_for_topic(client, "/clipboard")
    assert any(t.endswith("/clipboard") for t, _ in topics)
    payload = next(p for t, p in topics if t.endswith("/clipboard"))
    assert payload["text"] == "hello world"
    # id живёт в ack, а не в ответе клиента
    ack = next(p for t, p in topics if t.endswith("/ack") and p.get("id") == "c1")
    assert ack["action"] == "clipboard"


def test_shell_routing_uses_capture(client, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        kwargs["stdout"].write(b"ok\n")
        r = type("R", (), {})()
        r.stdout = "ok\n"
        r.stderr = ""
        r.returncode = 0
        return r

    monkeypatch.setattr(wds._load_command_approval_component("shell_execution"), "_run_process", fake_run)
    # Exercise the subprocess/result path independently; signature and replay
    # rejection are covered by test_shell_approval_gate.py.
    monkeypatch.setattr(wds, "_reserve_shell_approval", lambda _client, _payload: True)
    client._dispatch({"type": "shell", "command": "whoami", "id": "s1"})
    topics = _wait_for_topic(client, "/shell")
    assert captured["cmd"] == "whoami"
    assert captured["kwargs"]["shell"] is True
    assert captured["kwargs"]["timeout"] >= 1
    topics = _publish_calls(client)
    payload = next(p for t, p in topics if t.endswith("/shell"))
    assert "ok" in payload["output"]
    assert payload["returncode"] == 0


def test_shell_timeout_reports_error(client, monkeypatch):
    def fake_run(*a, **kw):
        raise wds.subprocess.TimeoutExpired(cmd="x", timeout=20)
    monkeypatch.setattr(wds._load_command_approval_component("shell_execution"), "_run_process", fake_run)
    monkeypatch.setattr(wds, "_reserve_shell_approval", lambda _client, _payload: True)
    client._dispatch({"type": "shell", "command": "sleep 999"})
    topics = _wait_for_topic(client, "/shell")
    payload = next(p for t, p in topics if t.endswith("/shell"))
    assert "TIMEOUT" in payload["output"]
    assert payload["returncode"] == -1


def test_open_app_uses_startfile_no_shell(client, monkeypatch):
    calls = {"startfile": None}

    def fake_startfile(p):
        calls["startfile"] = p

    monkeypatch.setattr(wds.os, "startfile", fake_startfile, raising=False)
    client._dispatch({"type": "open_app", "app": "calc"})
    assert calls["startfile"] == "calc"


def test_capabilities_does_not_leak_secret(client):
    client._dispatch({"type": "capabilities", "id": "cap1"})
    topics = _wait_for_topic(client, "/capabilities")
    payload = next(p for t, p in topics if t.endswith("/capabilities"))
    assert "SHARED_KEY" not in payload
    assert "shared_key" not in payload
    assert "clipboard" in payload["commands"]
    assert "mic" in payload["commands"]
    assert "shell" in payload["commands"]
    assert "open_app" in payload["commands"]
    assert "cpu_history" in payload["commands"]
    assert payload["feature_status"]["geolocation"] == "approximate"
    assert payload["feature_status"]["screenshot"] in {
        "device_unverified", "dependency_missing",
    }


def test_feature_status_does_not_claim_camera_permission(monkeypatch):
    monkeypatch.setattr(wds, "_detect_features", lambda: {
        "screenshot": True, "clipboard": True, "battery": True,
    })
    monkeypatch.setattr(wds, "_module_available", lambda _name: True)

    status = wds._detect_feature_status()

    assert status["webcam"] == "permission_unverified"
    assert status["microphone"] == "permission_unverified"
    assert status["screenshot"] == "device_unverified"
    assert status["geolocation"] == "approximate"
    assert status["battery"] == "supported"
    assert status["external_ip_watch"] == "supported"


def test_x_lock_rejects_second_windows_agent(tmp_path, monkeypatch):
    import msvcrt

    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    assert wds.acquire_instance_lock() is True
    try:
        assert wds.acquire_instance_lock() is False
    finally:
        handle = wds._instance_lock_file
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
        wds._instance_lock_file = None


def test_geoip_uses_https_fallback_and_reports_approximation(client, monkeypatch):
    import io
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if "ipapi.co" in request.full_url:
            raise OSError("provider unavailable")
        return Response(b'{"ip":"203.0.113.10","city":"Test","country":"XX","loc":"1.0,2.0"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client._do_geo_location({"ip_fallback": True})
    results = [payload for topic, payload in _publish_calls(client) if topic.endswith("/geo_location")]
    assert results and results[-1]["ok"] is True
    assert "не GPS" in results[-1]["text"]
    assert calls == ["https://ipapi.co/json/", "https://ipinfo.io/json"]
    assert len([payload for topic, payload in _publish_calls(client) if topic.endswith("/output") and payload.get("type") == "geo_location"]) == 0


def test_geoip_lookup_obeys_total_deadline_and_publishes_once(client, monkeypatch):
    import urllib.request

    clock = [100.0]
    timeouts = []
    publications = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        clock[0] += timeout
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(wds.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: publications.append((topic, payload)))

    client._do_geo_location({"id": "geo-456", "ip_fallback": True})

    assert timeouts == [3.0, 3.0]
    assert len(publications) == 1
    assert publications[0][0] == "geo_location"
    assert publications[0][1]["type"] == "geo_location"
    assert publications[0][1]["ok"] is False


def test_config_fails_closed_without_key(tmp_path):
    """Без SHARED_KEY в env и в .env config.py должен вызвать SystemExit(2)."""
    import os as _os
    import subprocess as sp
    env = {k: v for k, v in _os.environ.items() if k not in {"SHARED_KEY"}}
    # Не передаём SHARED_KEY, но load_dotenv прочитает существующий .env,
    # если он есть. Поэтому запускаем в копии каталога без .env.
    import shutil
    fake_root = tmp_path / "wds_fake"
    fake_root.mkdir()
    shutil.copy(
        Path(wds.__file__).with_name("config.py"),
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path(r'C:\\TEMP\\cfg.py', run_name='__main__')"
            .replace(r"C:\TEMP\cfg.py", str(fake_root / "config.py")),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "FATAL" in (result.stderr + result.stdout)


def test_config_fails_closed_with_default_key(tmp_path):
    """Дефолтный (публичный) SHARED_KEY должен тоже приводить к SystemExit(2)."""
    import os as _os
    import subprocess as sp
    import shutil
    env = {k: v for k, v in _os.environ.items() if k != "SHARED_KEY"}
    env["SHARED_KEY"] = wds.DEFAULT_SHARED_KEY
    fake_root = tmp_path / "wds_fake2"
    fake_root.mkdir()
    shutil.copy(
        Path(wds.__file__).with_name("config.py"),
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path(r'X', run_name='__main__')"
            .replace("r'X'", "r'" + str(fake_root / "config.py") + "'"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "FATAL" in (result.stderr + result.stdout)


def test_config_ok_with_strong_key(tmp_path):
    """Нормальный SHARED_KEY — config.py загружается без ошибок."""
    import os as _os
    import subprocess as sp
    import shutil
    env = {k: v for k, v in _os.environ.items() if k != "SHARED_KEY"}
    env["SHARED_KEY"] = "a-strong-secret-0123456789abcdef"
    fake_root = tmp_path / "wds_fake3"
    fake_root.mkdir()
    shutil.copy(
        Path(wds.__file__).with_name("config.py"),
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path(r'X', run_name='__main__')"
            .replace("r'X'", "r'" + str(fake_root / "config.py") + "'"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_external_ip_response_is_correlated_and_rejects_non_public_ip(client, monkeypatch):
    import io
    import urllib.request

    publications = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"8.8.8.8"))
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: publications.append((topic, dict(payload))))
    client._do_ext_ip({"id": "ip-check-1"})

    payload = publications[0][1]
    assert publications[0][0] == "ext_ip"
    assert payload["schema"] == "xider.external-ip.v1"
    assert payload["feature_id"] == "D04-F30"
    assert payload["request_id"] == "ip-check-1"
    assert payload["state"] == "available" and payload["ip"] == "8.8.8.8"
    assert payload["source"] == "ipify" and payload["checked_at"]

    publications.clear()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"192.168.1.2"))
    client._do_ext_ip({"id": "ip-check-2"})
    assert publications[0][1]["state"] == "unknown"
    assert publications[0][1]["ip"] == ""
