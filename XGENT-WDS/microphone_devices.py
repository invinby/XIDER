"""Bounded, privacy-minimized microphone input catalog helpers."""

from collections.abc import Iterable, Mapping
import math
from pathlib import Path


ROUNDTRIP_DURATION_SECONDS = 0.75
STEREO_BALANCE_DURATION_SECONDS = 1.0
MAX_PREVIEW_DURATION_SECONDS = 30
MAX_PREVIEW_BYTES = 2 * 1024 * 1024


def measure_stereo_channel_balance(sounddevice, *, input_device_id: object,
                                   input_device_name: object = None) -> dict[str, int | float | str]:
    """Measure one selected stereo input in memory and return channel RMS only."""
    try:
        devices = list(sounddevice.query_devices())
    except Exception as exc:
        raise ValueError("selected_stereo_microphone_unavailable") from exc
    input_index = resolve_input_index(devices, input_device_id)
    if input_index is None:
        raise ValueError("selected_stereo_microphone_unavailable")
    row = devices[input_index]
    channels = row.get("max_input_channels")
    if (isinstance(channels, bool) or not isinstance(channels, int) or channels < 2
            or (input_device_name is not None and row.get("name") != input_device_name)):
        raise ValueError("selected_stereo_microphone_unavailable")
    rate = row.get("default_samplerate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 8000 <= rate <= 192000:
        rate = 44100
    else:
        rate = int(rate)
    frame_count = int(rate * STEREO_BALANCE_DURATION_SECONDS)
    samples = None
    array = None
    try:
        import numpy as np
        samples = sounddevice.rec(
            frame_count, samplerate=rate, channels=2, device=input_index, dtype="float32",
        )
        sounddevice.wait()
        array = np.asarray(samples)
        if (array.ndim != 2 or array.shape != (frame_count, 2)
                or not np.isfinite(array).all() or np.any(np.abs(array) > 1.0)):
            raise ValueError("stereo_balance_samples_invalid")
        rms = np.sqrt(np.mean(np.square(array.astype(np.float64)), axis=0))
        floor = 1e-6
        left_dbfs = round(20.0 * math.log10(max(float(rms[0]), floor)), 2)
        right_dbfs = round(20.0 * math.log10(max(float(rms[1]), floor)), 2)
        return {
            "state": "measured", "duration_ms": 1000, "sample_rate": rate,
            "left_dbfs": left_dbfs, "right_dbfs": right_dbfs,
            "difference_db": round(abs(left_dbfs - right_dbfs), 2),
        }
    finally:
        # The sample itself is never persisted or returned. Clear its mutable
        # capture buffer before releasing the final local reference.
        for buffer in (array, samples):
            try:
                if buffer is not None:
                    buffer.fill(0)
            except (AttributeError, TypeError, ValueError):
                pass


def _create_roundtrip_temp_file():
    import media_temp
    return media_temp.create_media_temp_file(".wav")


def run_microphone_roundtrip(sounddevice, soundfile, *, input_device_id: object,
                             output_device_id: object, input_device_name: object = None,
                             output_device_name: object = None) -> dict[str, int | str]:
    """Record a short explicit test and play it locally, deleting its WAV on every exit."""
    try:
        devices = list(sounddevice.query_devices())
    except Exception as exc:
        raise ValueError("selected_audio_device_unavailable") from exc
    input_index = resolve_input_index(devices, input_device_id)
    output_index = resolve_output_index(devices, output_device_id)
    if input_index is None or output_index is None:
        raise ValueError("selected_audio_device_unavailable")
    if ((input_device_name is not None and devices[input_index].get("name") != input_device_name)
            or (output_device_name is not None and devices[output_index].get("name") != output_device_name)):
        raise ValueError("selected_audio_device_changed")
    input_row = devices[input_index]
    rate = input_row.get("default_samplerate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 8000 <= rate <= 192000:
        rate = 44100
    else:
        rate = int(rate)
    frame_count = int(rate * ROUNDTRIP_DURATION_SECONDS)
    samples = sounddevice.rec(
        frame_count, samplerate=rate, channels=1, device=input_index, dtype="float32",
    )
    sounddevice.wait()
    temp_file = _create_roundtrip_temp_file()
    path = Path(temp_file.name)
    try:
        temp_file.close()
        soundfile.write(str(path), samples, rate, format="WAV", subtype="PCM_16")
        playback_samples, playback_rate = soundfile.read(
            str(path), dtype="float32", always_2d=True,
        )
        if playback_rate != rate or len(playback_samples) <= 0:
            raise ValueError("microphone_test_audio_invalid")
        sounddevice.play(playback_samples, samplerate=rate, device=output_index)
        sounddevice.wait()
        return {"state": "played", "duration_ms": 750, "sample_rate": rate}
    except Exception:
        try:
            sounddevice.stop()
        except Exception:
            pass
        raise
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _create_preview_temp_file():
    import media_temp
    return media_temp.create_media_temp_file(".ogg")


def preview_microphone_recording(sounddevice, soundfile, *, input_device_id: object,
                                 output_device_id: object, duration_seconds: object,
                                 input_device_name: object = None,
                                 output_device_name: object = None) -> bytes:
    """Record an explicit bounded sample, play it locally, then return an OGG preview."""
    if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int)
            or not 1 <= duration_seconds <= MAX_PREVIEW_DURATION_SECONDS):
        raise ValueError("microphone_preview_duration_invalid")
    try:
        devices = list(sounddevice.query_devices())
    except Exception as exc:
        raise ValueError("selected_audio_device_unavailable") from exc
    input_index = resolve_input_index(devices, input_device_id)
    output_index = resolve_output_index(devices, output_device_id)
    if input_index is None or output_index is None:
        raise ValueError("selected_audio_device_unavailable")
    if ((input_device_name is not None and devices[input_index].get("name") != input_device_name)
            or (output_device_name is not None and devices[output_index].get("name") != output_device_name)):
        raise ValueError("selected_audio_device_changed")
    rate = devices[input_index].get("default_samplerate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 8000 <= rate <= 192000:
        rate = 44100
    else:
        rate = int(rate)
    samples = None
    path = None
    try:
        frame_count = int(duration_seconds * rate)
        samples = sounddevice.rec(frame_count, samplerate=rate,
                                  channels=1, device=input_index, dtype="float32")
        sounddevice.wait()
        import numpy as np
        samples = np.asarray(samples)
        if (samples.ndim != 2 or samples.shape != (frame_count, 1)
                or not np.isfinite(samples).all() or np.any(np.abs(samples) > 1.0)):
            raise ValueError("microphone_preview_audio_invalid")
        temp_file = _create_preview_temp_file()
        path = Path(temp_file.name)
        temp_file.close()
        sounddevice.play(samples, samplerate=rate, device=output_index)
        sounddevice.wait()
        soundfile.write(str(path), samples, rate, format="OGG")
        payload = path.read_bytes()
        if not payload or len(payload) > MAX_PREVIEW_BYTES:
            raise ValueError("microphone_preview_audio_invalid")
        return payload
    except Exception:
        try:
            sounddevice.stop()
        except Exception:
            pass
        raise
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if samples is not None:
                samples.fill(0)
        except (AttributeError, TypeError, ValueError):
            pass


MAX_INPUT_DEVICES = 16
MAX_DEVICE_NAME = 80
MAX_CHANNELS = 32


def project_input_devices(devices: Iterable[object]) -> list[dict]:
    """Project sounddevice query rows to owner-visible, non-sensitive fields."""
    projected = []
    try:
        rows = iter(devices)
    except TypeError:
        return projected
    for index, row in enumerate(rows):
        if len(projected) >= MAX_INPUT_DEVICES:
            break
        if not isinstance(row, Mapping):
            continue
        channels = row.get("max_input_channels")
        if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        name = "".join(char for char in name if char.isprintable()).strip()[:MAX_DEVICE_NAME]
        if not name:
            continue
        rate = row.get("default_samplerate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            rate = None
        elif not 8000 <= rate <= 192000:
            rate = None
        else:
            rate = int(rate)
        projected.append({
            "input_id": str(index),
            "name": name,
            "channels": min(channels, MAX_CHANNELS),
            "sample_rate": rate,
        })
    return projected


def resolve_input_index(devices: Iterable[object], input_id: object) -> int | None:
    """Resolve a catalog ID against the current device list; never trust stale IDs."""
    if not isinstance(input_id, str) or not input_id.isascii() or not input_id.isdecimal():
        return None
    try:
        index = int(input_id)
        rows = list(devices)
    except (TypeError, ValueError):
        return None
    if str(index) != input_id or index >= len(rows):
        return None
    row = rows[index]
    if not isinstance(row, Mapping):
        return None
    channels = row.get("max_input_channels")
    if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
        return None
    return index


def project_output_devices(devices: Iterable[object]) -> list[dict]:
    """Project sounddevice rows to bounded, non-sensitive output metadata."""
    projected = []
    try:
        rows = iter(devices)
    except TypeError:
        return projected
    for index, row in enumerate(rows):
        if len(projected) >= MAX_INPUT_DEVICES:
            break
        if not isinstance(row, Mapping):
            continue
        channels = row.get("max_output_channels")
        if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        name = "".join(char for char in name if char.isprintable()).strip()[:MAX_DEVICE_NAME]
        if not name:
            continue
        rate = row.get("default_samplerate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 8000 <= rate <= 192000:
            rate = None
        else:
            rate = int(rate)
        projected.append({"output_id": str(index), "name": name,
                          "channels": min(channels, MAX_CHANNELS), "sample_rate": rate})
    return projected


def resolve_output_index(devices: Iterable[object], output_id: object) -> int | None:
    """Resolve only a currently available output row from an opaque catalog index."""
    if not isinstance(output_id, str) or not output_id.isascii() or not output_id.isdecimal():
        return None
    try:
        index = int(output_id)
        rows = list(devices)
    except (TypeError, ValueError):
        return None
    if str(index) != output_id or index >= len(rows):
        return None
    row = rows[index]
    if not isinstance(row, Mapping):
        return None
    channels = row.get("max_output_channels")
    if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
        return None
    return index
