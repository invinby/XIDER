"""FileVault status is a read-only, redacted native OS snapshot."""

from types import SimpleNamespace

import xgent_mcs as mcs


def test_filevault_status_parses_only_on_off_and_omits_raw_output(monkeypatch):
    answers = iter(("FileVault is On.\n", "FileVault is Off.\n"))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=next(answers), stderr="", returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", run)

    on = mcs._read_filevault_status_snapshot()
    off = mcs._read_filevault_status_snapshot()

    assert on["state"] == off["state"] == "supported"
    assert on["data"] == {"enabled": True}
    assert off["data"] == {"enabled": False}
    assert all(argv == ["fdesetup", "status"] for argv, _kwargs in calls)
    assert all(kwargs.get("shell") is False for _argv, kwargs in calls)


def test_filevault_status_unknown_does_not_forward_command_output(monkeypatch):
    monkeypatch.setattr(
        mcs.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="unrecognized output with pretend recovery key SECRET-123",
            stderr="private diagnostic SECRET-456", returncode=0,
        ),
    )

    snapshot = mcs._read_filevault_status_snapshot()

    assert snapshot["state"] == "unknown"
    assert snapshot["data"] == {"enabled": None}
    assert "SECRET" not in repr(snapshot)
    assert "unrecognized" not in repr(snapshot)


def test_filevault_agent_reply_is_correlated_and_contains_no_key(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(
        mcs,
        "_read_filevault_status_snapshot",
        lambda: {"state": "supported", "source": "fdesetup_status", "data": {"enabled": True}},
    )

    client._do_filevault_status({"id": "request-123"})

    assert len(published) == 1
    topic, reply = published[0]
    assert topic == "output"
    assert reply["type"] == "filevault_status"
    assert reply["feature_id"] == "M01-F01"
    assert reply["id"] == "request-123"
    assert reply["data"] == {"enabled": True}
    assert "включён" in reply["text"]
    assert "recovery" not in repr(reply).lower()
