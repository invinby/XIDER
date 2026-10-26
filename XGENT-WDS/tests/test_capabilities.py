"""Snapshot contract tests without accessing OS permissions or hardware."""
import datetime

import xgent_wds as agent


def test_snapshot_inventory_is_complete_and_never_assumes_permission(monkeypatch):
    monkeypatch.setattr(agent, "_module_available", lambda _name: True)
    monkeypatch.setattr(agent, "_detect_features", lambda: {
        "screenshot": True, "clipboard": True, "battery": False,
    })
    # Permission status is OS-dependent. Keep this inventory test deterministic;
    # dedicated adapter tests cover the platform's passive permission report.
    monkeypatch.setattr("location_adapter.location_capability", lambda: (
        "needs_live_test", "source=system; optional source=ip_estimate; uncertainty=unknown"
    ))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    client._do_capabilities({})
    topic, snapshot = sent[0]
    assert topic == "capabilities"
    assert snapshot.get("schema") == 1
    assert snapshot["platform"] == agent.PLATFORM
    assert datetime.datetime.fromisoformat(snapshot["checked_at"]).utcoffset() == datetime.timedelta(0)
    items = {item["id"]: item for item in snapshot["items"]}
    assert set(items) == {"screen_recording", "camera", "microphone", "location", "accessibility", "file_access"}
    assert all(item["state"] in {"needs_live_test", "unavailable", "unknown"} for item in items.values())
    assert all(item["detail"] and "settings_uri" in item for item in items.values())
    assert "UAC" in items["accessibility"]["detail"]
    assert "source=ip_estimate" in items["location"]["detail"]
    assert "uncertainty=" in items["location"]["detail"]
    assert "memory_pressure_history" in snapshot["commands"]
    assert "features" in snapshot and "feature_status" in snapshot


def test_capabilities_include_passive_shell_runner_diagnostics(monkeypatch):
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(agent, "_detect_feature_status", lambda: {"shell": "supported"})
    monkeypatch.setattr(agent, "_shell_runner_diagnostic", lambda _client: {
        "schema": 1, "protocol_state": "ready", "executor_state": "available",
        "runner_state": "available", "permission_state": "available",
    }, raising=False)

    client._do_capabilities({})

    assert sent[0][1]["shell_runner"] == {
        "schema": 1, "protocol_state": "ready", "executor_state": "available",
        "runner_state": "available", "permission_state": "available",
    }


def test_missing_dependencies_are_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "_module_available", lambda _name: False)
    monkeypatch.setattr(agent, "_detect_features", lambda: {
        "screenshot": False, "clipboard": False, "battery": False,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    client._do_capabilities({})
    assert sent[0].get("schema") == 1
    items = {item["id"]: item for item in sent[0]["items"]}
    assert items["camera"]["state"] == "unavailable"
    assert items["microphone"]["state"] == "unavailable"
    assert items["screen_recording"]["state"] == "unavailable"


def test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources():
    snapshot = agent._regional_settings_snapshot()

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
    from types import SimpleNamespace

    monkeypatch.setattr(agent.psutil, "cpu_percent", lambda **_kwargs: 1)
    monkeypatch.setattr(agent.psutil, "virtual_memory", lambda: SimpleNamespace(used=1, total=8, percent=12.5))
    monkeypatch.setattr(agent.psutil, "disk_usage", lambda _path: SimpleNamespace(used=1, total=10, percent=10))
    monkeypatch.setattr(agent.psutil, "boot_time", lambda: 1000)
    monkeypatch.setattr(agent.psutil, "sensors_battery", lambda: None)
    monkeypatch.setattr(agent, "_format_battery", lambda _value: "unknown")
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload))) or True

    client._do_sysinfo({"id": "0123456789ab"})

    topic, payload = sent[0]
    snapshot = payload["regional_settings"]
    assert topic == "sysinfo"
    assert snapshot["device_id"] == agent.DEVICE_ID
    assert snapshot["request_id"] == "0123456789ab"


def test_cpu_history_returns_bounded_correlated_samples_for_selected_period(monkeypatch):
    readings = iter([10.0, 35.5, 80.0])
    monkeypatch.setattr(agent.psutil, "cpu_percent", lambda interval: next(readings))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_cpu_history({"id": "cpu-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "cpu_history"
    assert result["schema"] == "xider.cpu-history.v1"
    assert result["feature_id"] == "D01-F02"
    assert result["device_id"] == agent.DEVICE_ID
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
    monkeypatch.setattr(agent.psutil, "cpu_percent", lambda **kwargs: calls.append(kwargs))
    client = object.__new__(agent.XgentClient)
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
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(agent.psutil, "virtual_memory", lambda: SimpleNamespace(percent=next(ram)))
    monkeypatch.setattr(agent.psutil, "swap_memory", lambda: SimpleNamespace(total=1024, percent=next(swap)))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_history({"id": "memory-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "memory_history"
    assert result["schema"] == "xider.memory-history.v1"
    assert result["feature_id"] == "D01-F03"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["request_id"] == "memory-ticket"
    assert result["state"] == "supported"
    assert result["source"] == "psutil.virtual_memory+psutil.swap_memory"
    assert waits == [5, 5, 5]
    assert result["data"] == {
        "period_seconds": 15, "sample_interval_seconds": 5, "swap_state": "supported",
        "samples": [
            {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": 10.0},
            {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": 20.0},
            {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": 30.0},
        ],
    }


def test_memory_history_marks_absent_swap_unsupported_not_zero(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(agent.psutil, "virtual_memory", lambda: SimpleNamespace(percent=22.0))
    monkeypatch.setattr(agent.psutil, "swap_memory", lambda: SimpleNamespace(total=0, percent=0.0))
    monkeypatch.setattr(agent.time, "sleep", lambda _seconds: None)
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_history({"id": "memory-ticket", "period_seconds": 15})

    data = sent[0][1]["data"]
    assert sent[0][1]["state"] == "supported"
    assert data["swap_state"] == "unsupported"
    assert [item["swap_percent"] for item in data["samples"]] == [None, None, None]


def test_volume_history_keeps_real_samples_and_withholds_short_term_forecast(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(agent, "_VOLUME_HISTORY", {})
    monkeypatch.setattr(agent.time, "time", lambda: 1_800_000_000)
    monkeypatch.setattr(agent.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="disk-1", mountpoint="C:\\", fstype="NTFS")
    ])
    monkeypatch.setattr(agent.psutil, "disk_usage", lambda _path: SimpleNamespace(
        total=1_000_000_000_000, used=400_000_000_000, free=600_000_000_000,
    ))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_volume_history({"id": "volume-ticket"})

    topic, result = sent[0]
    assert topic == "volume_history"
    assert result["schema"] == "xider.volume-history.v1"
    assert result["feature_id"] == "D01-F04"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["request_id"] == "volume-ticket"
    assert result["state"] == "supported"
    volume = result["data"]["volumes"][0]
    assert volume["used_bytes"] == 400_000_000_000
    assert volume["used_percent"] == 40.0
    assert volume["trend_state"] == "insufficient_history"
    assert volume["estimated_hours_to_full"] is None


def test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(agent, "_VOLUME_HISTORY", {})
    clock = iter([1_800_000_000, 1_800_000_000 + 8 * 3600])
    usage = iter([
        SimpleNamespace(total=1_000_000_000_000, used=400_000_000_000, free=600_000_000_000),
        SimpleNamespace(total=1_000_000_000_000, used=600_000_000_000, free=400_000_000_000),
    ])
    monkeypatch.setattr(agent.time, "time", lambda: next(clock))
    monkeypatch.setattr(agent.psutil, "disk_partitions", lambda all=False: [
        SimpleNamespace(device="disk-1", mountpoint="C:\\", fstype="NTFS")
    ])
    monkeypatch.setattr(agent.psutil, "disk_usage", lambda _path: next(usage))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_volume_history({"id": "first"})
    client._do_volume_history({"id": "second"})

    volume = sent[1][1]["data"]["volumes"][0]
    assert volume["trend_state"] == "increasing"
    assert volume["observed_span_seconds"] == 8 * 3600
    assert volume["delta_used_bytes"] == 200_000_000_000
    assert volume["estimated_hours_to_full"] == 16.0


def test_memory_pressure_history_samples_native_source_and_marks_its_contract(monkeypatch):
    observations = iter([
        {"state": "low", "available_percent": None, "source": "windows_memory_resource_notification"},
        {"state": "intermediate", "available_percent": None, "source": "windows_memory_resource_notification"},
        {"state": "high", "available_percent": None, "source": "windows_memory_resource_notification"},
    ])
    waits = []
    monkeypatch.setattr(agent, "_read_memory_pressure_sample", lambda: next(observations))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: waits.append(seconds))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_pressure_history({"id": "pressure-ticket", "period_seconds": 15})

    topic, result = sent[0]
    assert topic == "memory_pressure_history"
    assert result["schema"] == "xider.memory-pressure-history.v1"
    assert result["feature_id"] == "D01-F05"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["request_id"] == "pressure-ticket"
    assert result["source"] == "windows_memory_resource_notification"
    assert result["state"] == "supported"
    assert waits == [5, 5, 5]
    assert result["data"]["samples"] == [
        {"offset_seconds": 5, "state": "low", "available_percent": None},
        {"offset_seconds": 10, "state": "intermediate", "available_percent": None},
        {"offset_seconds": 15, "state": "high", "available_percent": None},
    ]


def test_memory_pressure_invalid_period_does_not_query_native_source(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_read_memory_pressure_sample", lambda: calls.append(True))
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_memory_pressure_history({"id": "pressure-ticket", "period_seconds": 120})

    assert calls == []
    assert sent[0][0] == "memory_pressure_history"
    assert sent[0][1]["state"] == "unknown"
    assert sent[0][1]["data"] is None


def test_thermal_snapshot_reads_only_bounded_wmi_zones_and_converts_kelvin_tenths(monkeypatch):
    class Result:
        returncode = 0
        stdout = '[{"CurrentTemperature":3332},{"CurrentTemperature":3102}]'
        stderr = ""

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: Result())

    result = agent._read_thermal_snapshot()

    assert result["state"] == "supported"
    assert result["source"] == "windows_wmi_MSAcpi_ThermalZoneTemperature"
    assert result["sensors"] == [
        {"label": "Thermal zone 1", "temperature_c": 60.1, "quality": "native_provider"},
        {"label": "Thermal zone 2", "temperature_c": 37.1, "quality": "native_provider"},
    ]


def test_thermal_snapshot_missing_wmi_provider_is_unsupported_not_zero(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _name: None)

    result = agent._read_thermal_snapshot()

    assert result["state"] == "unsupported"
    assert result["sensors"] == []


def test_battery_health_joins_design_and_full_capacity_by_instance(monkeypatch):
    class Result:
        returncode = 0
        stdout = ('{"design":[{"InstanceName":"B","DesignedCapacity":40000},'
                  '{"InstanceName":"A","DesignedCapacity":50000}],'
                  '"full":[{"InstanceName":"A","FullChargedCapacity":42000},'
                  '{"InstanceName":"B","FullChargedCapacity":30000}]}')
        stderr = ""

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: Result())

    result = agent._read_battery_health_snapshot()

    assert result["state"] == "supported"
    assert result["source"] == "windows_wmi_battery_capacity"
    assert result["batteries"] == [
        {"label": "Battery 1", "design_capacity_mwh": 50000, "full_charge_capacity_mwh": 42000,
         "health_percent": 84.0, "quality": "native_provider"},
        {"label": "Battery 2", "design_capacity_mwh": 40000, "full_charge_capacity_mwh": 30000,
         "health_percent": 75.0, "quality": "native_provider"},
    ]


def test_battery_health_rejects_unmatched_or_relative_capacity_data(monkeypatch):
    class Result:
        returncode = 0
        stdout = ('{"design":[{"InstanceName":"A","DesignedCapacity":4294967295}],'
                  '"full":[{"InstanceName":"B","FullChargedCapacity":30000}]}')
        stderr = ""

    monkeypatch.setattr(agent.shutil, "which", lambda _name: "powershell.exe")
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: Result())

    result = agent._read_battery_health_snapshot()

    assert result["state"] == "unknown"
    assert result["batteries"] == []


def test_battery_cycle_snapshot_uses_only_positive_wmi_cycle_counters(monkeypatch):
    class Result:
        returncode = 0
        stdout = ('{"InstanceName":"ACPI\\\\BAT0\\\\1_0","CycleCount":321}')
        stderr = ""

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: Result())

    result = agent._read_battery_cycle_snapshot()

    assert result == {
        "state": "supported", "source": "windows_wmi_battery_cycle_count",
        "batteries": [{"label": "Battery 1", "cycle_count": 321, "quality": "native_provider"}],
    }


def test_battery_cycle_snapshot_treats_wmi_zero_as_unsupported(monkeypatch):
    class Result:
        returncode = 0
        stdout = '[{"InstanceName":"ACPI\\\\BAT0\\\\1_0","CycleCount":0}]'
        stderr = ""

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powershell.exe" if name == "powershell.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: Result())

    result = agent._read_battery_cycle_snapshot()

    assert result == {
        "state": "unsupported", "source": "windows_wmi_battery_cycle_count", "batteries": [],
    }


def test_energy_usage_snapshot_exports_srum_locally_and_returns_only_bounded_aggregate(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output_path = command[command.index("/output") + 1]
        with open(output_path, "w", encoding="utf-8-sig", newline="") as report:
            report.write(
                "AppId,CPU Energy (mJ),Network Energy (mJ),MeasuredPower\n"
                'C:\\Program Files\\Browser\\browser.exe,120,30,False\n'
            )
        return Result()

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powercfg.exe" if name == "powercfg.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", run)

    result = agent._read_energy_usage_snapshot()

    assert captured["command"][:3] == ["powercfg.exe", "/srumutil", "/output"]
    assert captured["command"][-1] == "/csv"
    assert captured["kwargs"]["timeout"] == 20
    assert result == {
        "state": "supported", "source": "windows_srum_energy_estimation", "unit": "mJ",
        "apps": [{"name": "browser", "energy_mj": 150, "quality": "estimated"}],
    }
    assert not __import__("os").path.exists(captured["command"][3])


def test_energy_usage_snapshot_is_explicitly_unknown_on_export_failure(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "Access is denied"

    captured = {}

    def run(command, **kwargs):
        captured["path"] = command[command.index("/output") + 1]
        return Result()

    monkeypatch.setattr(agent.shutil, "which", lambda name: "powercfg.exe" if name == "powercfg.exe" else None)
    monkeypatch.setattr(agent.subprocess, "run", run)

    result = agent._read_energy_usage_snapshot()

    assert result == {
        "state": "unknown", "source": "windows_srum_energy_estimation", "apps": [],
    }
    assert not __import__("os").path.exists(captured["path"])


def test_energy_usage_snapshot_marks_missing_powercfg_unsupported(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _name: None)

    assert agent._read_energy_usage_snapshot() == {
        "state": "unsupported", "source": "windows_srum_energy_estimation", "apps": [],
    }
