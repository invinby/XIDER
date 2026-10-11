"""Offline metadata-only action check persistence."""
import importlib.util
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest


def store_module():
    assert importlib.util.find_spec("check_store") is not None, "metadata-only check_store is missing"
    import check_store
    return check_store


def record(module, **changes):
    fields = dict(device_id="device-a", callback="cmd:sys_uptime", platform="windows",
                  agent_version="4.1.2", checked_at="2026-10-08T10:00:00+00:00",
                  latency_ms=120, outcome="succeeded", correlation_id="abc123")
    fields.update(changes)
    return module.CheckRecord(**fields)


def test_current_missing_old_unknown_and_separate_devices(tmp_path):
    m = store_module()
    store = m.CheckStore(tmp_path / "checks.json")
    assert store.get_last_check("device-a", "cmd:sys_uptime") is None
    first = record(m)
    store.record_check(first)
    store.record_check(record(m, device_id="device-b", outcome="unknown"))
    assert store.get_last_check("device-a", first.callback) == first
    assert store.get_last_check("device-b", first.callback).outcome == "unknown"
    now = datetime(2026, 10, 8, 10, 0, 20, tzinfo=timezone.utc)
    assert m.is_fresh(first, now=now)
    assert not m.is_fresh(first, now=now + timedelta(days=1))
    store.record_check(replace(first, checked_at="2026-10-07T10:00:00Z", outcome="failed"))
    assert store.get_last_check("device-a", first.callback).checked_at == first.checked_at


def test_atomic_reload_and_latest_bounded_records(tmp_path):
    m = store_module()
    path = tmp_path / "checks.json"
    store = m.CheckStore(path, max_records=2)
    for index in range(3):
        store.record_check(record(m, device_id=f"device-{index}", checked_at=f"2026-10-08T10:00:0{index}Z"))
    reloaded = m.CheckStore(path, max_records=2)
    assert reloaded.get_last_check("device-0", "cmd:sys_uptime") is None
    assert reloaded.get_last_check("device-2", "cmd:sys_uptime").outcome == "succeeded"
    assert len(json.loads(path.read_text())["records"]) == 2
    assert len(path.read_bytes()) <= m.MAX_BYTES
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("change", [dict(outcome="ok"), dict(latency_ms=-1), dict(latency_ms=True),
    dict(checked_at="yesterday"), dict(checked_at="2026-10-08T10:00:00"),
    dict(device_id="a" * 129), dict(device_id="/private/file"), dict(callback="cmd:screenshot"),
    dict(agent_version="token:secret"), dict(platform="secret text"), dict(correlation_id="secret/token"),
    dict(outcome={"raw": "private"})])
def test_rejects_invalid_or_private_fields_without_writing(tmp_path, change):
    m = store_module()
    path = tmp_path / "checks.json"
    with pytest.raises((ValueError, TypeError)):
        m.CheckStore(path).record_check(record(m, **change))
    assert not path.exists()


def test_failed_atomic_replace_keeps_previous_record(tmp_path, monkeypatch):
    m = store_module()
    path = tmp_path / "checks.json"
    store = m.CheckStore(path)
    first = record(m)
    store.record_check(first)
    original = path.read_bytes()
    def fail(*args):
        raise OSError("fixture replace failure")
    monkeypatch.setattr(m.os, "replace", fail)
    with pytest.raises(OSError):
        store.record_check(replace(first, checked_at="2026-10-08T10:01:00Z", outcome="failed"))
    assert path.read_bytes() == original
    assert store.get_last_check(first.device_id, first.callback) == first


def test_corrupt_store_is_not_overwritten(tmp_path):
    m = store_module()
    path = tmp_path / "checks.json"
    path.write_text("not JSON")
    with pytest.raises(ValueError):
        m.CheckStore(path).record_check(record(m))
    assert path.read_text() == "not JSON"


def test_symlink_store_is_not_overwritten_if_supported(tmp_path):
    m = store_module()
    actual = tmp_path / "actual.json"
    actual.write_text("private original")
    path = tmp_path / "checks.json"
    try:
        path.symlink_to(actual)
    except OSError:
        pytest.skip("local account cannot create symlinks")
    with pytest.raises(ValueError):
        m.CheckStore(path).record_check(record(m))
    assert actual.read_text() == "private original"


def test_serialized_byte_limit_refuses_write(tmp_path, monkeypatch):
    m = store_module()
    monkeypatch.setattr(m, "MAX_BYTES", 10)
    path = tmp_path / "checks.json"
    with pytest.raises(ValueError):
        m.CheckStore(path).record_check(record(m))
    assert not path.exists()


def test_none_device_and_extra_payload_fields_rejected(tmp_path):
    m = store_module()
    with pytest.raises(ValueError):
        m.CheckStore(tmp_path / "checks.json").record_check(record(m, device_id=None, correlation_id=None))
    with pytest.raises(TypeError):
        m.CheckRecord(**{**record(m).__dict__, "text": "PRIVATE"})


def test_future_check_is_not_fresh(tmp_path):
    m = store_module()
    assert not m.is_fresh(record(m), now=datetime(2026, 10, 7, tzinfo=timezone.utc))
