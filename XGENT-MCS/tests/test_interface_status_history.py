import interface_status_history as history
import xgent_mcs as agent
from types import SimpleNamespace


def test_history_records_only_verified_state_transitions_and_bounds_rows(tmp_path):
    path = tmp_path / "status-history.json"
    interface_id = "a" * 20
    history.configure_monitor(path, interface_id, interval_seconds=60, now=1000)

    first = history.record_sample(path, interface_id, {"state": "online", "sampled_at": "2026-10-20T10:00:00Z"}, now=1001)
    duplicate = history.record_sample(path, interface_id, {"state": "online", "sampled_at": "2026-10-20T10:01:00Z"}, now=1002)
    unknown = history.record_sample(path, interface_id, {"state": "unknown", "sampled_at": "2026-10-20T10:02:00Z"}, now=1003)
    offline = history.record_sample(path, interface_id, {"state": "offline", "sampled_at": "2026-10-20T10:03:00Z"}, now=1004)

    assert first["event"] == "baseline"
    assert duplicate["event"] is None
    assert unknown["event"] is None
    assert offline["event"] == "offline"
    assert history.read_history(path, interface_id)["events"] == [
        {"at": "2026-10-20T10:00:00Z", "state": "online", "kind": "baseline"},
        {"at": "2026-10-20T10:03:00Z", "state": "offline", "kind": "transition"},
    ]


def test_history_does_not_accept_another_interface_or_bad_timestamp(tmp_path):
    path = tmp_path / "status-history.json"
    selected = "a" * 20
    history.configure_monitor(path, selected, interval_seconds=60, now=1000)

    assert history.record_sample(path, "b" * 20, {"state": "offline", "sampled_at": "2026-10-20T10:00:00Z"}, now=1001)["event"] is None
    assert history.record_sample(path, selected, {"state": "online", "sampled_at": "not-a-time"}, now=1001)["event"] is None
    assert history.read_history(path, selected)["events"] == []


def test_monitor_configuration_is_disabled_by_default_and_can_be_stopped(tmp_path):
    path = tmp_path / "status-history.json"
    assert history.load_monitor(path)["enabled"] is False
    configured = history.configure_monitor(path, "a" * 20, interval_seconds=60, now=1000)
    assert configured["enabled"] is True
    stopped = history.stop_monitor(path)
    assert stopped["enabled"] is False


def test_status_catalog_and_selected_sample_expose_only_opaque_id_and_link_state():
    stats = {"en0": SimpleNamespace(isup=False)}
    catalog = history.collect_catalog(stats)
    interface_id = catalog["interfaces"][0]["interface_id"]
    sample = history.collect_interface_status(interface_id, stats)
    assert set(sample) == {"state", "source", "interface_id", "label", "sampled_at", "detail"}
    assert sample["state"] == "offline"
    assert sample["label"] == "en0"


def test_expired_monitor_does_not_record_more_status(tmp_path):
    path = tmp_path / "status-history.json"
    interface_id = "a" * 20
    history.configure_monitor(path, interface_id, interval_seconds=60, now=1000)
    sample = {"state": "online", "sampled_at": "2026-10-20T10:00:00Z"}
    assert history.record_sample(path, interface_id, sample, now=1000 + 6 * 60 * 60)["event"] is None


def test_agent_start_command_persists_opt_in_and_returns_correlated_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(history, "collect_interface_status", lambda interface_id: {
        "state": "offline", "source": history.SOURCE, "interface_id": interface_id,
        "label": "en0", "sampled_at": "2026-10-20T10:00:00Z", "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    client._interface_status_history_wake = SimpleNamespace(set=lambda: None)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_interface_status_history({"id": "history-start", "mode": "start", "interface_id": "a" * 20,
                                         "interval_seconds": 60})

    topic, result = sent[0]
    assert topic == "interface_status_history"
    assert result["feature_id"] == "D04-F20" and result["request_id"] == "history-start"
    assert result["state"] == "monitoring" and result["events"] == [
        {"at": "2026-10-20T10:00:00Z", "state": "offline", "kind": "baseline"},
    ]
    assert history.load_monitor(tmp_path / "interface_status_history.json")["enabled"] is True
