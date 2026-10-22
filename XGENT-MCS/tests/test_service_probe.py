import errno
import socket
import ssl

import service_probe


class _Socket:
    def __init__(self, failure=None):
        self.failure, self.timeout, self.address, self.closed = failure, None, None, False

    def settimeout(self, value): self.timeout = value
    def connect(self, address):
        self.address = address
        if self.failure: raise self.failure
    def close(self): self.closed = True


class _Context:
    def __init__(self, *, failure=None): self.failure, self.hostname, self.socket = failure, None, None
    def wrap_socket(self, raw, *, server_hostname):
        self.hostname, self.socket = server_hostname, raw
        if self.failure: raise self.failure
        return raw


def test_tcp_probe_is_single_bounded_connection_and_never_scans():
    raw, calls = _Socket(), []
    result = service_probe.probe_service("example.com", 8443, "tcp",
        connector=lambda address, timeout: calls.append((address, timeout)) or raw)
    assert result == {"state": "reachable", "detail": None, "source": service_probe.SOURCE}
    assert calls == [(("example.com", 8443), 2.5)] and raw.closed


def test_refused_and_timeout_are_not_conflated():
    def fail_with(error): raise error
    refused = service_probe.probe_service("example.com", 443, "tcp",
        connector=lambda *_args, **_kwargs: fail_with(ConnectionRefusedError(errno.ECONNREFUSED, "refused")))
    timed = service_probe.probe_service("example.com", 443, "tcp",
        connector=lambda *_args, **_kwargs: fail_with(socket.timeout()))
    assert refused["state"] == "unreachable"
    assert timed["state"] == "unknown" and timed["detail"] == "probe_timeout"


def test_tls_probe_verifies_hostname_and_distinguishes_untrusted_from_failure():
    raw, context = _Socket(), _Context()
    reachable = service_probe.probe_service("example.com", 443, "tls", connector=lambda *_args, **_kwargs: raw,
        context_factory=lambda: context)
    assert reachable["state"] == "reachable" and context.hostname == "example.com" and raw.closed
    raw2 = _Socket()
    untrusted = service_probe.probe_service("example.com", 443, "tls", connector=lambda *_args, **_kwargs: raw2,
        context_factory=lambda: _Context(failure=ssl.SSLCertVerificationError("do not leak")))
    assert untrusted["state"] == "reachable_untrusted"
    assert untrusted["detail"] == "tls_certificate_untrusted" and raw2.closed


def test_invalid_protocol_and_endpoint_are_rejected_without_connection():
    calls = []
    factory = lambda *_args, **_kwargs: calls.append(True) or _Socket()
    assert service_probe.probe_service("https://example.com", 443, "tcp", connector=factory)["state"] == "invalid_request"
    assert service_probe.probe_service("example.com", 443, "udp", connector=factory)["state"] == "invalid_request"
    assert not calls


def test_agent_publishes_correlated_service_status(monkeypatch):
    import xgent_mcs as agent
    monkeypatch.setattr(service_probe, "probe_service", lambda host, port, protocol: {
        "state": "reachable", "detail": None, "source": service_probe.SOURCE,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_service_probe({"id": "service-1", "host": "example.com", "port": 443, "protocol": "tls"})
    topic, result = sent[0]
    assert topic == "service_probe"
    assert result["feature_id"] == "D04-F14" and result["request_id"] == "service-1"
    assert result["host"] == "example.com" and result["protocol"] == "tls"
    assert result["state"] == "reachable" and result["device_id"] == agent.DEVICE_ID
