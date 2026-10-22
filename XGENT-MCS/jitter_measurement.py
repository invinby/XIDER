"""Bounded ICMP round-trip jitter samples for one selected literal IP."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time

import packet_loss_history


SOURCE = "icmp_rtt_adjacent_mean_abs_delta"
SAMPLE_COUNT = 10
PROCESS_TIMEOUT = 2.5
_RTT = re.compile(r"\btime\s*([=<])\s*(\d+(?:[.,]\d+)?)\s*ms\b", re.IGNORECASE)
_TIMEOUT_MARKERS = (
    "request timed out", "request timeout", "timed out", "timeout for icmp_seq",
    "no answer yet for icmp_seq", "превышен интервал ожидания",
)


def _unknown(address: str | None, detail: str) -> dict:
    return {"address": address, "state": "unknown", "source": SOURCE, "detail": detail,
            "attempted": SAMPLE_COUNT, "received": 0, "lost": 0, "unknown": SAMPLE_COUNT,
            "pairs": 0, "jitter_ms": None, "samples": []}


def _parse_reply(output: object) -> tuple[str, float | None]:
    if not isinstance(output, str) or len(output) > 4096:
        return "unknown", None
    lowered = output.casefold()
    if any(marker in lowered for marker in _TIMEOUT_MARKERS):
        return "lost", None
    match = _RTT.search(output)
    if not match:
        return "unknown", None
    if match.group(1) == "<":
        return "reply", None
    try:
        value = float(match.group(2).replace(",", "."))
    except ValueError:
        return "unknown", None
    if value < 0 or value > 60000:
        return "unknown", None
    return "reply", round(value, 3)


def measure_jitter(*, address: str, platform: str | None = None,
                   runner=subprocess.run, which=shutil.which) -> dict:
    target = packet_loss_history._literal_address(address)
    if target is None:
        return _unknown(None, "invalid_literal_address")

    selected = platform or sys.platform
    if selected in {"win32", "windows"}:
        executable = which("ping.exe") or which("ping")
        args_for = lambda: [executable, "-n", "1", "-w", "1000", target]
    elif selected in {"darwin", "macos"}:
        executable = which("ping") or "/sbin/ping"
        args_for = lambda: [executable, "-n", "-c", "1", "-W", "1000", target]
    else:
        return _unknown(target, "platform_unsupported")
    if not executable:
        return _unknown(target, "icmp_probe_unavailable")

    samples: list[dict] = []
    for index in range(1, SAMPLE_COUNT + 1):
        started = time.monotonic()
        try:
            result = runner(args_for(), capture_output=True, text=True,
                            timeout=PROCESS_TIMEOUT, check=False)
            output = "\n".join((getattr(result, "stdout", "") or "",
                                 getattr(result, "stderr", "") or ""))
            state, rtt_ms = _parse_reply(output)
            if getattr(result, "returncode", None) != 0 and state == "reply":
                state, rtt_ms = "unknown", None
        except subprocess.TimeoutExpired:
            state, rtt_ms = "unknown", None
        except OSError:
            state, rtt_ms = "unknown", None
        samples.append({"index": index, "state": state, "rtt_ms": rtt_ms,
                        "elapsed_ms": max(0, round((time.monotonic() - started) * 1000))})

    deltas = []
    for previous, current in zip(samples, samples[1:]):
        if (previous["state"] == current["state"] == "reply"
                and previous["rtt_ms"] is not None and current["rtt_ms"] is not None):
            deltas.append(abs(current["rtt_ms"] - previous["rtt_ms"]))
    received = sum(sample["state"] == "reply" for sample in samples)
    lost = sum(sample["state"] == "lost" for sample in samples)
    unknown = SAMPLE_COUNT - received - lost
    state = "available" if len(deltas) == SAMPLE_COUNT - 1 else "partial" if deltas else "unknown"
    return {"address": target, "state": state, "source": SOURCE,
            "detail": None if deltas else "no_adjacent_exact_rtt_pair",
            "attempted": SAMPLE_COUNT, "received": received, "lost": lost,
            "unknown": unknown, "pairs": len(deltas),
            "jitter_ms": round(sum(deltas) / len(deltas), 3) if deltas else None,
            "samples": samples}
