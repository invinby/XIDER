from pathlib import Path

from xg_telemetry.cache import CACHE_TTL_SECONDS, read_inventory_snapshot


def test_supported_snapshot_is_cached_and_force_bypasses_cache(tmp_path: Path):
    calls = []
    def reader():
        calls.append(len(calls) + 1)
        return {"state": "supported", "count": calls[-1]}

    first = read_inventory_snapshot("hardware", reader, tmp_path, now=100)
    cached = read_inventory_snapshot("hardware", reader, tmp_path, now=101)
    forced = read_inventory_snapshot("hardware", reader, tmp_path, force=True, now=102)

    assert first == {"state": "supported", "count": 1}
    assert cached == first
    assert forced == {"state": "supported", "count": 2}
    assert len(calls) == 2


def test_expired_or_future_cache_is_ignored(tmp_path: Path):
    calls = []
    def reader():
        calls.append(True)
        return {"state": "supported", "count": len(calls)}

    read_inventory_snapshot("drivers", reader, tmp_path, now=100)
    expired = read_inventory_snapshot("drivers", reader, tmp_path, now=100 + CACHE_TTL_SECONDS)
    future = read_inventory_snapshot("drivers", reader, tmp_path, now=99)

    assert expired["count"] == 2
    assert future["count"] == 3


def test_cache_failures_and_reader_failures_do_not_escape(tmp_path: Path, monkeypatch):
    unknown = {"state": "unknown", "drivers": []}
    result = read_inventory_snapshot("drivers", lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                                     tmp_path, unknown=unknown)
    assert result == unknown and result is not unknown

    def unsupported():
        return {"state": "unsupported", "drivers": []}
    result = read_inventory_snapshot("drivers", unsupported, tmp_path)
    assert result == {"state": "unsupported", "drivers": []}


def test_cache_name_cannot_escape_config_directory(tmp_path: Path):
    calls = []
    result = read_inventory_snapshot("../outside", lambda: calls.append(1), tmp_path)
    assert result == {"state": "unknown"}
    assert not calls
