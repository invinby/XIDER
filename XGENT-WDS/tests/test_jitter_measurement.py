import subprocess

import jitter_measurement


def test_icmp_jitter_uses_mean_absolute_delta_between_adjacent_rtts():
    values = [10.0, 12.0, 17.0, 17.0, 21.0, 25.0, 30.0, 31.0, 35.0, 40.0]
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        value = values[len(calls) - 1]
        return subprocess.CompletedProcess(args, 0, f"64 bytes from 192.0.2.8: icmp_seq=0 ttl=64 time={value} ms", "")

    result = jitter_measurement.measure_jitter(
        address="192.0.2.8", platform="macos", runner=runner, which=lambda name: "/sbin/ping"
    )

    assert result["state"] == "available"
    assert result["attempted"] == result["received"] == 10
    assert result["jitter_ms"] == 3.333
    assert len(calls) == 10
    assert all(call[1]["timeout"] <= 2.5 for call in calls)
    assert all(call[1].get("shell") is not True for call in calls)
    assert all(args[args.index("-W") + 1] == "1000" for args, _ in calls)


def test_icmp_jitter_does_not_bridge_lost_or_unclassified_probes():
    sequence = ["time=10 ms", "Request timed out.", "time=50 ms", "unrecognized", "time=54 ms"] + ["Request timed out."] * 5
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        text = sequence[len(calls) - 1]
        return subprocess.CompletedProcess(args, 1 if "timed out" in text else 0, text, "")

    result = jitter_measurement.measure_jitter(
        address="192.0.2.8", platform="windows", runner=runner, which=lambda name: "ping.exe"
    )

    assert result["state"] == "unknown"
    assert result["pairs"] == 0
    assert result["jitter_ms"] is None
    assert result["samples"][1]["state"] == "lost"
    assert result["samples"][3]["state"] == "unknown"
    assert len(calls) == 10


def test_icmp_jitter_rejects_broadcast_and_non_literal_addresses_before_probing():
    calls = []
    for address in ("example.com", "255.255.255.255", "0.0.0.0", "127.0.0.1", "224.0.0.1"):
        result = jitter_measurement.measure_jitter(
            address=address, platform="windows", runner=lambda *args, **kwargs: calls.append(args),
            which=lambda name: "ping.exe",
        )
        assert result["state"] == "unknown"
    assert calls == []


def test_agent_publishes_correlated_icmp_jitter_result(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(jitter_measurement, "measure_jitter", lambda **kwargs: {
        "address": "192.0.2.8", "state": "available", "source": jitter_measurement.SOURCE,
        "detail": None, "attempted": 10, "received": 10, "lost": 0,
        "unknown": 0, "pairs": 9, "jitter_ms": 1.2, "samples": [],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_jitter_measurement({"id": "jitter-request-1", "address": "192.0.2.8"})

    topic, result = sent[0]
    assert topic == "network_jitter"
    assert result["schema"] == "xider.network-jitter.v1"
    assert result["feature_id"] == "D04-F03"
    assert result["request_id"] == "jitter-request-1"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["data"]["jitter_ms"] == 1.2
