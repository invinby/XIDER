"""Bounded comparison of DNS answers from two owner-selected literal resolvers."""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct

import dns_diagnostics


MAX_DNS_PACKET = 1232
MAX_ANSWERS = 256
TIMEOUT_SECONDS = 1.5
_RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}
SOURCE = "direct_udp_dns_to_owner_selected_resolvers"


def _normalize_resolvers(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("exactly_two_dns_resolvers_required")
    result = []
    for item in value:
        if not isinstance(item, str) or item != item.strip():
            raise ValueError("dns_resolver_must_be_literal_ip")
        try:
            address = ipaddress.ip_address(item)
        except ValueError as exc:
            raise ValueError("dns_resolver_must_be_literal_ip") from exc
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("dns_resolver_ip_not_supported")
        normalized = str(address)
        if normalized in result:
            raise ValueError("dns_resolvers_must_be_distinct")
        result.append(normalized)
    return result


def _encode_name(hostname):
    labels = hostname.split(".")
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\x00"


def build_dns_query(hostname: str, qtype: str, transaction_id: int) -> bytes:
    if qtype not in {"A", "AAAA"} or type(transaction_id) is not int or not 0 <= transaction_id <= 65535:
        raise ValueError("invalid_dns_query")
    normalized = dns_diagnostics.normalize_hostname(hostname)
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(normalized) + struct.pack("!HH", 1 if qtype == "A" else 28, 1)
    return header + question


def _decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels = []
    cursor = offset
    next_offset = None
    visited = set()
    for _ in range(128):
        if cursor >= len(packet):
            raise ValueError("truncated_dns_name")
        length = packet[cursor]
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                raise ValueError("truncated_dns_pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer >= len(packet) or pointer in visited:
                raise ValueError("invalid_dns_pointer")
            visited.add(pointer)
            if next_offset is None:
                next_offset = cursor + 2
            cursor = pointer
            continue
        if length & 0xC0:
            raise ValueError("invalid_dns_label")
        cursor += 1
        if length == 0:
            result = ".".join(labels).lower()
            return result, next_offset if next_offset is not None else cursor
        if length > 63 or cursor + length > len(packet):
            raise ValueError("invalid_dns_label_length")
        try:
            labels.append(packet[cursor:cursor + length].decode("ascii").lower())
        except UnicodeDecodeError as exc:
            raise ValueError("non_ascii_dns_label") from exc
        cursor += length
        if sum(map(len, labels)) + max(0, len(labels) - 1) > 253:
            raise ValueError("dns_name_too_long")
    raise ValueError("dns_compression_depth_exceeded")


def parse_dns_response(packet: bytes, transaction_id: int, hostname: str, qtype: str) -> dict:
    if not isinstance(packet, bytes) or len(packet) < 12 or len(packet) > MAX_DNS_PACKET:
        raise ValueError("invalid_dns_packet_size")
    try:
        actual_id, flags, questions, answer_count, authority_count, additional_count = struct.unpack("!HHHHHH", packet[:12])
    except struct.error as exc:
        raise ValueError("invalid_dns_header") from exc
    if actual_id != transaction_id or not flags & 0x8000 or flags & 0x7800 or flags & 0x0200:
        raise ValueError("dns_response_not_bound_to_query")
    if questions != 1 or answer_count > MAX_ANSWERS or authority_count > MAX_ANSWERS or additional_count > MAX_ANSWERS:
        raise ValueError("dns_response_section_limit")
    expected_name = dns_diagnostics.normalize_hostname(hostname)
    expected_type = 1 if qtype == "A" else 28 if qtype == "AAAA" else None
    if expected_type is None:
        raise ValueError("invalid_dns_qtype")
    cursor = 12
    question_name, cursor = _decode_name(packet, cursor)
    if cursor + 4 > len(packet):
        raise ValueError("truncated_dns_question")
    question_type, question_class = struct.unpack("!HH", packet[cursor:cursor + 4])
    cursor += 4
    if (question_name, question_type, question_class) != (expected_name, expected_type, 1):
        raise ValueError("dns_question_mismatch")
    addresses = set()
    for _ in range(answer_count):
        _owner, cursor = _decode_name(packet, cursor)
        if cursor + 10 > len(packet):
            raise ValueError("truncated_dns_record")
        record_type, record_class, _ttl, length = struct.unpack("!HHIH", packet[cursor:cursor + 10])
        cursor += 10
        end = cursor + length
        if end > len(packet):
            raise ValueError("truncated_dns_rdata")
        if record_class == 1 and record_type == expected_type:
            wanted_length = 4 if qtype == "A" else 16
            if length != wanted_length:
                raise ValueError("invalid_dns_address_length")
            addresses.add(str(ipaddress.ip_address(packet[cursor:end])))
        cursor = end
    if cursor > len(packet):
        raise ValueError("invalid_dns_packet_cursor")
    rcode_value = flags & 0x000F
    rcode = _RCODES.get(rcode_value, f"RCODE_{rcode_value}")
    return {"rcode": rcode, "addresses": sorted(addresses, key=lambda item: (ipaddress.ip_address(item).version, int(ipaddress.ip_address(item))))}


def query_dns(server: str, hostname: str, qtype: str, timeout_seconds: float = TIMEOUT_SECONDS,
              *, socket_factory=socket.socket, transaction_id: int | None = None) -> dict:
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 3:
        raise ValueError("invalid_dns_timeout")
    resolver = ipaddress.ip_address(server)
    if resolver.version == 6 and resolver.is_link_local:
        raise ValueError("link_local_resolver_not_supported")
    identifier = secrets.randbits(16) if transaction_id is None else transaction_id
    request = build_dns_query(hostname, qtype, identifier)
    family = socket.AF_INET if resolver.version == 4 else socket.AF_INET6
    endpoint = (str(resolver), 53) if resolver.version == 4 else (str(resolver), 53, 0, 0)
    with socket_factory(family, socket.SOCK_DGRAM) as client:
        client.settimeout(float(timeout_seconds))
        client.sendto(request, endpoint)
        packet, peer = client.recvfrom(MAX_DNS_PACKET + 1)
    try:
        peer_ip = str(ipaddress.ip_address(peer[0]))
        peer_port = int(peer[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError("invalid_dns_peer") from exc
    if peer_ip != str(resolver) or peer_port != 53:
        raise ValueError("dns_peer_mismatch")
    return parse_dns_response(packet, identifier, hostname, qtype)


def compare_resolvers(hostname: object, resolvers: object, *, query_fn=None,
                      timeout_seconds: float = TIMEOUT_SECONDS) -> dict:
    normalized_host = dns_diagnostics.normalize_hostname(hostname)
    normalized_resolvers = _normalize_resolvers(resolvers)
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 3:
        raise ValueError("invalid_dns_timeout")
    query = query_fn or query_dns
    rows = []
    for resolver in normalized_resolvers:
        records = {}
        complete = True
        for qtype in ("A", "AAAA"):
            try:
                result = query(resolver, normalized_host, qtype, float(timeout_seconds))
                if not isinstance(result, dict) or result.get("rcode") not in set(_RCODES.values()) | {f"RCODE_{code}" for code in range(16)}:
                    raise ValueError("invalid_dns_result")
                raw_addresses = result.get("addresses")
                if not isinstance(raw_addresses, list) or len(raw_addresses) > MAX_ANSWERS:
                    raise ValueError("invalid_dns_addresses")
                parsed = []
                for value in raw_addresses:
                    address = ipaddress.ip_address(value) if isinstance(value, str) else None
                    if address is None or address.version != (4 if qtype == "A" else 6):
                        raise ValueError("dns_address_family_mismatch")
                    normalized = str(address)
                    if normalized not in parsed:
                        parsed.append(normalized)
                records[qtype] = {"rcode": result["rcode"], "addresses": sorted(parsed)}
            except (TimeoutError, socket.timeout, OSError, ValueError):
                complete = False
                break
        rows.append({"address": resolver, "status": "complete" if complete else "unknown",
                     "records": records if complete else {}})
    if all(row["status"] == "complete" for row in rows):
        comparison = "same" if rows[0]["records"] == rows[1]["records"] else "different"
    else:
        comparison = "unknown"
    return {"hostname": normalized_host, "comparison": comparison, "resolvers": rows}
