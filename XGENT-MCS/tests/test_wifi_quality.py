import xgent_mcs as agent
import wifi_quality
import wifi_channel


def test_macos_wifi_parser_returns_only_rssi_not_ssid_or_credentials():
    sample = wifi_quality.parse_macos_summary("SSID : private-network\nagrCtlRSSI: -47\nagrCtlNoise: -92\n")
    assert sample == {"state": "available", "metric": "rssi_dbm", "value": -47,
                      "source": "macos_ipconfig_agrctlrssi", "detail": None}
    assert "private-network" not in repr(sample)


def test_macos_wifi_interface_selection_requires_wifi_hardware_port():
    assert wifi_quality.wifi_device_from_hardware_ports(
        "Hardware Port: Wi-Fi\nDevice: en2\nEthernet Address: aa:bb:cc:dd:ee:ff\n"
        "Hardware Port: Bluetooth PAN\nDevice: en0\n"
    ) == "en2"
    assert wifi_quality.wifi_device_from_hardware_ports("Hardware Port: Ethernet\nDevice: en0\n") is None


def test_macos_collector_queries_only_detected_wifi_interface_and_returns_rssi():
    calls = []
    outputs = iter(("Hardware Port: Ethernet\nDevice: en0\n"
                    "Hardware Port: Wi-Fi\nDevice: en2\n",
                    "SSID : private-network\nagrCtlRSSI: -62\n"))
    def runner(command, **kwargs):
        calls.append(command)
        return next(outputs)
    result = wifi_quality.collect_wifi_quality(check_output=runner)
    assert result["metric"] == "rssi_dbm" and result["value"] == -62
    assert calls == [["networksetup", "-listallhardwareports"], ["ipconfig", "getsummary", "en2"]]
    assert "private-network" not in repr(result)


def test_agent_publishes_correlated_wifi_quality_sample_without_network_identity(monkeypatch):
    monkeypatch.setattr(wifi_quality, "collect_wifi_quality", lambda: {
        "state": "available", "metric": "rssi_dbm", "value": -47,
        "source": wifi_quality.MACOS_SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_wifi_quality_history({"id": "wifi-1"})
    topic, result = sent[0]
    assert topic == "wifi_quality_history"
    assert result["feature_id"] == "D04-F15" and result["request_id"] == "wifi-1"
    assert result["metric"] == "rssi_dbm" and result["value"] == -47
    assert "ssid" not in result and "password" not in result


def test_macos_channel_parser_keeps_only_channel_and_width():
    parsed = wifi_channel.parse_macos_channel(
        "SSID: private-network\nChannel: 149 (5GHz, 80MHz)\n"
    )
    assert parsed == {"state": "available", "channel": 149, "width_mhz": 80,
                      "width_state": "available", "source": wifi_channel.MACOS_SOURCE, "detail": None}
    assert "private-network" not in repr(parsed)


def test_macos_channel_parser_marks_missing_and_invalid_data_explicitly():
    parsed = wifi_channel.parse_macos_channel("Channel: 36\n")
    assert parsed["channel"] == 36 and parsed["width_mhz"] is None
    assert parsed["width_state"] == "unsupported"
    assert wifi_channel.parse_macos_channel("Channel: 999") ["state"] == "unknown"


def test_macos_collector_uses_read_only_wdutil_and_returns_no_network_identity():
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return "SSID: private-network\nChannel: 36 (2GHz, 40MHz)\n"
    parsed = wifi_channel.collect_wifi_channel(check_output=runner)
    assert parsed["channel"] == 36 and parsed["width_mhz"] == 40
    assert calls == [(["wdutil", "info"], {
        "text": True, "stderr": wifi_channel.subprocess.DEVNULL, "timeout": 5,
    })]
    assert "private-network" not in repr(parsed)


def test_agent_publishes_correlated_wifi_channel_summary(monkeypatch):
    monkeypatch.setattr(wifi_channel, "collect_wifi_channel", lambda: {
        "state": "available", "channel": 149, "width_mhz": 80,
        "source": wifi_channel.MACOS_SOURCE, "detail": None,
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
