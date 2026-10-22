import json
from types import SimpleNamespace

import dns_settings
import xgent_wds as agent


def test_windows_parser_returns_only_valid_resolvers_grouped_by_interface():
    rows = dns_settings.parse_windows_dns_settings(json.dumps([
        {"InterfaceIndex": 12, "AddressFamily": 2, "ServerAddresses": ["1.1.1.1", "bad", "1.1.1.1"], "Suffix": "private.example"},
        {"InterfaceIndex": 12, "AddressFamily": 23, "ServerAddresses": ["2001:4860:4860::8888"]},
        {"InterfaceIndex": -1, "AddressFamily": 2, "ServerAddresses": ["8.8.8.8"]},
    ]))
    assert rows == [
        {"interface_index": 12, "addresses": ["1.1.1.1", "2001:4860:4860::8888"]},
    ]
    assert "private.example" not in repr(rows)


def test_macos_parser_extracts_nameservers_but_not_search_domains_or_service_names():
    output = """resolver #1
  search domain[0] : private.example
  nameserver[0] : 192.0.2.53
  nameserver[1] : 2001:db8::53
  if_index : 14 (en0)
resolver #2
  nameserver[0] : 192.0.2.53
  if_index : 14 (en0)
"""
    rows = dns_settings.parse_macos_dns_settings(output)
    assert rows == [{"interface_index": 14, "addresses": ["192.0.2.53", "2001:db8::53"]}]
    assert "private.example" not in repr(rows) and "en0" not in repr(rows)


def test_agent_publishes_correlated_private_dns_snapshot(monkeypatch):
    monkeypatch.setattr(dns_settings, "read_dns_settings", lambda **_kwargs: {
        "source": "windows_get_dns_client_server_address",
        "resolvers": [{"interface_index": 12, "addresses": ["1.1.1.1"]}],
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
    assert result["data"]["resolvers"] == [{"interface_index": 12, "addresses": ["1.1.1.1"]}]
    assert "private.example" not in repr(result)
