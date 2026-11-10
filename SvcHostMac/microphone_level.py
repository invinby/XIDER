"""Measure input level in memory without retaining or returning audio samples."""

from __future__ import annotations

import math
import threading
import time

import microphone_devices


MAX_SAMPLES = 300_000
DEFAULT_SAMPLE_RATE = 44_100
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000


class _LevelAccumulator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sum_squares = 0.0
        self._peak = 0.0
        self._sample_count = 0

    def add(self, samples: object) -> None:
        import numpy as np

        values = np.asarray(samples)
        if values.size == 0 or values.size > MAX_SAMPLES or not np.issubdtype(values.dtype, np.number):
            return
        values = np.asarray(values, dtype=np.float64)
        if not np.isfinite(values).all():
            return
        flat = values.reshape(-1)
        with self._lock:
            if self._sample_count + flat.size > MAX_SAMPLES:
                return
            self._sum_squares += float(np.dot(flat, flat))
            self._peak = max(self._peak, float(np.max(np.abs(flat))))
            self._sample_count += int(flat.size)

    def result(self) -> dict[str, int | float] | None:
        with self._lock:
            count = self._sample_count
            if count <= 0:
                return None
            rms = math.sqrt(self._sum_squares / count)
            peak = self._peak
        return {
            "rms_dbfs": _to_dbfs(rms),
            "peak_dbfs": _to_dbfs(peak),
            "sample_count": count,
        }


def _to_dbfs(amplitude: float) -> float:
    if not math.isfinite(amplitude) or amplitude <= 0:
        return -120.0
    return round(max(-120.0, min(0.0, 20.0 * math.log10(amplitude))), 1)


def measure_input_level(sounddevice: object, input_id: object, *, duration_seconds: float = 1.0) -> dict[str, int | float] | None:
    """Open one selected mono input briefly and retain only aggregate level values."""
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not 0.25 <= duration_seconds <= 2.0:
        raise ValueError("invalid_observation_duration")
    devices = list(sounddevice.query_devices())
    index = microphone_devices.resolve_input_index(devices, input_id)
    if index is None:
        return None
    row = devices[index]
    sample_rate = row.get("default_samplerate") if isinstance(row, dict) else None
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) or not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        sample_rate = DEFAULT_SAMPLE_RATE
    accumulator = _LevelAccumulator()
    stream_status = False

    def callback(indata, _frames, _time_info, status):
        nonlocal stream_status
        if status:
            stream_status = True
            return
        accumulator.add(indata)

    with sounddevice.InputStream(device=index, channels=1, samplerate=int(sample_rate), callback=callback):
        time.sleep(float(duration_seconds))
    if stream_status:
        raise RuntimeError("audio_input_stream_status")
    return accumulator.result()
