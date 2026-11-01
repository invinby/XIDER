"""Bounded ICMP sample series for one owner-selected literal IP address."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time


SOURCE = "ten_bounded_icmp_echo_samples"
SAMPLE_COUNT = 10
PROCESS_TIMEOUT = 2.5
_TIMEOUT_MARKERS = (
    "request timed out", "request timeout", "timed out", "timeout for icmp_seq",
    "no answer yet for icmp_seq", "превышен интервал ожидания",
)


def _literal_address(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        return None
    if isinstance(address, ipaddress.IPv4Address) and int(address) == 0xFFFFFFFF:
        return None
    return str(address)


def _sample_state(returncode: object, output: object) -> str:
    if returncode == 0:
        return "reply"
    if not isinstance(output, str) or len(output) > 4096:
        return "unknown"
    lowered = output.casefold()
    if any(marker in lowered for marker in _TIMEOUT_MARKERS):
        return "lost"
    return "unknown"


def _unknown(address: str | None, detail: str, samples: list[dict] | None = None) -> dict:
    return {
        "address": address, "state": "unknown", "source": SOURCE, "detail": detail,
        "sent": SAMPLE_COUNT, "observed": 0, "received": 0, "lost": 0,
        "loss_percent": None, "samples": samples or [],
    }


def measure_packet_loss(*, address: str, platform: str | None = None,
                        runner=subprocess.run, which=shutil.which) -> dict:
    """Send ten single-packet ICMP probes to one literal address; never scan."""
    target = _literal_address(address)
    if target is None:
        return _unknown(None, "invalid_literal_address")

    selected = platform or sys.platform
    if selected in {"win32", "windows"}:
        executable = which("ping.exe") or which("ping")
        args_for = lambda: [executable, "-n", "1", "-w", "1000", target]
    elif selected in {"darwin", "macos"}:
        executable = which("ping") or ("/sbin/ping" if os.path.exists("/sbin/ping") else None)
        args_for = lambda: [executable, "-n", "-c", "1", "-W", "1", target]
    else:
        return _unknown(target, "platform_unsupported")
    if not executable:
        return _unknown(target, "icmp_probe_unavailable")

    samples: list[dict] = []
    for index in range(1, SAMPLE_COUNT + 1):
        started = time.monotonic()
        try:
            result = runner(args_for(), capture_output=True, text=True, timeout=PROCESS_TIMEOUT, check=False)
            output = "\n".join((getattr(result, "stdout", "") or "", getattr(result, "stderr", "") or ""))
            state = _sample_state(getattr(result, "returncode", None), output)
            detail = None if state != "unknown" else "icmp_result_unavailable"
        except subprocess.TimeoutExpired:
            state, detail = "unknown", "icmp_process_timeout"
        except OSError:
            state, detail = "unknown", "icmp_probe_unavailable"
        samples.append({"index": index, "state": state, "detail": detail,
                        "elapsed_ms": max(0, round((time.monotonic() - started) * 1000))})

    received = sum(sample["state"] == "reply" for sample in samples)
    lost = sum(sample["state"] == "lost" for sample in samples)
    observed = received + lost
    state = "unknown" if observed == 0 else "complete" if observed == SAMPLE_COUNT else "partial"
    return {
        "address": target, "state": state, "source": SOURCE,
        "detail": None if state != "unknown" else "no_confirmed_icmp_observations",
        "sent": SAMPLE_COUNT, "observed": observed, "received": received, "lost": lost,
        "loss_percent": round(lost * 100.0 / observed, 1) if observed else None,
        "samples": samples,
    }
