import xgent_wds as agent
import wifi_connection_history


def test_windows_history_extracts_only_connection_events_and_timestamps():
    raw = '''<Events>
      <Event><System><Provider Name="Microsoft-Windows-WLAN-AutoConfig"/><EventID>8001</EventID><TimeCreated SystemTime="2026-10-20T08:00:00.000Z"/></System><EventData><Data Name="SSID">private-net</Data></EventData></Event>
      <Event><System><Provider Name="Microsoft-Windows-WLAN-AutoConfig"/><EventID>8003</EventID><TimeCreated SystemTime="2026-10-20T09:00:00.000Z"/></System><EventData><Data Name="SSID">private-net</Data></EventData></Event>
      <Event><System><Provider Name="Microsoft-Windows-WLAN-AutoConfig"/><EventID>8002</EventID><TimeCreated SystemTime="2026-10-20T09:30:00.000Z"/></System></Event>
    </Events>'''
    assert wifi_connection_history.parse_windows_events(raw) == {
        "state": "available", "source": "windows_wlan_autoconfig_events",
        "events": [
            {"at": "2026-10-20T08:00:00Z", "kind": "connected"},
            {"at": "2026-10-20T09:00:00Z", "kind": "disconnected"},
        ], "detail": None,
    }
    assert "private-net" not in repr(wifi_connection_history.parse_windows_events(raw))


def test_windows_history_treats_malformed_or_missing_events_as_unknown_or_unsupported():
    assert wifi_connection_history.parse_windows_events("not xml")["state"] == "unknown"
    assert wifi_connection_history.parse_windows_events("<Events />")["state"] == "unsupported"
    single = '<Event><System><EventID>8001</EventID><TimeCreated SystemTime="2026-10-20T09:00:00Z"/></System></Event>'
    assert wifi_connection_history.parse_windows_events(single)["events"] == [
        {"at": "2026-10-20T09:00:00Z", "kind": "connected"},
    ]


def test_agent_publishes_correlated_wifi_connection_history_without_event_payloads(monkeypatch):
    monkeypatch.setattr(wifi_connection_history, "collect_wifi_connection_history", lambda: {
        "state": "available", "source": wifi_connection_history.WINDOWS_SOURCE,
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
