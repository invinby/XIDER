"""Persistence, bounds, and platform-path contract for command replay state."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from command_replay import CommandReplayLedger, default_replay_path


def test_default_replay_paths_match_the_approved_per_platform_locations():
    assert default_replay_path(
        "win32", local_app_data=r"C:\Users\example\AppData\Local"
    ) == Path(r"C:\Users\example\AppData\Local\XIDER\command-replay.json")
    assert default_replay_path(
        "darwin", home="/Users/example"
    ) == Path("/Users/example/Library/Application Support/XIDER/command-replay.json")
    with pytest.raises(OSError):
        default_replay_path("linux", home="/tmp")
    with pytest.raises(OSError):
        default_replay_path("win32", local_app_data="")


def test_restart_persists_only_replay_metadata_and_prunes_only_expired_entries(tmp_path):
    path = tmp_path / "ledger.json"
    first = CommandReplayLedger(path)

    assert first.reserve("one", 120, now=100)
    assert first.reserve("expired", 100, now=99)
    raw = path.read_text(encoding="utf-8")
    assert "command" not in raw.lower()
    assert "signature" not in raw.lower()
    assert "private" not in raw.lower()

    restarted = CommandReplayLedger(path)
    assert restarted.reserve("one", 150, now=110) is False
    assert restarted.reserve("expired", 150, now=100) is True
    assert json.loads(path.read_text(encoding="utf-8"))["entries"] == {
        "expired": 150,
        "one": 120,
    }


def test_corrupt_or_oversized_ledger_is_not_reset_or_replaced(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{broken", encoding="utf-8")
    ledger = CommandReplayLedger(path)

    assert ledger.reserve("request-1", 200, now=100) is False
    assert ledger.ensure_available(now=100) is False
    assert path.read_text(encoding="utf-8") == "{broken"

    path.write_text(json.dumps({"schema": 1, "entries": {}}), encoding="utf-8")
    oversized = CommandReplayLedger(path, max_bytes=64)
    path.write_text(" " * 65, encoding="utf-8")
    assert oversized.reserve("request-1", 200, now=100) is False


def test_capacity_and_expired_reservation_boundaries_fail_closed(tmp_path):
    ledger = CommandReplayLedger(tmp_path / "ledger.json", max_entries=1)
    assert ledger.reserve("first", 200, now=100)
    assert ledger.reserve("second", 200, now=100) is False
    assert ledger.reserve("late", 100, now=100) is False
    assert ledger.reserve("first", 100, now=100) is False


def test_concurrent_reservations_across_store_instances_have_one_winner(tmp_path):
    path = tmp_path / "ledger.json"
    ledgers = [CommandReplayLedger(path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=len(ledgers)) as pool:
        outcomes = list(pool.map(lambda ledger: ledger.reserve("same", 200, now=100), ledgers))

    assert sum(outcomes) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["entries"] == {"same": 200}
