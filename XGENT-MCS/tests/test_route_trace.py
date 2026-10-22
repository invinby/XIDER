import subprocess

import pytest

import route_trace


def test_trace_parser_keeps_only_bounded_ip_hops_and_detects_the_target():
    result = route_trace.parse_trace_output(
        " 1  192.168.1.1  0.5 ms\n 2  * * *\n 3  203.0.113.9  1.0 ms\n",
        "203.0.113.9", max_hops=3,
    )
    assert result == {
        "state": "complete", "target": "203.0.113.9", "reached": True,
        "hops": [
            {"index": 1, "address": "192.168.1.1"},
            {"index": 2, "address": None},
            {"index": 3, "address": "203.0.113.9"},
        ],
    }


def test_trace_parser_returns_partial_without_claiming_target_reached():
    result = route_trace.parse_trace_output("1 192.168.1.1 0.5 ms\n2 * * *\n", "203.0.113.9", max_hops=2)
    assert result["state"] == "partial" and result["reached"] is False
    assert result["hops"] == [{"index": 1, "address": "192.168.1.1"}, {"index": 2, "address": None}]


@pytest.mark.parametrize("target", ["router.example", "0.0.0.0", "224.0.0.1", "2001:db8::1%en0"])
def test_trace_route_rejects_nonliteral_or_nonunicast_targets_before_process_launch(target):
    called = []
    with pytest.raises(ValueError):
        route_trace.trace_route(target, platform="macos", runner=lambda *a, **k: called.append(a))
    assert called == []


def test_macos_trace_uses_numeric_mode_and_bounded_query_count():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "1 192.168.1.1 0.5 ms\n2 203.0.113.9 1.0 ms\n", "")

    result = route_trace.trace_route(
        "203.0.113.9", platform="macos", runner=runner, which=lambda name: f"/sbin/{name}",
    )
    args, kwargs = calls[0]
    assert args == ["/sbin/traceroute", "-n", "-q", "1", "-m", str(route_trace.MAX_HOPS), "-w", "1", "203.0.113.9"]
    assert "shell" not in kwargs and kwargs["timeout"] <= 20
    assert result["state"] == "complete" and result["reached"] is True


def test_agent_publishes_correlated_route_trace(monkeypatch):
    import xgent_mcs as agent

    monkeypatch.setattr(route_trace, "trace_route", lambda target, **_kwargs: {
        "target": target, "state": "partial", "reached": False,
        "hops": [{"index": 1, "address": "192.168.1.1"}],
        "source": route_trace.MACOS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_route_trace({"id": "trace-1", "target": "203.0.113.9"})
    topic, result = sent[0]
    assert topic == "route_trace" and result["schema"] == "xider.route-trace.v1"
    assert result["feature_id"] == "D04-F09" and result["request_id"] == "trace-1"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["data"]["hops"] == [{"index": 1, "address": "192.168.1.1"}]
