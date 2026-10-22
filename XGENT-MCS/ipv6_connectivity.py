"""Bounded IPv6 reachability check using Cloudflare's documented DoT endpoint."""
from __future__ import annotations

import errno
import secrets
import socket
import ssl
import struct

IPV6_RESOLVER = "2606:4700:4700::1111"
TLS_SERVER_NAME = "one.one.one.one"
QUERY_NAME = "cloudflare-dns.com"
SOURCE = "cloudflare_ipv6_dot"
MAX_FRAME_BYTES = 4096


def build_dns_query(identifier: int) -> bytes:
    if type(identifier) is not int or not 0 <= identifier <= 0xFFFF:
        raise ValueError("DNS identifier must be a 16-bit integer")
    labels = QUERY_NAME.split(".")
    question = b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels) + b"\0"
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0) + question + struct.pack("!HH", 1, 1)


def valid_dns_response(response: bytes, identifier: int) -> bool:
    if not isinstance(response, bytes) or not 12 <= len(response) <= MAX_FRAME_BYTES:
        return False
    try:
        response_id, flags, questions, _answers, _authority, _additional = struct.unpack("!HHHHHH", response[:12])
        if (response_id != identifier or not flags & 0x8000 or flags & 0x7800 or flags & 0x0200
                or flags & 0x000F or questions != 1):
            return False
        offset = 12
        labels = []
        while True:
            if offset >= len(response):
                return False
            size = response[offset]
            offset += 1
            if size == 0:
                break
            if size > 63 or offset + size > len(response):
                return False
            label = response[offset:offset + size]
            if any(byte < 0x21 or byte > 0x7E for byte in label):
                return False
            labels.append(label.decode("ascii").lower())
            offset += size
        if offset + 4 > len(response):
            return False
        qtype, qclass = struct.unpack("!HH", response[offset:offset + 4])
        return ".".join(labels) == QUERY_NAME and qtype == 1 and qclass == 1
    except (ValueError, UnicodeError, struct.error):
        return False


def _recv_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.recv(size - len(chunks))
        if not chunk:
            raise OSError("peer closed the DNS-over-TLS stream")
        chunks.extend(chunk)
    return bytes(chunks)


def probe_ipv6(*, timeout: float = 2.5, socket_factory=socket.socket,
               context_factory=ssl.create_default_context, identifier_factory=secrets.randbits) -> dict:
    """Confirm a fixed IPv6 route only after a verified TLS and DNS response."""
    if type(timeout) not in {int, float} or not 0.25 <= timeout <= 3.0:
        raise ValueError("timeout must be between 0.25 and 3 seconds")
    if not getattr(socket, "has_ipv6", True):
        return {"state": "unsupported", "source": SOURCE, "detail": "ipv6_unavailable"}

    raw = None
    secure = None
    try:
        raw = socket_factory(socket.AF_INET6, socket.SOCK_STREAM)
        raw.settimeout(float(timeout))
        raw.connect((IPV6_RESOLVER, 853, 0, 0))
        secure = context_factory().wrap_socket(raw, server_hostname=TLS_SERVER_NAME)
        identifier = identifier_factory(16)
        query = build_dns_query(identifier)
        secure.sendall(struct.pack("!H", len(query)) + query)
        frame_size = struct.unpack("!H", _recv_exact(secure, 2))[0]
        if not 12 <= frame_size <= MAX_FRAME_BYTES:
            return {"state": "unknown", "source": SOURCE, "detail": "invalid_dns_response"}
        response = _recv_exact(secure, frame_size)
        if not valid_dns_response(response, identifier):
            return {"state": "unknown", "source": SOURCE, "detail": "invalid_dns_response"}
        return {"state": "reachable", "source": SOURCE, "detail": None}
    except OSError as exc:
        if exc.errno == errno.ENETUNREACH:
            return {"state": "no_route", "source": SOURCE, "detail": "ipv6_route_unavailable"}
        detail = "probe_timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "probe_failed"
        return {"state": "unknown", "source": SOURCE, "detail": detail}
    except (ssl.SSLError, ValueError, struct.error):
        return {"state": "unknown", "source": SOURCE, "detail": "tls_or_dns_failed"}
    finally:
        closer = secure if secure is not None else raw
        if closer is not None:
            try:
                closer.close()
            except OSError:
                pass
