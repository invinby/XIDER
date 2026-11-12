"""Local clock-step sampling against monotonic elapsed time."""

from __future__ import annotations

import logging


def read_clock_drift_snapshot(*, time_module, logger: logging.Logger) -> dict:
    """Estimate wall-clock steps against monotonic elapsed time over one second."""
    source = "wall_vs_monotonic_elapsed"
    try:
        wall_start = time_module.time_ns()
        monotonic_start = time_module.perf_counter_ns()
        time_module.sleep(1.0)
        wall_end = time_module.time_ns()
        monotonic_end = time_module.perf_counter_ns()
        wall_elapsed = wall_end - wall_start
        monotonic_elapsed = monotonic_end - monotonic_start
        sample_ms = round(monotonic_elapsed / 1_000_000)
        if (wall_elapsed <= 0 or monotonic_elapsed <= 0
                or not 500 <= sample_ms <= 5000):
            return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}
        drift_ms = round((wall_elapsed - monotonic_elapsed) / 1_000_000)
        return {"state": "supported", "source": source,
                "drift_ms": drift_ms, "sample_ms": sample_ms}
    except Exception:
        logger.info("Windows local clock comparison unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}
