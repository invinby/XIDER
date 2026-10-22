import struct

import pytest

import dns_comparison


def test_compare_resolvers_normalizes_and_compares_a_and_aaaa_answers():
    calls = []

    def query(server, hostname, qtype, timeout_seconds):
        calls.append((server, hostname, qtype, timeout_seconds))
        answer = "203.0.113.8" if qtype == "A" else "2001:db8::8"
        return {"rcode": "NOERROR", "addresses": [answer, answer]}

    result = dns_comparison.compare_resolvers(
        "BÜCHER.example.", ["1.1.1.1", "2001:4860:4860::8888"], query_fn=query,
    )
    assert result == {
        "hostname": "xn--bcher-kva.example",
        "comparison": "same",
        "resolvers": [
            {"address": "1.1.1.1", "status": "complete", "records": {
                "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]},
                "AAAA": {"rcode": "NOERROR", "addresses": ["2001:db8::8"]},
            }},
            {"address": "2001:4860:4860::8888", "status": "complete", "records": {
                "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]},
                "AAAA": {"rcode": "NOERROR", "addresses": ["2001:db8::8"]},
            }},
        ],
    }
    assert len(calls) == 4
    assert {row[0] for row in calls} == {"1.1.1.1", "2001:4860:4860::8888"}


@pytest.mark.parametrize("servers", [
    ["1.1.1.1"], ["1.1.1.1", "1.1.1.1"], ["resolver.example", "8.8.8.8"],
    ["1.1.1.1", "bad"], ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
])
def test_compare_resolvers_rejects_invalid_server_selection(servers):
    with pytest.raises(ValueError):
        dns_comparison.compare_resolvers("example.com", servers)


def test_resolver_timeout_returns_unknown_without_claiming_equality():
    calls = []

    def query(server, hostname, qtype, timeout_seconds):
        calls.append(server)
        if server == "8.8.8.8":
            raise TimeoutError("timeout")
        return {"rcode": "NOERROR", "addresses": ["203.0.113.8" if qtype == "A" else "2001:db8::8"]}

    result = dns_comparison.compare_resolvers("example.com", ["1.1.1.1", "8.8.8.8"], query_fn=query)
    assert result["comparison"] == "unknown"
    assert result["resolvers"][1]["status"] == "unknown"
    assert len(calls) == 3


def test_dns_wire_response_is_bound_to_transaction_question_and_resolver_answer():
    question = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    header = struct.pack("!HHHHHH", 77, 0x8180, 1, 1, 0, 0)
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + bytes([203, 0, 113, 8])
    packet = header + question + answer
    assert dns_comparison.parse_dns_response(packet, 77, "example.com", "A") == {
        "rcode": "NOERROR", "addresses": ["203.0.113.8"],
    }
    with pytest.raises(ValueError):
        dns_comparison.parse_dns_response(packet, 78, "example.com", "A")


def test_dns_query_sends_only_to_selected_resolver_and_checks_source_port():
    sent = []
    question = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + bytes([203, 0, 113, 8])
    packet = struct.pack("!HHHHHH", 77, 0x8180, 1, 1, 0, 0) + question + answer

    class FakeSocket:
        def __init__(self, *_args): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def settimeout(self, value): assert 0 < value <= 3
        def sendto(self, payload, endpoint): sent.append((payload, endpoint))
        def recvfrom(self, _size): return packet, ("1.1.1.1", 53)

    result = dns_comparison.query_dns("1.1.1.1", "example.com", "A", socket_factory=FakeSocket, transaction_id=77)
    assert result == {"rcode": "NOERROR", "addresses": ["203.0.113.8"]}
    assert sent[0][1] == ("1.1.1.1", 53)
    assert sent[0][0][12:] == question  # QNAME is encoded as DNS labels, not passed through a shell.


def test_malformed_remote_dns_response_does_not_claim_comparison_result():
    def query(*_args):
        raise ValueError("bad response")
    result = dns_comparison.compare_resolvers("example.com", ["1.1.1.1", "8.8.8.8"], query_fn=query)
    assert result["comparison"] == "unknown"
    assert all(row["status"] == "unknown" for row in result["resolvers"])


def test_agent_publishes_correlated_dns_comparison(monkeypatch):
    import xgent_wds as agent
    monkeypatch.setattr(dns_comparison, "compare_resolvers", lambda host, servers: {
        "hostname": "example.com", "comparison": "same", "resolvers": [
            {"address": "1.1.1.1", "status": "complete", "records": {}},
            {"address": "8.8.8.8", "status": "complete", "records": {}},
        ],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_dns_comparison({"id": "dns-compare-1", "hostname": "example.com", "resolvers": ["1.1.1.1", "8.8.8.8"]})
    topic, result = sent[0]
    assert topic == "dns_comparison"
    assert result["schema"] == "xider.dns-comparison.v1"
    assert result["feature_id"] == "D04-F07"
    assert result["request_id"] == "dns-compare-1"
    assert result["device_id"] == agent.DEVICE_ID
    assert result["data"]["comparison"] == "same"
