"""Read-only storage history command handlers."""

from __future__ import annotations


def volume_history(
    payload: dict, *, publish_response, device_id: str, psutil_module, time_module,
    datetime_module, logger, volume_measurement, history_minimum_span_seconds: int,
) -> None:
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    now = time_module.time()
    result = {
        "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
        "device_id": device_id, "request_id": request_id, "state": "unknown",
        "source": "psutil.disk_partitions+psutil.disk_usage",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    try:
        partitions = list(psutil_module.disk_partitions(all=False))[:64]
        volumes = []
        for part in partitions:
            mountpoint = getattr(part, "mountpoint", None)
            device = getattr(part, "device", None)
            if not isinstance(mountpoint, str) or not mountpoint or not isinstance(device, str):
                continue
            try:
                usage = psutil_module.disk_usage(mountpoint)
                total, used = getattr(usage, "total", None), getattr(usage, "used", None)
                if type(total) is not int or type(used) is not int or total <= 0 or not 0 <= used <= total:
                    raise ValueError("invalid_volume_usage")
                volume = volume_measurement(device, mountpoint, total, used, now)
                volume.update(
                    mountpoint=mountpoint[:512], filesystem=str(getattr(part, "fstype", "") or "")[:32],
                    state="supported",
                )
            except Exception:
                logger.info("Volume usage unavailable for a mounted filesystem", exc_info=True)
                volume = {
                    "mountpoint": mountpoint[:512], "filesystem": str(getattr(part, "fstype", "") or "")[:32],
                    "state": "unknown", "trend_state": "unknown", "total_bytes": None,
                    "used_bytes": None, "free_bytes": None, "used_percent": None,
                    "observed_span_seconds": None, "delta_used_bytes": None,
                    "used_bytes_per_hour": None, "estimated_hours_to_full": None,
                    "history_scope": "agent_process",
                }
            volumes.append(volume)
        result.update(
            state="supported" if any(item.get("state") == "supported" for item in volumes)
            else "unsupported" if not partitions else "unknown",
            data={"history_minimum_span_seconds": history_minimum_span_seconds,
                  "history_scope": "agent_process", "volumes": volumes},
        )
        if not partitions:
            result["detail"] = "no_mountable_volumes"
    except Exception:
        logger.exception("Volume history collection failed")
        result["detail"] = "collection_failed"
    publish_response("volume_history", result)
