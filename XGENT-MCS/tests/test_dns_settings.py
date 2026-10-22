import dns_settings
import xgent_mcs as agent


def test_macos_parser_extracts_only_nameservers_by_interface():
    output = """resolver #1
  search domain[0] : corp.example
  nameserver[0] : 192.0.2.53
  nameserver[1] : 2001:db8::53
  if_index : 14 (en0)
resolver #2
  nameserver[0] : 192.0.2.53
  if_index : 14 (en0)
"""
    rows = dns_settings.parse_macos_dns_settings(output)
    assert rows == [{"interface_index": 14, "addresses": ["192.0.2.53", "2001:db8::53"]}]
    assert "corp.example" not in repr(rows) and "en0" not in repr(rows)


def test_agent_publishes_correlated_private_dns_snapshot(monkeypatch):
    monkeypatch.setattr(dns_settings, "read_dns_settings", lambda **_kwargs: {
        "source": "macos_scutil_dns",
        "resolvers": [{"interface_index": 14, "addresses": ["192.0.2.53"]}],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_dns_settings({"id": "dns-settings-ticket"})
    topic, result = sent[0]
    assert topic == "dns_settings"
    assert result["type"] == "dns_settings"
    assert result["schema"] == "xider.dns-settings.v1"
    assert result["feature_id"] == "D04-F06"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["request_id"] == "dns-settings-ticket"
    assert result["state"] == "supported"
    assert result["data"]["resolvers"] == [{"interface_index": 14, "addresses": ["192.0.2.53"]}]
    assert "corp.example" not in repr(result)
