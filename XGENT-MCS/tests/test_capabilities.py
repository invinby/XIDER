"""Capability reporting tests; no camera, microphone, or screen is accessed."""

import xgent_mcs as mcs
import shell_execution
import datetime
import json
import plistlib
import threading
from types import SimpleNamespace


def test_feature_report_never_claims_live_permissions(monkeypatch):
    monkeypatch.setattr(mcs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(mcs, "_module_available", lambda _name: True)
    monkeypatch.setattr(mcs.psutil, "sensors_battery", lambda: None)

    status = mcs._detect_feature_status()

    assert status["screenshot"] == "permission_unverified"
    assert status["webcam"] == "permission_unverified"
    assert status["microphone"] == "permission_unverified"
    assert status["battery"] == "unavailable"
    assert status["geolocation"] == "approximate"
    assert status["shell"] == "supported"
    assert status["external_ip_watch"] == "supported"


def test_feature_report_marks_missing_macos_tools_and_dependencies(monkeypatch):
    monkeypatch.setattr(mcs.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mcs, "_module_available", lambda _name: False)
    monkeypatch.setattr(mcs.psutil, "sensors_battery", lambda: None)

    status = mcs._detect_feature_status()

    assert status["screenshot"] == "unavailable"
    assert status["webcam"] == "dependency_missing"
    assert status["microphone"] == "dependency_missing"
    assert status["clipboard"] == "unavailable"


def test_capabilities_response_includes_status_and_legacy_fields(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "_detect_feature_status", lambda: {
        "shell": "supported",
        "open_app": "supported",
        "battery": "unavailable",
        "microphone": "permission_unverified",
        "clipboard": "device_unverified",
        "screenshot": "permission_unverified",
        "webcam": "dependency_missing",
        "geolocation": "approximate",
    })

    client._do_capabilities({})

    topic, payload = sent[0]
    assert topic == "capabilities"
    assert payload["features"]["mic"] is True
    assert payload["features"]["clipboard"] is True
    assert payload["features"]["battery"] is False
    assert payload["feature_status"]["screenshot"] == "permission_unverified"
    assert "cpu_history" in payload["commands"]
    assert "memory_pressure_history" in payload["commands"]
    assert "SHARED_KEY" not in payload
    assert payload["shell_runner"]["schema"] == 1
    assert payload["shell_runner"]["protocol_state"] in {"ready", "unavailable"}
    assert payload["shell_runner"]["executor_state"] in {"available", "unavailable"}


def test_shell_runner_probe_checks_filesystem_only_and_never_starts_a_process(monkeypatch, tmp_path):
    runner = tmp_path / "runner"
    runner.write_text("fixed shell executable placeholder", encoding="utf-8")
    monkeypatch.setattr(shell_execution.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not launch")))
    result = shell_execution.inspect_shell_runner(
        str(runner), is_file=lambda path: path == str(runner),
        can_execute=lambda path: path == str(runner),
    )
    assert result == {"runner_state": "available", "permission_state": "available"}


def test_shell_runner_probe_reports_missing_runner_without_claiming_permissions():
    result = shell_execution.inspect_shell_runner(
        "/not/a/runner", is_file=lambda _path: False,
        can_execute=lambda _path: (_ for _ in ()).throw(AssertionError("must not check absent runner")),
    )
    assert result == {"runner_state": "unavailable", "permission_state": "unknown"}


def test_snapshot_inventory_is_complete_and_never_assumes_permission(monkeypatch):
    monkeypatch.setattr(mcs, "_module_available", lambda _name: True)
    monkeypatch.setattr(mcs, "_command_available", lambda *_args: True)
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    client._do_capabilities({})
    topic, snapshot = sent[0]
    assert topic == "capabilities"
    assert snapshot.get("schema") == 1
    assert snapshot["platform"] == mcs.PLATFORM
    assert datetime.datetime.fromisoformat(snapshot["checked_at"]).utcoffset() == datetime.timedelta(0)
    items = {item["id"]: item for item in snapshot["items"]}
    assert set(items) == {"screen_recording", "camera", "microphone", "location", "accessibility", "file_access"}
    assert all(item["state"] in {"needs_live_test", "unavailable", "unknown"} for item in items.values())
    assert all(item["detail"] and "settings_uri" in item for item in items.values())
    assert "TCC" in items["accessibility"]["detail"]
    assert "source=ip_estimate" in items["location"]["detail"]
    assert "uncertainty=" in items["location"]["detail"]


def test_snapshot_missing_dependencies_are_unavailable(monkeypatch):
    monkeypatch.setattr(mcs, "_module_available", lambda _name: False)
    monkeypatch.setattr(mcs, "_command_available", lambda *_args: False)
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    client._do_capabilities({})
    assert sent[0].get("schema") == 1
    items = {item["id"]: item for item in sent[0]["items"]}
    assert items["camera"]["state"] == "unavailable"
    assert items["microphone"]["state"] == "unavailable"
    assert items["screen_recording"]["state"] == "unavailable"


def test_permission_settings_opens_only_the_allowlisted_macos_pane(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    client._command_context = threading.local()
    client._command_context.cmd_id = "permission-request-1"
    responses = []
    calls = []
    client._publish_response = lambda topic, payload: responses.append((topic, dict(payload))) or True
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))

    result = client._do_permission_settings({"permission": "camera"})

    assert calls == [(([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
    ],), {"check": False, "capture_output": True, "text": True, "timeout": 10})]
    assert result["ok"] is True
    assert result["permission"] == "camera"
    assert result["id"] == "permission-request-1"
    assert responses[0][0] == "permission_settings"
    assert "XIDER" in responses[0][1]["text"]


def test_permission_settings_rejects_arbitrary_or_unknown_targets(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    client._command_context = threading.local()
    responses = []
    calls = []
    client._publish_response = lambda topic, payload: responses.append((topic, dict(payload))) or True
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    result = client._do_permission_settings({"permission": "file:///tmp/anything"})

    assert calls == []
    assert result["ok"] is False
    assert result["permission"] == "unknown"
    assert responses[0][0] == "permission_settings"


def test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources():
    snapshot = mcs._regional_settings_snapshot()

    assert snapshot["schema"] == 1
    assert snapshot["feature_id"] == "D01-F23"
    assert snapshot["source"] == "local_os_runtime_apis"
    assert snapshot["state"] in {"supported", "unknown"}
    assert datetime.datetime.fromisoformat(snapshot["checked_at"]).utcoffset() == datetime.timedelta(0)
    assert set(snapshot["data"]) == {"timezone", "locale"}
    assert snapshot["data"]["timezone"]["state"] in {"supported", "unknown"}
    assert snapshot["data"]["locale"]["state"] in {"supported", "unknown"}
    assert snapshot["data"]["timezone"]["source"] == "python_localtime"
    assert snapshot["data"]["locale"]["source"] == "python_runtime_locale"
    offset = snapshot["data"]["timezone"]["utc_offset_minutes"]
    assert offset is None or type(offset) is int


def test_sysinfo_embeds_the_correlated_regional_snapshot(monkeypatch):
    monkeypatch.setattr(mcs.psutil, "cpu_percent", lambda **_kwargs: 1)
    monkeypatch.setattr(mcs.psutil, "virtual_memory", lambda: SimpleNamespace(used=1, total=8, percent=12.5))
    monkeypatch.setattr(mcs.psutil, "disk_usage", lambda _path: SimpleNamespace(used=1, total=10, percent=10))
    monkeypatch.setattr(mcs.psutil, "boot_time", lambda: 1000)
    monkeypatch.setattr(mcs.psutil, "sensors_battery", lambda: None)
    monkeypatch.setattr(mcs, "_format_battery", lambda _value: "unknown")
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload))) or True

    client._do_sysinfo({"id": "0123456789ab"})

    topic, payload = sent[0]
    snapshot = payload["regional_settings"]
    assert topic == "sysinfo"
    assert snapshot["device_id"] == mcs.DEVICE_ID
    assert snapshot["request_id"] == "0123456789ab"


def test_cpu_history_returns_bounded_correlated_samples_for_selected_period(monkeypatch):
    readings = iter([10.0, 35.5, 80.0])
    monkeypatch.setattr(mcs.psutil, "cpu_percent", lambda interval: next(readings))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_cpu_history({"id": "cpu-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "cpu_history"
    assert result["schema"] == "xider.cpu-history.v1"
    assert result["feature_id"] == "D01-F02"
    assert result["device_id"] == mcs.DEVICE_ID
    assert result["request_id"] == "cpu-ticket"
    assert result["state"] == "supported"
    assert result["source"] == "psutil.cpu_percent"
    assert result["data"] == {
        "period_seconds": 15,
        "sample_interval_seconds": 5,
        "samples": [
            {"offset_seconds": 5, "percent": 10.0},
            {"offset_seconds": 10, "percent": 35.5},
            {"offset_seconds": 15, "percent": 80.0},
        ],
    }


def test_cpu_history_rejects_unbounded_period_without_sampling(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.psutil, "cpu_percent", lambda **kwargs: calls.append(kwargs))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_cpu_history({"id": "ticket", "period_seconds": 3600})

    assert calls == []
    assert sent[0][1]["state"] == "unknown"
    assert sent[0][1]["data"] is None


def test_memory_history_returns_bounded_correlated_ram_and_swap_samples(monkeypatch):
    from types import SimpleNamespace

    ram = iter([24.0, 36.5, 48.0])
    swap = iter([10.0, 20.0, 30.0])
    waits = []
    monkeypatch.setattr(mcs.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(mcs.psutil, "virtual_memory", lambda: SimpleNamespace(percent=next(ram)))
    monkeypatch.setattr(mcs.psutil, "swap_memory", lambda: SimpleNamespace(total=1024, percent=next(swap)))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_history({"id": "memory-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "memory_history"
    assert result["schema"] == "xider.memory-history.v1"
    assert result["feature_id"] == "D01-F03"
    assert result["device_id"] == mcs.DEVICE_ID
    assert result["request_id"] == "memory-ticket"
    assert result["state"] == "supported"
    assert result["source"] == "psutil.virtual_memory+psutil.swap_memory"
    assert waits == [5, 5, 5]
    assert result["data"]["swap_state"] == "supported"
    assert result["data"]["samples"] == [
        {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": 10.0},
        {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": 20.0},
        {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": 30.0},
    ]


def test_volume_history_keeps_real_samples_and_withholds_short_term_forecast(monkeypatch):
    monkeypatch.setattr(mcs, "_VOLUME_HISTORY", {})
    monkeypatch.setattr(mcs.time, "time", lambda: 1_800_000_000)
    monkeypatch.setattr(mcs.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="/dev/disk1s1", mountpoint="/", fstype="apfs")
    ])
    monkeypatch.setattr(mcs.psutil, "disk_usage", lambda _path: SimpleNamespace(
        total=1_000_000_000_000, used=400_000_000_000, free=600_000_000_000,
    ))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_volume_history({"id": "volume-ticket"})

    topic, result = sent[0]
    assert topic == "volume_history"
    assert result["schema"] == "xider.volume-history.v1"
    assert result["feature_id"] == "D01-F04"
    assert result["device_id"] == mcs.DEVICE_ID
    assert result["request_id"] == "volume-ticket"
    assert result["state"] == "supported"
    volume = result["data"]["volumes"][0]
    assert volume["used_bytes"] == 400_000_000_000
    assert volume["used_percent"] == 40.0
    assert volume["trend_state"] == "insufficient_history"
    assert volume["estimated_hours_to_full"] is None


def test_macos_volume_topology_groups_mount_aliases_without_device_paths(monkeypatch):
    monkeypatch.setattr(mcs.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="/dev/disk1s1", mountpoint="/", fstype="apfs"),
        SimpleNamespace(device="/dev/disk1s1", mountpoint="/System/Volumes/Data", fstype="apfs"),
        SimpleNamespace(device="/dev/disk2s1", mountpoint="/Volumes/Backup", fstype="apfs"),
    ])
    monkeypatch.setattr(mcs, "_volume_topology_device", lambda path: 1 if path != "/Volumes/Backup" else 2)

    snapshot = mcs._read_volume_topology_snapshot()

    assert snapshot["state"] == "supported"
    assert len(snapshot["volumes"]) == 2
    assert snapshot["volumes"][0]["mount_paths"] == ["/", "/System/Volumes/Data"]
    assert snapshot["volumes"][0]["volume_key"].startswith("vol-")
    assert "/dev/disk" not in json.dumps(snapshot)


def test_macos_clock_drift_compares_wall_and_monotonic_elapsed_without_setting_time(monkeypatch):
    wall = iter([10_000_000_000, 12_123_000_000])
    monotonic = iter([50_000, 2_000_050_000])
    monkeypatch.setattr(mcs.time, "time_ns", lambda: next(wall))
    monkeypatch.setattr(mcs.time, "perf_counter_ns", lambda: next(monotonic))
    monkeypatch.setattr(mcs.time, "sleep", lambda _seconds: None)

    result = mcs._read_clock_drift_snapshot()

    assert result["state"] == "supported"
    assert result["source"] == "wall_vs_monotonic_elapsed"
    assert result["drift_ms"] == 123
    assert result["sample_ms"] == 2000
    assert "clock_drift" in mcs.SUPPORTED_COMMANDS


def test_macos_clock_drift_command_envelope_is_correlated(monkeypatch):
    monkeypatch.setattr(mcs, "_read_clock_drift_snapshot", lambda: {
        "state": "supported", "source": "wall_vs_monotonic_elapsed", "drift_ms": -123, "sample_ms": 2000,
    })
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    client._do_clock_drift({"id": "clock-ticket"})

    topic, payload = published[0]
    assert topic == "clock_drift"
    assert payload["feature_id"] == "D01-F22"
    assert payload["schema"] == "xider.clock-drift.v1"
    assert payload["device_id"] == "mac-1"
    assert payload["request_id"] == "clock-ticket"
    assert payload["checked_at"].endswith("+00:00")
    assert payload["data"] == {"drift_ms": -123, "sample_ms": 2000}


def test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours(monkeypatch):
    monkeypatch.setattr(mcs, "_VOLUME_HISTORY", {})
    clock = iter([1_800_000_000, 1_800_000_000 + 8 * 3600])
    usage = iter([
        SimpleNamespace(total=1_000_000_000_000, used=400_000_000_000, free=600_000_000_000),
        SimpleNamespace(total=1_000_000_000_000, used=600_000_000_000, free=400_000_000_000),
    ])
    monkeypatch.setattr(mcs.time, "time", lambda: next(clock))
    monkeypatch.setattr(mcs.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="/dev/disk1s1", mountpoint="/", fstype="apfs")
    ])
    monkeypatch.setattr(mcs.psutil, "disk_usage", lambda _path: next(usage))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_volume_history({"id": "first"})
    client._do_volume_history({"id": "second"})

    volume = sent[1][1]["data"]["volumes"][0]
    assert volume["trend_state"] == "increasing"
    assert volume["observed_span_seconds"] == 8 * 3600
    assert volume["delta_used_bytes"] == 200_000_000_000
    assert volume["estimated_hours_to_full"] == 16.0


def test_memory_pressure_utility_sample_is_a_free_percent_metric_not_activity_monitor_level(monkeypatch):
    class Result:
        returncode = 0
        stdout = "System-wide memory free percentage: 42%\n"
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/bin/memory_pressure" if name == "memory_pressure" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    sample = mcs._read_memory_pressure_sample()

    assert sample == {
        "state": "supported", "available_percent": 42.0,
        "source": "macos_memory_pressure_utility_free_percent",
    }


def test_memory_pressure_utility_malformed_output_is_unknown_not_zero(monkeypatch):
    class Result:
        returncode = 0
        stdout = "memory pressure not available\n"
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/bin/memory_pressure" if name == "memory_pressure" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    sample = mcs._read_memory_pressure_sample()

    assert sample["state"] == "unknown"
    assert sample["available_percent"] is None


def test_memory_pressure_history_samples_native_source_and_marks_its_contract(monkeypatch):
    observations = iter([
        {"state": "supported", "available_percent": 61.0, "source": "macos_memory_pressure_utility_free_percent"},
        {"state": "supported", "available_percent": 52.0, "source": "macos_memory_pressure_utility_free_percent"},
        {"state": "supported", "available_percent": 42.0, "source": "macos_memory_pressure_utility_free_percent"},
    ])
    waits = []
    monkeypatch.setattr(mcs, "_read_memory_pressure_sample", lambda: next(observations))
    monkeypatch.setattr(mcs.time, "sleep", lambda seconds: waits.append(seconds))
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_pressure_history({"id": "pressure-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "memory_pressure_history"
    assert result["schema"] == "xider.memory-pressure-history.v1"
    assert result["feature_id"] == "D01-F05"
    assert result["device_id"] == mcs.DEVICE_ID
    assert result["request_id"] == "pressure-ticket"
    assert result["source"] == "macos_memory_pressure_utility_free_percent"
    assert result["state"] == "supported"
    assert waits == [5, 5, 5]
    assert result["data"]["samples"] == [
        {"offset_seconds": 5, "state": "supported", "available_percent": 61.0},
        {"offset_seconds": 10, "state": "supported", "available_percent": 52.0},
        {"offset_seconds": 15, "state": "supported", "available_percent": 42.0},
    ]


def test_macos_thermal_snapshot_is_explicitly_unsupported_without_a_public_sensor_source():
    result = mcs._read_thermal_snapshot()

    assert result == {
        "state": "unsupported", "source": "macos_public_temperature_sensor_api_unavailable",
        "sensors": [],
    }


def test_macos_battery_health_reads_design_and_full_capacity_from_system_profiler_json(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"SPPowerDataType": [{"SPBatteryData": [{
            "sppower_battery_health": {"sppower_battery_health_condition": "Normal"},
            "sppower_battery_health_data": {
                "design_capacity_milliamp_hours": "5000",
                "full_charge_capacity_milliamp_hours": "4250",
            },
        }]}]})
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/sbin/system_profiler" if name == "system_profiler" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    result = mcs._read_battery_health_snapshot()

    assert result == {
        "state": "supported", "source": "macos_system_profiler_battery_capacity",
        "batteries": [{"label": "Internal battery", "design_capacity_mah": 5000,
                        "full_charge_capacity_mah": 4250, "health_percent": 85.0,
                        "condition": "Normal", "quality": "system_profiler"}],
    }


def test_macos_battery_health_missing_capacity_is_unknown_not_zero(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"SPPowerDataType": [{"SPBatteryData": [{
            "sppower_battery_health": {"sppower_battery_health_condition": "Normal"},
        }]}]})
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda _name: "/usr/sbin/system_profiler")
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    result = mcs._read_battery_health_snapshot()

    assert result["state"] == "unknown"
    assert result["batteries"] == []


def test_macos_battery_cycle_snapshot_reads_reported_system_profiler_count(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"SPPowerDataType": [{"SPBatteryData": [{
            "sppower_battery_health_data": {"sppower_battery_cycle_count": "487"},
        }]}]})
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/sbin/system_profiler" if name == "system_profiler" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    result = mcs._read_battery_cycle_snapshot()

    assert result == {
        "state": "supported", "source": "macos_system_profiler_battery_cycle_count",
        "batteries": [{"label": "Internal battery", "cycle_count": 487, "quality": "system_profiler"}],
    }


def test_macos_battery_cycle_snapshot_missing_field_is_unknown(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"SPPowerDataType": [{"SPBatteryData": [{
            "sppower_battery_health": {"sppower_battery_health_condition": "Normal"},
        }]}]})
        stderr = ""

    monkeypatch.setattr(mcs.shutil, "which", lambda _name: "/usr/sbin/system_profiler")
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: Result())

    result = mcs._read_battery_cycle_snapshot()

    assert result == {
        "state": "unknown", "source": "macos_system_profiler_battery_cycle_count", "batteries": [],
    }


def test_macos_energy_usage_is_explicitly_unsupported_without_a_public_per_app_provider(monkeypatch):
    published = []
    instance = mcs.XgentClient.__new__(mcs.XgentClient)
    instance._publish_response = lambda *args: published.append(args)
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")

    assert "energy_usage" in mcs.SUPPORTED_COMMANDS
    instance._do_energy_usage({"id": "req-1"})

    assert published[0][0] == "energy_usage"
    assert published[0][1]["state"] == "unsupported"
    assert published[0][1]["source"] == "macos_public_per_app_energy_api_unavailable"
    assert published[0][1]["data"] == {"unit": None, "apps": []}


def test_macos_sleep_wake_snapshot_uses_pmset_and_publishes_redacted_correlated_events(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    sleep_at = (now - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S %z")
    wake_at = (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S %z")

    class Result:
        returncode = 0
        stdout = (
            f"{sleep_at} Sleep Entering Sleep state due to 'private lid detail'\n"
            f"{wake_at} Wake Wake from Normal Sleep [CDNVA]\n"
        )
        stderr = ""

    calls = []
    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/bin/pmset" if name == "pmset" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result())
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "sleep_wake_history" in mcs.SUPPORTED_COMMANDS
    snapshot = mcs._read_sleep_wake_history_snapshot()
    client._do_sleep_wake_history({"id": "sleep-ticket"})

    assert calls[0][0] == ["/usr/bin/pmset", "-g", "log"]
    assert calls[0][1]["timeout"] <= 15
    assert snapshot["state"] == "supported" and len(snapshot["events"]) == 2
    assert "private lid detail" not in repr(snapshot)
    assert published[0][0] == "sleep_wake_history"
    assert published[0][1]["feature_id"] == "D01-F10"
    assert published[0][1]["request_id"] == "sleep-ticket"
    assert published[0][1]["data"]["events"] == snapshot["events"]


def test_macos_startup_duration_is_explicitly_unsupported_without_reliable_public_source(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "startup_duration" in mcs.SUPPORTED_COMMANDS
    assert mcs._read_startup_duration_snapshot()["state"] == "unsupported"
    client._do_startup_duration({"id": "boot-ticket"})

    assert calls == []
    assert published[0][0] == "startup_duration"
    assert published[0][1]["feature_id"] == "D01-F11"
    assert published[0][1]["request_id"] == "boot-ticket"
    assert published[0][1]["state"] == "unsupported"


def test_macos_smart_counter_trend_is_explicitly_unsupported(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "smart_trend" in mcs.SUPPORTED_COMMANDS
    assert mcs._read_smart_trend_snapshot() == {"state": "unsupported", "disks": []}
    client._do_smart_trend({"id": "smart-ticket"})

    assert calls == []
    assert published[0][0] == "smart_trend"
    assert published[0][1]["feature_id"] == "D01-F20"
    assert published[0][1]["request_id"] == "smart-ticket"
    assert published[0][1]["state"] == "unsupported"


def test_macos_pending_restart_is_explicitly_unsupported_without_reliable_public_source(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "pending_restart" in mcs.SUPPORTED_COMMANDS
    assert mcs._read_pending_restart_snapshot() == {
        "state": "unsupported", "source": "macos_pending_restart_flag_unavailable",
        "pending": None, "reasons": [], "completeness": "unknown",
    }
    client._do_pending_restart({"id": "restart-ticket"})

    assert calls == []
    assert published[0][0] == "pending_restart"
    assert published[0][1]["feature_id"] == "D01-F12"
    assert published[0][1]["request_id"] == "restart-ticket"
    assert published[0][1]["state"] == "unsupported"


def test_macos_os_support_identity_reports_only_system_version(monkeypatch):
    monkeypatch.setattr(mcs.platform, "mac_ver", lambda: ("15.7.9", ("", "", ""), "arm64"))
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert mcs._read_os_support_identity() == {
        "state": "supported", "family": "macos", "release": "15.7.9", "edition": None,
    }
    client._do_os_support({"id": "os-ticket"})

    assert published[0][0] == "os_support"
    assert published[0][1]["feature_id"] == "D01-F13"
    assert published[0][1]["request_id"] == "os-ticket"
    assert published[0][1]["data"]["release"] == "15.7.9"


def test_macos_driver_inventory_is_explicitly_unsupported_without_stable_public_list(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    assert "driver_inventory" in mcs.SUPPORTED_COMMANDS
    assert mcs._read_driver_inventory_snapshot() == {"state": "unsupported", "drivers": []}
    client._do_driver_inventory({"id": "driver-ticket"})

    assert calls == []
    assert published[0][0] == "driver_inventory"
    assert published[0][1]["feature_id"] == "D01-F15"
    assert published[0][1]["request_id"] == "driver-ticket"
    assert published[0][1]["state"] == "unsupported"


def test_macos_installed_app_inventory_reads_bounded_bundle_metadata_only(tmp_path, monkeypatch):
    app = tmp_path / "Demo.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Demo", "CFBundleShortVersionString": "1.4", "CFBundleIdentifier": "org.example.demo",
        "CFBundleExecutable": "secret-executable-name",
    }))
    monkeypatch.setattr(mcs, "APPLICATIONS_PATH", str(tmp_path), raising=False)
    snapshot = mcs._read_installed_app_inventory_snapshot()
    assert snapshot == {"state": "supported", "apps": [{"name": "Demo", "publisher": None, "version": "1.4"}]}
    assert "org.example.demo" not in json.dumps(snapshot)
    assert "secret-executable-name" not in json.dumps(snapshot)
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_installed_app_inventory({"id": "apps-ticket"})
    assert published[0][0] == "installed_app_inventory"
    assert published[0][1]["feature_id"] == "D01-F16"
    assert published[0][1]["request_id"] == "apps-ticket"


def test_macos_startup_inventory_is_explicitly_unsupported_without_querying_login_items(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert mcs._read_startup_inventory_snapshot() == {"state": "unsupported", "entries": []}
    assert calls == []
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_startup_inventory({"id": "startup-ticket"})
    assert published[0][0] == "startup_inventory"
    assert published[0][1]["feature_id"] == "D01-F17"
    assert published[0][1]["state"] == "unsupported"


def test_macos_service_inventory_is_explicitly_unsupported_without_shell_probing(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert mcs._read_service_inventory_snapshot() == {"state": "unsupported", "services": []}
    assert calls == []
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_service_inventory({"id": "service-ticket"})
    assert published[0][0] == "service_inventory"
    assert published[0][1]["feature_id"] == "D01-F18"
    assert published[0][1]["state"] == "unsupported"


def test_macos_crash_summary_is_unsupported_without_exporting_private_unified_log(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert mcs._read_crash_summary_snapshot() == {"state": "unsupported", "events": []}
    assert calls == []
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    client._do_crash_summary({"id": "crash-ticket"})
    assert published[0][0] == "crash_summary"
    assert published[0][1]["feature_id"] == "D01-F19"
    assert published[0][1]["state"] == "unsupported"


def test_macos_hardware_inventory_uses_sysctl_allowlist_without_serials(monkeypatch):
    outputs = {"hw.model": "MacBookAir10,1", "hw.memsize": "17179869184", "machdep.cpu.brand_string": ""}
    calls = []

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, key):
            self.stdout = outputs[key]

    monkeypatch.setattr(mcs.shutil, "which", lambda name: "/usr/sbin/sysctl" if name == "sysctl" else None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or Result(args[-1]))
    monkeypatch.setattr(mcs.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))

    inventory = mcs._read_hardware_inventory_snapshot()
    assert len(calls) == 3 and all(call[1]["timeout"] <= 5 for call in calls)
    calls.clear()
    client._do_hardware_inventory({"id": "hardware-ticket"})

    assert inventory == {
        "state": "supported", "manufacturer": "Apple", "model": "MacBookAir10,1",
        "cpu": None, "architecture": "arm64", "memory_gib": 16, "graphics": [],
    }
    assert len(calls) == 3 and all(call[1]["timeout"] <= 5 for call in calls)
    assert published[0][0] == "hardware_inventory"
    assert published[0][1]["feature_id"] == "D01-F14"
    assert published[0][1]["request_id"] == "hardware-ticket"
    assert "serial" not in json.dumps(published).lower()


def test_macos_external_ip_response_correlates_public_result_and_hides_provider_error(monkeypatch):
    import io
    import urllib.request

    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-1")
    published = []
    client = mcs.XgentClient.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"8.8.4.4"))
    client._do_ext_ip({"id": "mac-ip-check"})

    result = published[-1][1]
    assert published[-1][0] == "ext_ip"
    assert result["schema"] == "xider.external-ip.v1"
    assert result["feature_id"] == "D04-F30"
    assert result["request_id"] == "mac-ip-check"
    assert result["state"] == "available" and result["ip"] == "8.8.4.4"

    def failed(*_args, **_kwargs):
        raise OSError("secret proxy detail")

    monkeypatch.setattr(urllib.request, "urlopen", failed)
    client._do_ext_ip({"id": "mac-ip-fail"})
    result = published[-1][1]
    assert result["state"] == "unknown" and result["ip"] == ""
    assert "secret proxy detail" not in json.dumps(result)
