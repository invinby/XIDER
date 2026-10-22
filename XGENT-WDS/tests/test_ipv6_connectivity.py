import errno
import socket

import ipv6_connectivity


def _dns_ok_response(query):
    header = query[:2] + bytes.fromhex("81800001000000000000")
    return header + query[12:]


class _Socket:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.timeout = None
        self.connected = None
        self.sent = b""
        self.incoming = bytearray()
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        self.connected = address
        if self.failure:
            raise self.failure

    def sendall(self, data):
        self.sent += data
        query = data[2:]
        response = _dns_ok_response(query)
        self.incoming.extend(len(response).to_bytes(2, "big") + response)

    def recv(self, size):
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def close(self):
        self.closed = True


class _TLSContext:
    def __init__(self):
        self.hostname = None

    def wrap_socket(self, raw, *, server_hostname):
        self.hostname = server_hostname
        return raw


def test_dns_query_is_bounded_and_uses_a_fixed_public_name():
    assert ipv6_connectivity.build_dns_query(0x1234).hex() == (
        "1234010000010000000000000e636c6f7564666c6172652d646e7303636f6d0000010001"
    )


def test_valid_dns_reply_confirms_tls_ipv6_reachability():
    raw = _Socket()
    context = _TLSContext()
    result = ipv6_connectivity.probe_ipv6(
        socket_factory=lambda *_args: raw,
        context_factory=lambda: context,
        identifier_factory=lambda _bits: 0x1234,
    )
    assert result == {"state": "reachable", "source": "cloudflare_ipv6_dot", "detail": None}
    assert raw.connected == ("2606:4700:4700::1111", 853, 0, 0)
    assert raw.timeout <= 3.0 and context.hostname == "one.one.one.one"
    assert raw.closed


def test_ipv6_reply_with_a_mismatched_id_is_not_reachability_evidence():
    response = bytes.fromhex("9999818000010000000000000e636c6f7564666c6172652d646e7303636f6d0000010001")
    assert not ipv6_connectivity.valid_dns_response(response, 0x1234)


def test_network_unreachable_is_distinct_from_a_timeout():
    no_route = _Socket(failure=OSError(errno.ENETUNREACH, "no route"))
    timeout = _Socket(failure=socket.timeout("timed out"))
    assert ipv6_connectivity.probe_ipv6(socket_factory=lambda *_args: no_route)["state"] == "no_route"
    assert ipv6_connectivity.probe_ipv6(socket_factory=lambda *_args: timeout)["state"] == "unknown"


def test_agent_publishes_correlated_ipv6_reachability(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(ipv6_connectivity, "probe_ipv6", lambda: {
        "state": "reachable", "source": ipv6_connectivity.SOURCE, "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_ipv6_connectivity({"id": "ipv6-1"})
    topic, result = sent[0]
    assert topic == "ipv6_connectivity"
    assert result["feature_id"] == "D04-F12" and result["request_id"] == "ipv6-1"
    assert result["state"] == "reachable" and result["device_id"] == agent.DEVICE_ID
