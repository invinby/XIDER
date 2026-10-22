import subprocess

import gateway_probe


def test_default_gateway_parsers_accept_only_literal_gateway_addresses():
    assert gateway_probe.parse_windows_gateway("192.168.1.1\r\n") == "192.168.1.1"
    assert gateway_probe.parse_windows_gateway("not-an-ip\r\n") is None
    assert gateway_probe.parse_macos_gateway("gateway: 192.168.1.254\ninterface: en0\n") == "192.168.1.254"
    assert gateway_probe.parse_macos_gateway("gateway: malicious.example\n") is None


def test_gateway_probe_reports_reachable_only_after_echo_reply():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if "powershell" in args[0]:
            return subprocess.CompletedProcess(args, 0, "192.168.1.1\n", "")
        return subprocess.CompletedProcess(args, 0, "Reply from 192.168.1.1", "")

    result = gateway_probe.probe_gateway(
        platform="windows", runner=runner, which=lambda name: f"{name}.exe",
    )

    assert result == {
        "gateway": "192.168.1.1", "state": "reachable",
        "source": gateway_probe.SOURCE, "detail": None,
    }
    assert calls[1][0][-1] == "192.168.1.1"
    assert calls[1][1]["timeout"] <= 3


def test_gateway_without_icmp_reply_is_unknown_not_declared_down():
    def runner(args, **kwargs):
        if args[0] == "/sbin/route":
            return subprocess.CompletedProcess(args, 0, "gateway: 192.168.1.1\n", "")
        return subprocess.CompletedProcess(args, 1, "100% packet loss", "")

    result = gateway_probe.probe_gateway(
        platform="macos", runner=runner, which=lambda name: f"/sbin/{name}",
    )

    assert result["gateway"] == "192.168.1.1"
    assert result["state"] == "unknown"
    assert result["detail"] == "gateway_did_not_answer_icmp"


def test_gateway_probe_does_not_ping_when_default_route_is_missing():
    commands = []

    def runner(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    result = gateway_probe.probe_gateway(
        platform="macos", runner=runner, which=lambda name: f"/sbin/{name}",
    )

    assert result["state"] == "no_gateway"
    assert result["gateway"] is None
    assert len(commands) == 1


def test_agent_publishes_correlated_gateway_probe_result(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(gateway_probe, "probe_gateway", lambda: {
        "gateway": "192.168.1.1", "state": "reachable",
        "source": gateway_probe.SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_gateway_probe({"id": "gateway-request-1"})

    topic, result = sent[0]
    assert topic == "gateway_probe"
    assert result["schema"] == "xider.gateway-probe.v1"
    assert result["feature_id"] == "D04-F08"
    assert result["request_id"] == "gateway-request-1"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["data"] == {"gateway": "192.168.1.1"}


def test_agent_never_promotes_icmp_timeout_to_unreachable(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(gateway_probe, "probe_gateway", lambda: {
        "gateway": "192.168.1.1", "state": "unknown",
        "source": gateway_probe.SOURCE, "detail": "gateway_did_not_answer_icmp",
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append(dict(payload))
    client._do_gateway_probe({"id": "gateway-request-2"})
    assert sent[0]["state"] == "unknown"
