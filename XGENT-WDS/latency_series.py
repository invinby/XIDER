"""Bounded TCP connection-latency samples for one explicitly selected endpoint."""
from __future__ import annotations

import socket
import statistics
import time

import tls_certificate


SOURCE = "tcp_connect_monotonic"
ATTEMPTS = 5
CONNECT_TIMEOUT = 1.25
SAMPLE_INTERVAL = 0.25


def _valid_endpoint(host: object, port: object) -> bool:
    if not isinstance(host, str) or not host or type(port) is not int:
        return False
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    parsed = tls_certificate.parse_target(target)
    return bool(parsed and parsed["host"] == host and parsed["port"] == port)


def measure_latency_series(host: str, port: int, *, connector=socket.create_connection,
                           monotonic=time.monotonic, sleeper=time.sleep) -> dict:
    """Measure five sequential TCP handshakes to exactly one host/port.

    Values are TCP connection setup durations, not ICMP RTT. Failed attempts
    remain explicit points without invented zero-latency values.
    """
    if not _valid_endpoint(host, port):
        return {"state": "invalid_request", "source": SOURCE, "samples": [], "summary": None,
                "detail": "invalid_endpoint"}

    samples = []
    successful_ms = []
    for index in range(ATTEMPTS):
        started = monotonic()
        connection = None
        try:
            connection = connector((host, port), timeout=CONNECT_TIMEOUT)
            elapsed_ms = round(max(0.0, (monotonic() - started) * 1000.0), 2)
            successful_ms.append(elapsed_ms)
            sample_state = "connected"
        except (TimeoutError, socket.timeout):
            elapsed_ms, sample_state = None, "timeout"
        except ConnectionRefusedError:
            elapsed_ms, sample_state = None, "refused"
        except OSError:
            elapsed_ms, sample_state = None, "unknown"
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        samples.append({"index": index + 1, "state": sample_state, "latency_ms": elapsed_ms})
        if index + 1 < ATTEMPTS:
            sleeper(SAMPLE_INTERVAL)

    count = len(successful_ms)
    summary = {
        "successful": count,
        "attempted": ATTEMPTS,
        "min_ms": min(successful_ms) if successful_ms else None,
        "mean_ms": round(statistics.fmean(successful_ms), 2) if successful_ms else None,
        "max_ms": max(successful_ms) if successful_ms else None,
    }
    state = "available" if count == ATTEMPTS else "partial" if count else "unknown"
    return {"state": state, "source": SOURCE, "samples": samples, "summary": summary,
            "detail": None if count else "no_tcp_connection_confirmed"}
