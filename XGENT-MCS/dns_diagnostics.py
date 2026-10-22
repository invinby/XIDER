"""Bounded one-shot DNS lookup for an owner-selected hostname."""

from __future__ import annotations

import ipaddress
import queue
import re
import socket
import threading


MAX_ADDRESSES = 16
_LOOKUP_SLOTS = threading.BoundedSemaphore(2)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def normalize_hostname(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 254 or value != value.strip():
        raise ValueError("invalid_dns_hostname")
    candidate = value[:-1] if value.endswith(".") else value
    if not candidate or len(candidate) > 253 or any(ord(char) < 33 for char in candidate):
        raise ValueError("invalid_dns_hostname")
    try:
        labels = [label.encode("idna").decode("ascii").lower() for label in candidate.split(".")]
    except UnicodeError as exc:
        raise ValueError("invalid_dns_hostname") from exc
    if any(not _LABEL.fullmatch(label) for label in labels):
        raise ValueError("invalid_dns_hostname")
    normalized = ".".join(labels)
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    raise ValueError("dns_hostname_must_be_a_name")


def _normalize_expected(values: object) -> set[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_ADDRESSES:
        raise ValueError("invalid_expected_addresses")
    try:
        addresses = {str(ipaddress.ip_address(value)) for value in values if isinstance(value, str)}
    except ValueError as exc:
        raise ValueError("invalid_expected_addresses") from exc
    if len(addresses) != len(values):
        raise ValueError("invalid_expected_addresses")
    return addresses


def _run_resolver(resolver, hostname: str, timeout_seconds: float):
    if not _LOOKUP_SLOTS.acquire(blocking=False):
        raise TimeoutError("dns_resolver_busy")
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def run():
        try:
            result_queue.put((True, resolver(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)))
        except BaseException as exc:
            result_queue.put((False, exc))
        finally:
            _LOOKUP_SLOTS.release()

    threading.Thread(target=run, name="xider-dns-lookup", daemon=True).start()
    try:
        success, value = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError("dns_lookup_timeout") from exc
    if not success:
        raise value
    return value


def resolve_dns(
    hostname: object,
    expected_addresses: object = None,
    *,
    resolver=None,
    timeout_seconds: float = 5.0,
) -> dict:
    normalized = normalize_hostname(hostname)
    expected = _normalize_expected(expected_addresses)
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0.1 <= timeout_seconds <= 10:
        raise ValueError("invalid_dns_timeout")
    rows = _run_resolver(resolver or socket.getaddrinfo, normalized, float(timeout_seconds))
    found = set()
    for row in rows if isinstance(rows, (list, tuple)) else ():
        try:
            address = str(ipaddress.ip_address(row[4][0]))
        except (IndexError, TypeError, ValueError):
            continue
        found.add(address)
    ordered = sorted(found, key=lambda item: (ipaddress.ip_address(item).version, int(ipaddress.ip_address(item))))
    if not ordered:
        raise LookupError("dns_no_addresses")
    truncated = len(ordered) > MAX_ADDRESSES
    selected = ordered[:MAX_ADDRESSES]
    expected_match = None if expected is None or truncated else set(selected) == expected
    return {
        "hostname": normalized,
        "addresses": selected,
        "truncated": truncated,
        "expected_match": expected_match,
    }
