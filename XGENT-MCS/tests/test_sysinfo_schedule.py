from __future__ import annotations

import pytest
import threading

from sysinfo_schedule import load_schedule, next_sample_at, normalize_interval, save_schedule
import xgent_mcs as mcs


@pytest.mark.parametrize("value, expected", [(0, 0), (900, 900), (1800, 1800), (3600, 3600)])
def test_interval_is_limited_to_owner_visible_choices(value, expected):
    assert normalize_interval(value) == expected


@pytest.mark.parametrize("value", [True, -1, 1, 60, 899, 901, 7200, "900", None])
def test_interval_rejects_unapproved_or_mistyped_values(value):
    with pytest.raises(ValueError):
        normalize_interval(value)


def test_next_sample_is_utc_and_disabled_schedule_has_no_next_sample():
    assert next_sample_at(900, now=1_000) == 1_900
    assert next_sample_at(0, now=1_000) is None


def test_schedule_persists_only_allowlisted_state_and_disable_clears_it(tmp_path):
    path = tmp_path / "sysinfo_schedule.json"
    active = save_schedule(path, 900, "a" * 32, now=1_000)
    assert active == {"interval_seconds": 900, "schedule_id": "a" * 32, "next_sample_at": 1_900}
    assert load_schedule(path) == active
    disabled = save_schedule(path, 0, "b" * 32, now=1_100)
    assert disabled == {"interval_seconds": 0, "schedule_id": "b" * 32, "next_sample_at": None}
    assert load_schedule(path) == disabled


def test_corrupt_or_extra_schedule_data_fails_closed(tmp_path):
    path = tmp_path / "sysinfo_schedule.json"
    path.write_text('{"interval_seconds":900,"schedule_id":"' + "a" * 32 +
                    '","next_sample_at":1900,"hostname":"must_not_be_loaded"}', encoding="utf-8")
    assert load_schedule(path) == {"interval_seconds": 0, "schedule_id": None, "next_sample_at": None}


def test_agent_applies_owner_command_and_reports_correlated_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    client = object.__new__(mcs.XgentClient)
    client._sysinfo_schedule_wake = threading.Event()
    published = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: published.append((topic, payload)))
    client._do_sysinfo_interval({"id": "request-1", "interval_seconds": 900, "schedule_id": "a" * 32})
    assert published[-1][0] == "sysinfo_schedule"
    assert published[-1][1]["request_id"] == "request-1"
    assert published[-1][1]["next_sample_at"] > 0
    assert load_schedule(tmp_path / "sysinfo_schedule.json")["schedule_id"] == "a" * 32


def test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    save_schedule(tmp_path / "sysinfo_schedule.json", 900, "a" * 32, now=1_000)
    client = object.__new__(mcs.XgentClient)
    client._running = threading.Event()
    client._running.set()
    client._sysinfo_schedule_wake = threading.Event()
    published = []
    def publish(topic, payload):
        published.append((topic, payload))
        client._running.clear()
        return True
    monkeypatch.setattr(client, "_publish_response", publish)
    monkeypatch.setattr(mcs.psutil, "cpu_percent", lambda **_kwargs: 10.0)
    monkeypatch.setattr(mcs.psutil, "virtual_memory", lambda: SimpleNamespace(percent=20.0))
    monkeypatch.setattr(mcs.psutil, "disk_usage", lambda _path: SimpleNamespace(percent=30.0))
    monkeypatch.setattr(mcs.time, "time", lambda: 2_000)
    client._sysinfo_schedule_loop()
    topic, sample = published[0]
    assert topic == "sysinfo_schedule"
    assert sample["type"] == "sysinfo_summary_sample"
    assert sample["feature_id"] == "D01-F26" and sample["schedule_id"] == "a" * 32
    assert sample["summary"] == {"platform": "macOS", "cpu_percent": 10.0, "ram_percent": 20.0, "disk_percent": 30.0}
    assert sample["temperature"] == {
        "state": "unsupported", "source": "macos_public_temperature_sensor_api_unavailable", "max_celsius": None,
    }
    assert "hostname" not in sample and "mac" not in sample
    assert sample["next_sample_at"] == 2_900
    client._do_sysinfo_interval({"id": "request-2", "interval_seconds": 60, "schedule_id": "b" * 32})
    assert published[-1][1]["state"] == "unknown"
    assert load_schedule(tmp_path / "sysinfo_schedule.json")["schedule_id"] == "a" * 32
