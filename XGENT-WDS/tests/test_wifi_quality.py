import xgent_wds as agent
import wifi_quality
import wifi_channel


def test_windows_signal_parser_returns_only_normalized_quality_not_ssid():
    sample = wifi_quality.parse_windows_signal("SSID : private-network\nSignal : 83%\n")
    assert sample == {"state": "available", "metric": "signal_percent", "value": 83,
                      "source": "windows_netsh_signal_percent", "detail": None}
    assert "private-network" not in repr(sample)


def test_windows_signal_parser_does_not_invent_a_sample():
    assert wifi_quality.parse_windows_signal("State : disconnected\n") == {
        "state": "unsupported", "metric": None, "value": None,
        "source": "windows_netsh_signal_percent", "detail": "signal_unavailable",
    }
    assert wifi_quality.parse_windows_signal("Signal : 140%") ["state"] == "unknown"


def test_windows_collector_uses_read_only_netsh_and_returns_only_signal():
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return "SSID : private-network\nSignal : 61%\n"
    result = wifi_quality.collect_wifi_quality(check_output=runner)
    assert result["value"] == 61 and result["metric"] == "signal_percent"
    assert calls == [(["netsh", "wlan", "show", "interfaces"],
                      {"text": True, "stderr": wifi_quality.subprocess.DEVNULL, "timeout": 5})]
    assert "private-network" not in repr(result)


def test_agent_publishes_correlated_wifi_quality_sample_without_network_identity(monkeypatch):
    monkeypatch.setattr(wifi_quality, "collect_wifi_quality", lambda: {
        "state": "available", "metric": "signal_percent", "value": 83,
        "source": wifi_quality.WINDOWS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_wifi_quality_history({"id": "wifi-1"})
    topic, result = sent[0]
    assert topic == "wifi_quality_history"
    assert result["feature_id"] == "D04-F15" and result["request_id"] == "wifi-1"
    assert result["metric"] == "signal_percent" and result["value"] == 83
    assert "ssid" not in result and "password" not in result


def test_windows_channel_parser_keeps_only_channel_and_width():
    parsed = wifi_channel.parse_windows_channel(
        "SSID : private-network\nChannel : 149\nChannel width : 80 MHz\n"
    )
    assert parsed == {"state": "available", "channel": 149, "width_mhz": 80,
                      "width_state": "available", "source": wifi_channel.WINDOWS_SOURCE, "detail": None}
    assert "private-network" not in repr(parsed)


def test_windows_channel_parser_does_not_guess_missing_width_or_invalid_channel():
    parsed = wifi_channel.parse_windows_channel("Channel : 36\n")
    assert parsed["channel"] == 36 and parsed["width_mhz"] is None
    assert parsed["width_state"] == "unsupported"
    assert wifi_channel.parse_windows_channel("Channel : 999\n")["state"] == "unknown"


def test_agent_publishes_correlated_wifi_channel_summary(monkeypatch):
    monkeypatch.setattr(wifi_channel, "collect_wifi_channel", lambda: {
        "state": "available", "channel": 149, "width_mhz": 80,
        "source": wifi_channel.WINDOWS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_wifi_channel_summary({"id": "channel-1"})
    topic, result = sent[0]
    assert topic == "wifi_channel_summary"
    assert result["feature_id"] == "D04-F16" and result["request_id"] == "channel-1"
    assert result["channel"] == 149 and result["width_mhz"] == 80
    assert "ssid" not in result and "password" not in result
