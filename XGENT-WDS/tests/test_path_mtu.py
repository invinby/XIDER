import subprocess

import pytest

import path_mtu


def test_search_finds_largest_confirmed_payload_and_reports_path_mtu():
    result = path_mtu.search_path_mtu(lambda size: "reachable" if size <= 1400 else "too_large")
    assert result == {"state": "supported", "mtu": 1428, "max_tested": False}


def test_search_keeps_upper_bound_only_when_maximum_test_responds():
    result = path_mtu.search_path_mtu(lambda _size: "reachable")
    assert result == {"state": "supported", "mtu": 1500, "max_tested": True}


def test_search_returns_unknown_when_smallest_packet_is_not_confirmed():
    assert path_mtu.search_path_mtu(lambda _size: "unknown")["state"] == "unknown"


@pytest.mark.parametrize("target", ["router.example", "0.0.0.0", "224.0.0.1", "2001:db8::1"])
def test_mtu_rejects_non_ipv4_unicast_target_before_process(target):
    called = []
    with pytest.raises(ValueError):
        path_mtu.probe_ipv4_mtu(target, 1200, platform="windows", runner=lambda *a, **k: called.append(a))
    assert called == []


def test_windows_probe_uses_df_single_ping_without_shell():
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "Reply from 203.0.113.9: bytes=1200 time=10ms TTL=50", "")
    assert path_mtu.probe_ipv4_mtu("203.0.113.9", 1200, platform="windows", runner=runner,
                                    which=lambda name: f"C:/Windows/{name}") == "reachable"
    args, kwargs = calls[0]
    assert args == ["C:/Windows/ping.exe", "/n", "1", "/w", "800", "/f", "/l", "1200", "/4", "203.0.113.9"]
    assert "shell" not in kwargs and kwargs["timeout"] <= 1.25


def test_agent_publishes_correlated_path_mtu_result(monkeypatch):
    import xgent_wds as agent
    monkeypatch.setattr(path_mtu, "diagnose_path_mtu", lambda target, **_kwargs: {
        "state": "supported", "mtu": 1428, "max_tested": False,
        "source": path_mtu.WINDOWS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_path_mtu({"id": "mtu-1", "target": "203.0.113.9"})
    topic, result = sent[0]
    assert topic == "path_mtu" and result["schema"] == "xider.path-mtu.v1"
    assert result["feature_id"] == "D04-F10" and result["request_id"] == "mtu-1"
    assert result["device_id"] == agent.DEVICE_ID and result["data"]["mtu"] == 1428


def test_agent_does_not_present_an_ambiguous_probe_as_a_path_mtu(monkeypatch):
    import xgent_wds as agent
    monkeypatch.setattr(path_mtu, "diagnose_path_mtu", lambda target, **_kwargs: {
        "state": "unknown", "mtu": None, "max_tested": False,
        "target": target, "source": path_mtu.WINDOWS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_path_mtu({"id": "mtu-ambiguous", "target": "203.0.113.9"})
    result = sent[0][1]
    assert result["state"] == "unknown" and result["data"] is None
    assert result["detail"] == "probe_no_confirmed_reply"
