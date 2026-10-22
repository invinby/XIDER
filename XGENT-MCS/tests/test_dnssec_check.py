import struct

import dnssec_check
import xgent_mcs as agent


def response(transaction_id, hostname="example.com", *, ad=False, rcode=0):
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in hostname.split(".")) + b"\0"
    flags = 0x8000 | 0x0080 | rcode | (0x0020 if ad else 0)
    return struct.pack("!HHHHHH", transaction_id, flags, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)


def test_dnssec_query_sets_edns_do_bit_and_parse_marks_only_ad_as_validated():
    packet = dnssec_check.build_query("example.com", 513)
    assert struct.unpack("!HHHHHH", packet[:12]) == (513, 0x0100, 1, 0, 0, 1)
    assert packet[-11:] == b"\0" + struct.pack("!HHIH", 41, 1232, 0x8000, 0)
    assert dnssec_check.parse_response(response(513, ad=True), 513, "example.com")["assessment"] == "validated_by_resolver"
    assert dnssec_check.parse_response(response(513), 513, "example.com")["assessment"] == "not_confirmed"


def test_dnssec_rejects_mismatched_or_truncated_responses():
    for packet in (response(1), response(513, "other.example"), response(513)[:15]):
        try:
            dnssec_check.parse_response(packet, 513, "example.com")
        except ValueError:
            pass
        else:
            raise AssertionError("unbound DNS response was accepted")


def test_dnssec_request_is_one_literal_public_resolver_and_valid_domain_only():
    assert dnssec_check.check_domain("example.com", "1.1.1.1", query_fn=lambda *_a, **_k: {
        "assessment": "validated_by_resolver", "ad_bit": True, "rcode": "NOERROR",
    }, settings_fn=lambda: {"state": "supported", "resolvers": [{"addresses": ["1.1.1.1"]}]})["assessment"] == "validated_by_resolver"
    for hostname, resolver in (("", "1.1.1.1"), ("example.com", "resolver.example"),
                               ("example.com", "127.0.0.1"), ("example.com", "1.1.1.1:53")):
        try:
            dnssec_check.check_domain(hostname, resolver)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid DNSSEC request was accepted")


def test_agent_publishes_correlated_dnssec_assessment(monkeypatch):
    monkeypatch.setattr(dnssec_check, "check_domain", lambda *_a, **_k: {
        "hostname": "example.com", "resolver": "1.1.1.1", "assessment": "not_confirmed",
        "ad_bit": False, "rcode": "NOERROR",
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_dnssec_check({"id": "dnssec-1", "hostname": "example.com", "resolver": "1.1.1.1"})
    topic, result = sent[0]
    assert topic == "dnssec_check"
    assert result["feature_id"] == "D04-F26" and result["request_id"] == "dnssec-1"
    assert result["state"] == "not_confirmed" and result["ad_bit"] is False
