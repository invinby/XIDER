import xgent_mcs as agent
import wifi_connection_history


def test_macos_history_extracts_only_connection_events_and_timestamps():
    raw = '''[
      {"timestamp":"2026-10-20 08:00:00.000 +0300","process":"airportd","eventMessage":"Joined network private-net"},
      {"timestamp":"2026-10-20 09:00:00.000 +0300","process":"airportd","eventMessage":"Disassociated from network private-net"},
      {"timestamp":"2026-10-20 09:30:00.000 +0300","process":"airportd","eventMessage":"noise only"}
    ]'''
    assert wifi_connection_history.parse_macos_events(raw) == {
        "state": "available", "source": "macos_unified_airportd_events",
        "events": [
            {"at": "2026-10-20T08:00:00+03:00", "kind": "connected"},
            {"at": "2026-10-20T09:00:00+03:00", "kind": "disconnected"},
        ], "detail": None,
    }
    assert "private-net" not in repr(wifi_connection_history.parse_macos_events(raw))


def test_macos_history_rejects_malformed_or_unknown_logs():
    assert wifi_connection_history.parse_macos_events("not json")["state"] == "unknown"
    assert wifi_connection_history.parse_macos_events("[]")["state"] == "unsupported"


def test_agent_publishes_correlated_wifi_connection_history_without_event_payloads(monkeypatch):
    monkeypatch.setattr(wifi_connection_history, "collect_wifi_connection_history", lambda: {
        "state": "available", "source": wifi_connection_history.MACOS_SOURCE,
        "events": [{"at": "2026-10-20T08:00:00Z", "kind": "connected"}], "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_wifi_connection_history({"id": "history-1"})
    topic, payload = sent[0]
    assert topic == "wifi_connection_history"
    assert payload["feature_id"] == "D04-F17" and payload["request_id"] == "history-1"
    assert payload["events"] == [{"at": "2026-10-20T08:00:00Z", "kind": "connected"}]
    assert "ssid" not in payload and "event_payload" not in payload
