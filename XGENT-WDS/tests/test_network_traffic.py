from datetime import datetime, timezone
from types import SimpleNamespace

import network_traffic
import xgent_wds as agent


def test_windows_adapter_ids_are_stable_and_labels_are_bounded_and_clean():
    rows = network_traffic.interface_catalog([
        "Wi-Fi",
        "Ethernet\nsecret" + "x" * 100,
    ])

    assert rows == [
        {"interface_id": "f7f269ac85524af41f25", "label": "Ethernet secret" + "x" * 49},
        {"interface_id": "bc60aa9b07ccca8059ab", "label": "Wi-Fi"},
    ]
    assert len(rows[1]["label"]) <= 64


def test_windows_selected_adapter_sample_returns_only_byte_counters():
    counters = {
        "Wi-Fi": SimpleNamespace(bytes_sent=1200, bytes_recv=3400, packets_sent=99),
        "Ethernet": SimpleNamespace(bytes_sent=9000, bytes_recv=8000, packets_sent=111),
    }
    interface_id = network_traffic.interface_catalog(["Wi-Fi"])[0]["interface_id"]

    result = network_traffic.sample_interface(
        interface_id,
        counters,
        sampled_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "state": "available",
        "source": "windows_psutil_interface_bytes",
        "interface_id": interface_id,
        "label": "Wi-Fi",
        "sent_bytes": 1200,
        "received_bytes": 3400,
        "sampled_at": "2026-10-20T12:00:00Z",
        "detail": None,
    }
    assert "packets_sent" not in result


def test_windows_unknown_interface_and_invalid_counter_fail_closed():
    assert network_traffic.sample_interface("x" * 20, {"Wi-Fi": SimpleNamespace(bytes_sent=1, bytes_recv=2)})["state"] == "unknown"
    interface_id = network_traffic.interface_catalog(["Wi-Fi"])[0]["interface_id"]
    assert network_traffic.sample_interface(interface_id, {"Wi-Fi": SimpleNamespace(bytes_sent=-1, bytes_recv=2)})["state"] == "unknown"


def test_windows_agent_publishes_correlated_selected_adapter_traffic(monkeypatch):
    assert "network_traffic" in agent.SUPPORTED_COMMANDS
    monkeypatch.setattr(network_traffic, "collect_interface_traffic", lambda interface_id: {
        "state": "available", "source": network_traffic.SOURCE,
        "interface_id": interface_id, "label": "Wi-Fi", "sent_bytes": 1200,
        "received_bytes": 3400, "sampled_at": "2026-10-20T12:00:00Z", "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))
    client._do_network_traffic({"id": "traffic-1", "interface_id": "bc60aa9b07ccca8059ab"})
    assert published[0][0] == "network_traffic"
    assert published[0][1]["feature_id"] == "D04-F18"
    assert published[0][1]["request_id"] == "traffic-1"
    assert published[0][1]["sent_bytes"] == 1200


def test_windows_agent_acknowledges_owner_selected_threshold_schedule(monkeypatch, tmp_path):
    assert "network_traffic_interval" in agent.SUPPORTED_COMMANDS
    monkeypatch.setattr(agent, "CONFIG_DIR", tmp_path)
    client = object.__new__(agent.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))
    client._network_traffic_schedule_wake = SimpleNamespace(set=lambda: None)
    client._do_network_traffic_interval({
        "id": "alert-1", "interval_seconds": 900, "schedule_id": "a" * 32,
        "interface_id": "b" * 20, "threshold_bytes": 100_000_000,
    })
    assert published[0][0] == "network_traffic_alert"
    result = published[0][1]
    next_sample_at = result.pop("next_sample_at")
    assert result == {
        "type": "network_traffic_alert_status", "schema": "xider.network-traffic-alert.v1",
        "feature_id": "D04-F19", "device_id": agent.DEVICE_ID, "request_id": "alert-1",
        "state": "supported", "interval_seconds": 900, "schedule_id": "a" * 32,
        "interface_id": "b" * 20, "threshold_bytes": 100_000_000,
    }
    assert type(next_sample_at) is int and next_sample_at > 0


def test_windows_background_sampler_only_reads_selected_adapter_and_reschedules(monkeypatch, tmp_path):
    import time
    import network_traffic_schedule as schedule

    monkeypatch.setattr(agent, "CONFIG_DIR", tmp_path)
    schedule.save_schedule(tmp_path / "network_traffic_schedule.json", 900, "a" * 32,
                           "b" * 20, 100_000_000, now=int(time.time()) - 1800)
    client = object.__new__(agent.XgentClient)
    class RunOnce:
        calls = 0
        def is_set(self):
            self.calls += 1
            return self.calls == 1
    class Wake:
        def wait(self, _timeout): return False
        def clear(self): pass
    client._running = RunOnce()
    client._network_traffic_schedule_wake = Wake()
    samples, published = [], []
    def collect(interface_id):
        samples.append(interface_id)
        return {"state": "available", "sent_bytes": 100, "received_bytes": 200,
                "sampled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "detail": None}
    monkeypatch.setattr(agent.network_traffic, "collect_interface_traffic", collect)
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))
    client._network_traffic_schedule_loop()
    assert samples == ["b" * 20]
    assert published[0][0] == "network_traffic_alert"
    assert published[0][1]["type"] == "network_traffic_alert_sample"
    assert published[0][1]["schedule_id"] == "a" * 32
    assert published[0][1]["sent_bytes"] == 100


def test_windows_frozen_agent_includes_the_network_traffic_helper():
    from pathlib import Path

    root = Path(agent.__file__).resolve().parent.parent
    spec = (root / "XGENT-WDS" / "XGENT-WDS.spec").read_text(encoding="utf-8")
    source_bundle = Path(agent.__file__).read_text(encoding="utf-8")
    assert "'network_traffic'" in spec
    assert "'network_traffic_schedule'" in spec
    assert '"network_traffic.py"' in source_bundle
    assert '"network_traffic_schedule.py"' in source_bundle
