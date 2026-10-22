import proxy_status
import xgent_mcs as agent


def test_macos_proxy_summary_returns_only_enabled_protocol_names():
    output = """<dictionary> {
  HTTPEnable : 1
  HTTPProxy : private.proxy.local
  HTTPPort : 8080
  HTTPSEnable : 0
  HTTPSProxy : unused.proxy.local
  SOCKSEnable : 1
  SOCKSProxy : user:secret@socks.local
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : https://user:secret@pac.local/proxy.pac
}
"""

    result = proxy_status.parse_scutil_proxy(output)

    assert result == {
        "state": "available",
        "sources": ["http", "socks", "pac"],
    }
    assert "secret" not in repr(result)
    assert "proxy.local" not in repr(result)
    assert "pac.local" not in repr(result)


def test_macos_proxy_summary_handles_direct_connection():
    output = """<dictionary> {
  HTTPEnable : 0
  HTTPSEnable : 0
  SOCKSEnable : 0
}
"""
    assert proxy_status.parse_scutil_proxy(output) == {
        "state": "available", "sources": [],
    }


def test_agent_publishes_correlated_redacted_proxy_status(monkeypatch):
    monkeypatch.setattr(proxy_status, "collect_proxy_status", lambda: {
        "state": "available", "sources": ["http", "socks", "pac"],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_proxy_status({"id": "proxy-1"})

    topic, result = sent[0]
    assert topic == "proxy_status"
    assert result["feature_id"] == "D04-F23" and result["request_id"] == "proxy-1"
    assert result["sources"] == ["http", "socks", "pac"]
    assert "server" not in result and "endpoint" not in result and "credentials" not in result
