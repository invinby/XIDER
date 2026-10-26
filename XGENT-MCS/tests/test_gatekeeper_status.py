"""Gatekeeper policy status is read-only and excludes raw output."""

from types import SimpleNamespace

import xgent_mcs as mcs


def test_gatekeeper_status_parses_enabled_and_disabled_without_shell(monkeypatch):
    answers = iter(("assessments enabled\n", "assessments disabled\n"))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=next(answers), stderr="", returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", run)
    enabled = mcs._read_gatekeeper_status_snapshot()
    disabled = mcs._read_gatekeeper_status_snapshot()

    assert enabled["data"] == {"enabled": True}
    assert disabled["data"] == {"enabled": False}
    assert enabled["state"] == disabled["state"] == "supported"
    assert all(argv == ["spctl", "--status"] for argv, _kwargs in calls)
    assert all(kwargs.get("shell") is False for _argv, kwargs in calls)


def test_gatekeeper_status_unknown_redacts_unrecognized_output(monkeypatch):
    monkeypatch.setattr(
        mcs.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="unexpected app/path SECRET-123", stderr="private SECRET-456", returncode=0
        ),
    )

    snapshot = mcs._read_gatekeeper_status_snapshot()

    assert snapshot["state"] == "unknown"
    assert snapshot["data"] == {"enabled": None}
    assert "SECRET" not in repr(snapshot)
    assert "unexpected" not in repr(snapshot)


def test_gatekeeper_agent_reply_is_correlated_and_sanitized(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(
        mcs,
        "_read_gatekeeper_status_snapshot",
        lambda: {"state": "supported", "source": "spctl_status", "data": {"enabled": True}},
    )

    client._do_gatekeeper_status({"id": "request-789"})

    topic, reply = published[0]
    assert topic == "output"
    assert reply["type"] == "gatekeeper_status"
    assert reply["feature_id"] == "M01-F03"
    assert reply["id"] == "request-789"
    assert reply["data"] == {"enabled": True}
    assert "включён" in reply["text"]
    assert "secret" not in repr(reply).lower()
