"""SIP status is queried read-only and never forwards raw command output."""

from types import SimpleNamespace

import xgent_mcs as mcs


def test_sip_status_parses_enabled_and_disabled_without_shell(monkeypatch):
    answers = iter((
        "System Integrity Protection status: enabled.\n",
        "System Integrity Protection status: disabled.\n",
    ))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=next(answers), stderr="", returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", run)
    enabled = mcs._read_sip_status_snapshot()
    disabled = mcs._read_sip_status_snapshot()

    assert enabled["data"] == {"enabled": True}
    assert disabled["data"] == {"enabled": False}
    assert enabled["state"] == disabled["state"] == "supported"
    assert all(argv == ["csrutil", "status"] for argv, _kwargs in calls)
    assert all(kwargs.get("shell") is False for _argv, kwargs in calls)


def test_sip_status_unknown_redacts_unrecognized_output(monkeypatch):
    monkeypatch.setattr(
        mcs.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="unexpected recovery details SECRET-123", stderr="private SECRET-456", returncode=0
        ),
    )

    snapshot = mcs._read_sip_status_snapshot()

    assert snapshot["state"] == "unknown"
    assert snapshot["data"] == {"enabled": None}
    assert "SECRET" not in repr(snapshot)
    assert "unexpected" not in repr(snapshot)


def test_sip_agent_reply_is_correlated_and_sanitized(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(
        mcs,
        "_read_sip_status_snapshot",
        lambda: {"state": "supported", "source": "csrutil_status", "data": {"enabled": True}},
    )

    client._do_sip_status({"id": "request-456"})

    topic, reply = published[0]
    assert topic == "output"
    assert reply["type"] == "sip_status"
    assert reply["feature_id"] == "M01-F02"
    assert reply["id"] == "request-456"
    assert reply["data"] == {"enabled": True}
    assert "включена" in reply["text"]
    assert "recovery" not in repr(reply).lower()
