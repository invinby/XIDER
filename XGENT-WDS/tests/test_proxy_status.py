import proxy_status
import xgent_wds as agent


def test_proxy_status_exposes_only_enabled_proxy_categories():
    result = proxy_status.summarize_proxy_sources(
        internet_settings={"ProxyEnable": 1, "ProxyServer": "user:secret@proxy.local:8080",
                          "AutoConfigURL": "https://proxy.local/config.pac"},
        winhttp_output="Current WinHTTP proxy settings: Proxy Server(s) : http=corp.local:3128",
        environment={"HTTPS_PROXY": "http://user:secret@proxy.local:8080"},
    )

    assert result == {
        "state": "available",
        "sources": ["current_user_manual", "current_user_pac", "winhttp", "environment"],
    }
    assert "secret" not in repr(result)
    assert "proxy.local" not in repr(result)


def test_proxy_status_reports_no_proxy_without_inventing_a_value():
    assert proxy_status.summarize_proxy_sources(
        internet_settings={"ProxyEnable": 0}, winhttp_output="Direct access (no proxy server).",
        environment={},
    ) == {"state": "available", "sources": []}


def test_proxy_status_does_not_claim_no_proxy_when_winhttp_could_not_be_read():
    assert proxy_status.summarize_proxy_sources(
        internet_settings={}, winhttp_output="", environment={},
    ) == {"state": "unknown", "sources": []}


def test_agent_publishes_correlated_redacted_proxy_status(monkeypatch):
    monkeypatch.setattr(proxy_status, "collect_proxy_status", lambda: {
        "state": "available", "sources": ["current_user_manual", "winhttp"],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_proxy_status({"id": "proxy-1"})

    topic, result = sent[0]
    assert topic == "proxy_status"
    assert result["feature_id"] == "D04-F23" and result["request_id"] == "proxy-1"
    assert result["sources"] == ["current_user_manual", "winhttp"]
    assert "server" not in result and "endpoint" not in result and "credentials" not in result
