"""Bounded performance-history command handlers."""

from __future__ import annotations


_SUPPORTED_PERIODS = {15, 30, 60}


def _request_id(payload: dict) -> str | None:
    value = payload.get("id")
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else None


def cpu_history(
    payload: dict, *, publish_response, device_id: str, psutil_module, math_module,
    datetime_module, logger,
) -> None:
    period = payload.get("period_seconds")
    result = {
        "type": "cpu_history", "schema": "xider.cpu-history.v1", "feature_id": "D01-F02",
        "device_id": device_id, "request_id": _request_id(payload), "state": "unknown",
        "source": "psutil.cpu_percent",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    if type(period) is not int or period not in _SUPPORTED_PERIODS:
        result["detail"] = "period_not_supported"
        publish_response("cpu_history", result)
        return
    try:
        samples = []
        for index in range(period // 5):
            value = psutil_module.cpu_percent(interval=5)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math_module.isfinite(value) or not 0 <= value <= 100
            ):
                raise ValueError("invalid_cpu_sample")
            samples.append({"offset_seconds": (index + 1) * 5, "percent": round(float(value), 1)})
        result.update(
            state="supported",
            data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
        )
    except Exception:
        logger.exception("CPU history sampling failed")
        result["detail"] = "sampling_failed"
    publish_response("cpu_history", result)


def memory_history(
    payload: dict, *, publish_response, device_id: str, psutil_module, math_module,
    time_module, datetime_module, logger,
) -> None:
    period = payload.get("period_seconds")
    result = {
        "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
        "device_id": device_id, "request_id": _request_id(payload), "state": "unknown",
        "source": "psutil.virtual_memory+psutil.swap_memory",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    if type(period) is not int or period not in _SUPPORTED_PERIODS:
        result["detail"] = "period_not_supported"
        publish_response("memory_history", result)
        return
    try:
        samples, swap_states = [], []
        for index in range(period // 5):
            time_module.sleep(5)
            ram = psutil_module.virtual_memory().percent
            if (isinstance(ram, bool) or not isinstance(ram, (int, float))
                    or not math_module.isfinite(ram) or not 0 <= ram <= 100):
                raise ValueError("invalid_ram_sample")
            swap_percent, swap_state = None, "unknown"
            try:
                swap = psutil_module.swap_memory()
                total, value = getattr(swap, "total", None), getattr(swap, "percent", None)
                if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
                    if (isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math_module.isfinite(value) and 0 <= value <= 100):
                        swap_percent, swap_state = round(float(value), 1), "supported"
                elif total == 0:
                    swap_state = "unsupported"
            except Exception:
                logger.info("Swap telemetry unavailable for memory history", exc_info=True)
            swap_states.append(swap_state)
            samples.append({
                "offset_seconds": (index + 1) * 5,
                "ram_percent": round(float(ram), 1),
                "swap_percent": swap_percent,
            })
        swap_state = (
            swap_states[0] if swap_states and all(value == swap_states[0] for value in swap_states)
            else "unknown"
        )
        result.update(
            state="supported",
            data={"period_seconds": period, "sample_interval_seconds": 5,
                  "swap_state": swap_state, "samples": samples},
        )
    except Exception:
        logger.exception("Memory history sampling failed")
        result["detail"] = "sampling_failed"
    publish_response("memory_history", result)


def memory_pressure_history(
    payload: dict, *, publish_response, device_id: str, time_module, datetime_module,
    logger, source: str, read_sample, normalize_sample,
) -> None:
    period = payload.get("period_seconds")
    result = {
        "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
        "feature_id": "D01-F05", "device_id": device_id, "request_id": _request_id(payload),
        "state": "unknown", "source": source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    if type(period) is not int or period not in _SUPPORTED_PERIODS:
        result["detail"] = "period_not_supported"
        publish_response("memory_pressure_history", result)
        return
    try:
        samples = []
        for index in range(period // 5):
            time_module.sleep(5)
            samples.append({"offset_seconds": (index + 1) * 5, **normalize_sample(read_sample())})
        result.update(
            state="supported" if any(item["state"] in {"low", "intermediate", "high"} for item in samples)
            else "unknown",
            data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
        )
    except Exception:
        logger.exception("Memory pressure sampling failed")
        result["detail"] = "sampling_failed"
    publish_response("memory_pressure_history", result)
