import socket

import pytest

import dns_diagnostics
import xgent_wds as agent


def test_dns_lookup_normalizes_idna_and_compares_owner_expected_addresses():
    calls = []

    def resolver(hostname, port, family, socktype):
        calls.append((hostname, port, family, socktype))
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
        ]

    result = dns_diagnostics.resolve_dns(
        "bücher.example", ["203.0.113.10", "2001:db8::1"], resolver=resolver,
    )

    assert calls == [("xn--bcher-kva.example", None, socket.AF_UNSPEC, socket.SOCK_STREAM)]
    assert result == {
        "hostname": "xn--bcher-kva.example",
        "addresses": ["203.0.113.10", "2001:db8::1"],
        "truncated": False,
        "expected_match": True,
    }


@pytest.mark.parametrize("hostname", ["", "example.com;id", "bad..example", "-bad.example", "a" * 254])
def test_dns_lookup_rejects_invalid_hostnames_without_resolving(hostname):
    called = False

    def resolver(*_args):
        nonlocal called
        called = True
        return []

    with pytest.raises(ValueError, match="invalid_dns_hostname"):
        dns_diagnostics.resolve_dns(hostname, resolver=resolver)

    assert called is False


def test_dns_lookup_caps_addresses_and_does_not_claim_match_for_truncated_answer():
    rows = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"192.0.2.{n}", 0))
        for n in range(1, 18)
    ]

    result = dns_diagnostics.resolve_dns(
        "example.test", ["192.0.2.17"], resolver=lambda *_args: rows,
    )

    assert len(result["addresses"]) == 16
    assert result["truncated"] is True
    assert result["expected_match"] is None


def test_agent_publishes_correlated_dns_result_and_does_not_connect_to_addresses(monkeypatch):
    monkeypatch.setattr(dns_diagnostics, "resolve_dns", lambda host, expected: {
        "hostname": host, "addresses": ["203.0.113.10"], "truncated": False,
        "expected_match": expected == ["203.0.113.10"],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_dns_resolution({
        "id": "dns-ticket", "hostname": "example.com", "expected_addresses": ["203.0.113.10"],
    })

    topic, result = sent[0]
    assert topic == "dns_resolution"
    assert result["type"] == "dns_resolution"
    assert result["schema"] == "xider.dns-resolution.v1"
    assert result["feature_id"] == "D04-F05"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["request_id"] == "dns-ticket"
    assert result["state"] == "supported"
    assert result["data"]["addresses"] == ["203.0.113.10"]
    assert result["data"]["expected_match"] is True
    assert "outbound_connection" not in result
