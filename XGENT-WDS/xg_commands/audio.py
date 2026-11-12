"""Audio command handlers backed by platform-specific device adapters."""

from __future__ import annotations

import datetime
import importlib
import math
import re


def microphone_catalog(payload: dict, *, publish_response, device_id, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
        "feature_id": "D02-F15", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "sounddevice_query_devices",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"inputs": []},
    }
    try:
        sounddevice = importlib.import_module("sounddevice")
        microphone_devices = importlib.import_module("microphone_devices")
        result["data"]["inputs"] = microphone_devices.project_input_devices(sounddevice.query_devices())
        result["state"] = "supported" if result["data"]["inputs"] else "unavailable"
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except Exception:
        logger.exception("Microphone input enumeration failed")
        result["state"] = "unknown"
        result["data"] = None
    publish_response("microphone_catalog", result)


def microphone_level(payload: dict, *, publish_response, device_id, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "microphone_level", "schema": "xider.microphone-level.v1",
        "feature_id": "D02-F16", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "sounddevice_input_stream_level",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    try:
        sounddevice = importlib.import_module("sounddevice")
        meter = importlib.import_module("microphone_level")
        levels = meter.measure_input_level(sounddevice, payload.get("input_device_id"), duration_seconds=1.0)
        if levels is None:
            result["state"] = "unavailable"
            result["detail"] = "selected_microphone_unavailable"
        else:
            result["state"] = "supported"
            result["data"] = {**levels, "observation_ms": 1000}
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except Exception:
        logger.exception("Microphone input-level measurement failed")
        result["state"] = "unknown"
        result["detail"] = "microphone_level_measurement_failed"
    publish_response("microphone_level", result)


def microphone_balance(payload: dict, *, publish_response, device_id, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "microphone_balance", "schema": "xider.microphone-balance.v1",
        "feature_id": "D02-F21", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "sounddevice_stereo_input_rms",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    try:
        sounddevice = importlib.import_module("sounddevice")
        microphone_devices = importlib.import_module("microphone_devices")
        result["data"] = microphone_devices.measure_stereo_channel_balance(
            sounddevice, input_device_id=payload.get("input_device_id"),
            input_device_name=payload.get("input_device_name"),
        )
        result["data"].pop("state", None)
        result["state"] = "supported"
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except ValueError as exc:
        if str(exc) == "selected_stereo_microphone_unavailable":
            result["state"] = "unavailable"
            result["detail"] = str(exc)
        else:
            result["detail"] = "stereo_balance_measurement_failed"
    except Exception:
        logger.exception("Microphone stereo-balance measurement failed")
        result["detail"] = "stereo_balance_measurement_failed"
    publish_response("microphone_balance", result)


def switch_output(payload: dict, *, publish_response, device_id, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "audio_output_switch", "schema": "xider.audio-output-switch.v1",
        "feature_id": "D02-F18", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "data": None,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        sounddevice = importlib.import_module("sounddevice")
        output_control = importlib.import_module("audio_output_control")
        data = output_control.switch_system_output(
            sounddevice, output_id=payload.get("output_device_id"),
            output_name=payload.get("output_device_name"),
        )
        result["state"] = data.get("state", "unknown")
        if result["state"] in {"changed", "unchanged"}:
            result["data"] = {
                "output_name": data["output_name"],
                "previous_output_name": data["previous_output_name"],
            }
    except ImportError:
        result["state"] = "unsupported"
    except Exception:
        logger.exception("System audio output switch failed")
    publish_response("audio_output_switch", result)


def output_catalog(payload: dict, *, publish_response, device_id, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
        "feature_id": "D02-F19", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "sounddevice_query_devices",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"outputs": []},
    }
    try:
        sounddevice = importlib.import_module("sounddevice")
        microphone_devices = importlib.import_module("microphone_devices")
        result["data"]["outputs"] = microphone_devices.project_output_devices(sounddevice.query_devices())
        result["state"] = "supported" if result["data"]["outputs"] else "unavailable"
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except Exception:
        logger.exception("Audio output enumeration failed")
        result["state"] = "unknown"
        result["data"] = None
    publish_response("audio_output_catalog", result)


def set_master_volume(payload: dict, *, get_master_volume, publish_response, device_id, logger) -> None:
    """Set the master volume and report only the level observed on readback."""
    try:
        requested = payload.get("level", 50)
        if isinstance(requested, bool):
            raise ValueError("boolean volume")
        level = max(0, min(100, int(requested)))
    except (TypeError, ValueError, OverflowError):
        result = {
            "type": "volume_set", "device_id": device_id, "ok": False,
            "state": "failed", "level": None, "current": None,
            "source": "pycaw.IAudioEndpointVolume",
            "text": "⚠️ Не удалось изменить громкость: запрос содержит некорректное значение.",
        }
        publish_response("volume_set", result)
        publish_response("output", result)
        return

    volume = None
    setter_error = None
    try:
        volume = get_master_volume()
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    except Exception as exc:
        logger.warning("Ошибка установки громкости: %s", exc)
        setter_error = exc

    current = None
    try:
        if volume is None:
            volume = get_master_volume()
        observed = float(volume.GetMasterVolumeLevelScalar())
        if math.isfinite(observed) and 0.0 <= observed <= 1.0:
            current = round(observed * 100)
    except Exception as exc:
        logger.warning("Ошибка чтения фактической громкости: %s", exc)

    if current == level:
        ok, state = True, "confirmed"
        text = f"🎚 Громкость {current}%; уровень подтверждён системой."
    elif current is None:
        ok, state = False, "unknown"
        text = "⚠️ Запрос громкости отправлен, но система не вернула проверяемый уровень. Результат неизвестен."
    else:
        ok, state = False, "failed"
        text = f"⚠️ Запрошено {level}%, но система сообщила {current}%; изменение не подтверждено."
    if setter_error is not None and current != level and current is not None:
        logger.debug("Volume setter failed and readback differs from requested value: %s", setter_error)

    result = {
        "type": "volume_set",
        "device_id": device_id,
        "ok": ok,
        "state": state,
        "level": level,
        "current": current,
        "source": "pycaw.IAudioEndpointVolume",
        "text": text,
    }
    publish_response("volume_set", result)
    publish_response("output", result)
    logger.info("Громкость: %s", text)


def toggle_mute(*, get_master_volume, publish_response, device_id, logger) -> None:
    """Toggle mute and report success only when the device state changes."""
    source = "pycaw.IAudioEndpointVolume"
    try:
        volume = get_master_volume()
        previous_mute = bool(volume.GetMute())
    except Exception as exc:
        logger.warning("Не удалось прочитать состояние звука: %s", exc)
        result = {
            "type": "volume_toggle", "device_id": device_id, "ok": False,
            "state": "unknown", "muted": None, "source": source,
            "text": "⚠️ Не удалось прочитать состояние звука; переключение не выполнялось.",
        }
        publish_response("volume_toggle", result)
        publish_response("output", result)
        return

    setter_error = None
    try:
        volume.SetMute(not previous_mute, None)
    except Exception as exc:
        setter_error = exc
        logger.warning("Ошибка команды переключения звука: %s", exc)
    try:
        current_mute = bool(volume.GetMute())
    except Exception as exc:
        current_mute = None
        logger.warning("Не удалось проверить новое состояние звука: %s", exc)

    if current_mute is None:
        ok, state = False, "unknown"
        text = "⚠️ Запрос переключения звука отправлен, но система не подтвердила состояние. Результат неизвестен."
    elif current_mute != previous_mute:
        ok, state = True, "confirmed"
        text = "🔇 Звук выключен; состояние подтверждено системой." if current_mute else "🔊 Звук включён; состояние подтверждено системой."
    else:
        ok, state = False, "failed"
        text = "⚠️ Состояние звука не изменилось; переключение не выполнено."
    if setter_error is not None and current_mute == previous_mute:
        logger.debug("Mute setter failed and readback stayed unchanged: %s", setter_error)

    result = {
        "type": "volume_toggle",
        "device_id": device_id,
        "ok": ok,
        "state": state,
        "muted": current_mute,
        "source": source,
        "text": text,
    }
    publish_response("volume_toggle", result)
    publish_response("output", result)
    logger.info("Volume toggle state=%s", state)
