import subprocess

import packet_loss_history


def test_packet_loss_counts_only_echo_replies_and_explicit_timeouts():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) in {2, 7}:
            return subprocess.CompletedProcess(args, 1, "Request timed out.", "")
        return subprocess.CompletedProcess(args, 0, "Reply from 192.0.2.8: bytes=32", "")

    result = packet_loss_history.measure_packet_loss(
        address="192.0.2.8", platform="windows", runner=runner,
        which=lambda name: f"C:/Windows/{name}",
    )

    assert result["state"] == "complete"
    assert result["sent"] == 10 and result["received"] == 8 and result["lost"] == 2
    assert result["loss_percent"] == 20.0
    assert result["samples"][1]["state"] == "lost"
    assert len(calls) == 10
    assert all(call[1]["timeout"] <= 2.5 for call in calls)
    assert all("192.0.2.8" in call[0] and "-n" in call[0] and "1" in call[0] for call in calls)
    assert all(call[1].get("shell") is not True for call in calls)


def test_packet_loss_keeps_unrecognized_probe_failures_unknown():
    def runner(args, **kwargs):
        if kwargs["timeout"] == 2.5:
            return subprocess.CompletedProcess(args, 1, "General failure.", "")

    result = packet_loss_history.measure_packet_loss(
        address="2001:db8::1", platform="macos", runner=runner,
        which=lambda name: f"/sbin/{name}",
    )

    assert result["state"] == "unknown"
    assert result["sent"] == 10 and result["received"] == result["lost"] == 0
    assert result["loss_percent"] is None
    assert all(sample["state"] == "unknown" for sample in result["samples"])


def test_packet_loss_rejects_non_literal_or_unsafe_destinations_before_execution():
    calls = []
    for destination in ("example.com", "127.0.0.1", "224.0.0.1", "192.0.2.1;whoami", "0.0.0.0", "255.255.255.255"):
        result = packet_loss_history.measure_packet_loss(
            address=destination, platform="windows", runner=lambda *args, **kwargs: calls.append(args),
            which=lambda name: f"{name}.exe",
        )
        assert result["state"] == "unknown"
        assert result["loss_percent"] is None
    assert calls == []


def test_agent_publishes_correlated_packet_loss_history(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(packet_loss_history, "measure_packet_loss", lambda **kwargs: {
        "address": "192.0.2.8", "state": "partial", "source": packet_loss_history.SOURCE,
        "detail": None, "sent": 10, "observed": 9, "received": 8, "lost": 1,
        "loss_percent": 11.1, "samples": [],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_packet_loss_history({"id": "loss-request-1", "address": "192.0.2.8"})

    topic, result = sent[0]
    assert topic == "packet_loss_history"
    assert result["schema"] == "xider.packet-loss-history.v1"
    assert result["feature_id"] == "D04-F02"
    assert result["request_id"] == "loss-request-1"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["data"]["loss_percent"] == 11.1
