import datetime
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import tls_certificate


def _certificate(*, not_before=None, not_after=None):
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before or now - datetime.timedelta(days=1))
            .not_valid_after(not_after or now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


class _TLS:
    def __init__(self, cert):
        self.cert = cert
        self.closed = False

    def getpeercert(self, *, binary_form=False):
        return self.cert if binary_form else {}

    def close(self):
        self.closed = True


class _Context:
    def __init__(self, cert, *, verify, fail_verify=False):
        self.cert, self.verify, self.fail_verify = cert, verify, fail_verify
        self.hostname = None
        self.tls = _TLS(cert)

    def wrap_socket(self, raw, *, server_hostname):
        self.hostname = server_hostname
        if self.fail_verify:
            raise ssl.SSLCertVerificationError("do not expose provider text")
        return self.tls


def test_target_parser_accepts_host_and_bracketed_ipv6_but_not_urls_or_ranges():
    assert tls_certificate.parse_target("example.com") == {"host": "example.com", "port": 443}
    assert tls_certificate.parse_target("example.com:8443") == {"host": "example.com", "port": 8443}
    assert tls_certificate.parse_target("[2001:db8::1]:443") == {"host": "2001:db8::1", "port": 443}
    for value in ("https://example.com", "user@example.com", "example.com/path", "example.com:0", "example.com:65536", "1.2.3.0/24"):
        assert tls_certificate.parse_target(value) is None


def test_verified_certificate_reports_only_validated_leaf_dates_and_fingerprint():
    cert = _certificate()
    raw = object()
    context = _Context(cert, verify=True)
    result = tls_certificate.probe_certificate("example.com", 443,
        connector=lambda address, timeout: raw, context_factory=lambda verify: context)
    assert result["state"] == "valid" and result["detail"] is None
    assert result["source"] == tls_certificate.SOURCE
    now = datetime.datetime.now(datetime.timezone.utc)
    not_after = datetime.datetime.fromisoformat(result["data"]["not_after"].replace("Z", "+00:00"))
    assert now + datetime.timedelta(days=29) <= not_after <= now + datetime.timedelta(days=31)
    assert len(result["data"]["sha256"]) == 64
    assert context.hostname == "example.com" and context.verify is True
    assert context.tls.closed


def test_untrusted_expired_certificate_is_inspected_but_never_called_valid():
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    cert = _certificate(not_before=now - datetime.timedelta(days=5), not_after=now - datetime.timedelta(days=1))
    contexts = []
    def context_factory(verify):
        context = _Context(cert, verify=verify, fail_verify=verify)
        contexts.append(context)
        return context
    result = tls_certificate.probe_certificate("example.com", 443,
        connector=lambda address, timeout: _TLS(cert), context_factory=context_factory)
    assert result["state"] == "expired" and result["detail"] == "certificate_expired"
    assert [context.verify for context in contexts] == [True, False]
    assert result["data"]["not_after"] < datetime.datetime.now(datetime.timezone.utc).isoformat()


def test_verification_error_with_current_dates_is_chain_invalid_and_timeout_unknown():
    cert = _certificate()
    def context_factory(verify):
        return _Context(cert, verify=verify, fail_verify=verify)
    invalid = tls_certificate.probe_certificate("example.com", 443,
        connector=lambda address, timeout: _TLS(cert), context_factory=context_factory)
    timeout = tls_certificate.probe_certificate("example.com", 443,
        connector=lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.timeout()))
    assert invalid["state"] == "chain_invalid" and invalid["detail"] == "certificate_chain_invalid"
    assert timeout["state"] == "unknown" and timeout["detail"] == "probe_timeout"


def test_agent_publishes_correlated_certificate_result(monkeypatch):
    import xgent_wds as agent

    monkeypatch.setattr(tls_certificate, "probe_certificate", lambda host, port: {
        "state": "valid", "source": tls_certificate.SOURCE, "detail": None,
        "data": {"not_before": "2026-01-01T00:00:00Z", "not_after": "2027-01-01T00:00:00Z", "sha256": "a" * 64},
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))
    client._do_tls_certificate({"id": "cert-1", "host": "example.com", "port": 443})
    topic, result = sent[0]
    assert topic == "tls_certificate"
    assert result["feature_id"] == "D04-F13" and result["request_id"] == "cert-1"
    assert result["host"] == "example.com" and result["port"] == 443
    assert result["state"] == "valid" and result["device_id"] == agent.DEVICE_ID
