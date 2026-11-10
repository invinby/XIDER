"""Single-domain DNSSEC validation hint from an OS-configured resolver."""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct

import dns_diagnostics
import dns_comparison
import dns_settings

MAX_PACKET = 1232
TIMEOUT_SECONDS = 1.5
SOURCE = "edns_do_ad_from_configured_resolver"
_RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}


def build_query(hostname: str, transaction_id: int) -> bytes:
    if type(transaction_id) is not int or not 0 <= transaction_id <= 65535:
        raise ValueError("invalid_dns_transaction_id")
    name = dns_diagnostics.normalize_hostname(hostname)
    qname = b"".join(bytes((len(label),)) + label.encode("ascii") for label in name.split(".")) + b"\0"
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 1)
    question = qname + struct.pack("!HH", 1, 1)
    opt = b"\0" + struct.pack("!HHIH", 41, MAX_PACKET, 0x8000, 0)
    return header + question + opt


def parse_response(packet: bytes, transaction_id: int, hostname: str) -> dict:
    if not isinstance(packet, bytes) or not 12 <= len(packet) <= MAX_PACKET:
        raise ValueError("invalid_dns_packet_size")
    actual_id, flags, questions, answers, authority, additional = struct.unpack("!HHHHHH", packet[:12])
    if actual_id != transaction_id or not flags & 0x8000 or flags & 0x7800 or flags & 0x0200:
        raise ValueError("dns_response_not_bound_to_query")
    if questions != 1 or answers > 256 or authority > 256 or additional > 256:
        raise ValueError("dns_response_section_limit")
    name, cursor = dns_comparison._decode_name(packet, 12)
    if cursor + 4 > len(packet):
        raise ValueError("truncated_dns_question")
    qtype, qclass = struct.unpack("!HH", packet[cursor:cursor + 4])
    expected = dns_diagnostics.normalize_hostname(hostname)
    if (name, qtype, qclass) != (expected, 1, 1):
        raise ValueError("dns_question_mismatch")
    rcode_number = flags & 0x000F
    rcode = _RCODES.get(rcode_number, f"RCODE_{rcode_number}")
    ad = bool(flags & 0x0020)
    return {"assessment": "validated_by_resolver" if ad else "not_confirmed", "ad_bit": ad, "rcode": rcode}


def query_resolver(resolver: str, hostname: str, *, timeout_seconds: float = TIMEOUT_SECONDS,
                   socket_factory=socket.socket, transaction_id: int | None = None) -> dict:
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 3:
        raise ValueError("invalid_dns_timeout")
    address = ipaddress.ip_address(resolver)
    identifier = secrets.randbits(16) if transaction_id is None else transaction_id
    request = build_query(hostname, identifier)
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    endpoint = (str(address), 53) if family == socket.AF_INET else (str(address), 53, 0, 0)
    with socket_factory(family, socket.SOCK_DGRAM) as client:
        client.settimeout(float(timeout_seconds))
        client.sendto(request, endpoint)
        packet, peer = client.recvfrom(MAX_PACKET + 1)
    if str(ipaddress.ip_address(peer[0])) != str(address) or int(peer[1]) != 53:
        raise ValueError("dns_peer_mismatch")
    return parse_response(packet, identifier, hostname)


def _configured_resolvers(settings: dict) -> set[str]:
    found = set()
    if not isinstance(settings, dict) or settings.get("state") != "supported":
        return found
    rows = settings.get("resolvers")
    if not isinstance(rows, list):
        return found
    for row in rows[:64]:
        if not isinstance(row, dict) or not isinstance(row.get("addresses"), list):
            continue
        for value in row["addresses"][:8]:
            try:
                found.add(str(ipaddress.ip_address(value)))
            except (ValueError, TypeError):
                continue
    return found


def check_domain(hostname: object, resolver: object, *, query_fn=None, settings_fn=dns_settings.read_dns_settings) -> dict:
    normalized_host = dns_diagnostics.normalize_hostname(hostname)
    if not isinstance(resolver, str) or resolver != resolver.strip():
        raise ValueError("dns_resolver_must_be_configured_literal_ip")
    try:
        normalized_resolver = str(ipaddress.ip_address(resolver))
    except ValueError as exc:
        raise ValueError("dns_resolver_must_be_configured_literal_ip") from exc
    if normalized_resolver not in _configured_resolvers(settings_fn()):
        raise ValueError("dns_resolver_not_configured_on_device")
    result = (query_fn or query_resolver)(normalized_resolver, normalized_host)
    if (not isinstance(result, dict) or set(result) != {"assessment", "ad_bit", "rcode"}
            or result.get("assessment") not in {"validated_by_resolver", "not_confirmed"}
            or type(result.get("ad_bit")) is not bool
            or result["assessment"] != ("validated_by_resolver" if result["ad_bit"] else "not_confirmed")
            or result.get("rcode") not in set(_RCODES.values()) | {f"RCODE_{code}" for code in range(16)}):
        raise ValueError("invalid_dnssec_result")
    return {"hostname": normalized_host, "resolver": normalized_resolver, **result}
