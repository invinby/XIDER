"""Bounded TLS certificate validation for one explicitly selected endpoint."""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import re
import socket
import ssl

from cryptography import x509

SOURCE = "verified_tls_certificate"
DEFAULT_PORT = 443
MAX_TIMEOUT = 3.0
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def parse_target(value: object) -> dict | None:
    """Parse a single host or host:port, with bracketed IPv6 for explicit ports."""
    if not isinstance(value, str) or not value or len(value) > 300 or value != value.strip():
        return None
    host, port = value, DEFAULT_PORT
    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]{1,5}))?", value)
        if not match:
            return None
        host = match.group(1)
        try:
            if ipaddress.ip_address(host).version != 6:
                return None
        except ValueError:
            return None
        if match.group(2):
            port = int(match.group(2))
    elif value.count(":") == 1:
        candidate, raw_port = value.rsplit(":", 1)
        if raw_port.isdecimal():
            host, port = candidate, int(raw_port)
        else:
            return None
    elif ":" in value:
        try:
            if ipaddress.ip_address(value).version != 6:
                return None
        except ValueError:
            return None
    if not 1 <= port <= 65535 or not host or "%" in host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            return None
        if len(host) > 253 or not host or any(not _DNS_LABEL.fullmatch(label) for label in host.split(".")):
            return None
    else:
        host = address.compressed
    return {"host": host, "port": port}


def _context(verify: bool):
    if verify:
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _read_leaf(secure, host: str) -> dict:
    der = secure.getpeercert(binary_form=True)
    if not isinstance(der, bytes) or not der or len(der) > 128 * 1024:
        raise ValueError("certificate unavailable")
    cert = x509.load_der_x509_certificate(der)
    before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=datetime.timezone.utc)
    after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    return {
        "not_before": before.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "not_after": after.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sha256": hashlib.sha256(der).hexdigest(),
    }


def _close(value) -> None:
    if value is not None:
        try:
            value.close()
        except OSError:
            pass


def probe_certificate(host: str, port: int, *, timeout: float = MAX_TIMEOUT,
                      connector=socket.create_connection, context_factory=_context,
                      now_factory=lambda: datetime.datetime.now(datetime.timezone.utc)) -> dict:
    """Validate one TLS endpoint; unverified retry reads leaf metadata only after a failed verification."""
    parsed = parse_target(f"[{host}]:{port}" if isinstance(host, str) and ":" in host else f"{host}:{port}")
    if parsed is None or parsed["host"] != host or parsed["port"] != port:
        return {"state": "invalid_request", "source": SOURCE, "detail": "invalid_endpoint", "data": None}
    if type(timeout) not in {int, float} or not 0.25 <= timeout <= MAX_TIMEOUT:
        raise ValueError("timeout must be between 0.25 and 3 seconds")
    raw = secure = None
    try:
        raw = connector((host, port), timeout=float(timeout))
        secure = context_factory(True).wrap_socket(raw, server_hostname=host)
        leaf = _read_leaf(secure, host)
        now = now_factory().astimezone(datetime.timezone.utc)
        before = datetime.datetime.fromisoformat(leaf["not_before"].replace("Z", "+00:00"))
        after = datetime.datetime.fromisoformat(leaf["not_after"].replace("Z", "+00:00"))
        if after <= now:
            return {"state": "expired", "source": SOURCE, "detail": "certificate_expired", "data": leaf}
        if before > now:
            return {"state": "not_yet_valid", "source": SOURCE, "detail": "certificate_not_yet_valid", "data": leaf}
        return {"state": "valid", "source": SOURCE, "detail": None, "data": leaf}
    except ssl.SSLCertVerificationError:
        _close(secure if secure is not None else raw)
        raw = secure = None
        try:
            raw = connector((host, port), timeout=float(timeout))
            secure = context_factory(False).wrap_socket(raw, server_hostname=host)
            leaf = _read_leaf(secure, host)
            now = now_factory().astimezone(datetime.timezone.utc)
            before = datetime.datetime.fromisoformat(leaf["not_before"].replace("Z", "+00:00"))
            after = datetime.datetime.fromisoformat(leaf["not_after"].replace("Z", "+00:00"))
            if after <= now:
                return {"state": "expired", "source": SOURCE, "detail": "certificate_expired", "data": leaf}
            if before > now:
                return {"state": "not_yet_valid", "source": SOURCE, "detail": "certificate_not_yet_valid", "data": leaf}
            return {"state": "chain_invalid", "source": SOURCE, "detail": "certificate_chain_invalid", "data": leaf}
        except (OSError, ssl.SSLError, ValueError):
            return {"state": "chain_invalid", "source": SOURCE, "detail": "certificate_chain_invalid", "data": None}
    except (TimeoutError, socket.timeout):
        return {"state": "unknown", "source": SOURCE, "detail": "probe_timeout", "data": None}
    except (OSError, ssl.SSLError, ValueError):
        return {"state": "unknown", "source": SOURCE, "detail": "tls_probe_failed", "data": None}
    finally:
        _close(secure if secure is not None else raw)
