"""Media capture command handlers."""

from __future__ import annotations

import base64
import datetime
import os
import re


def camera_catalog(payload: dict, *, list_cameras, device_id, publish_response, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
        "feature_id": "D02-F11", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "opencv_camera_device_enumeration",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"cameras": []},
    }
    try:
        result["data"]["cameras"] = [
            {"camera_id": row["camera_id"], "name": row["name"]}
            for row in list_cameras()
        ]
        result["state"] = "supported" if result["data"]["cameras"] else "unavailable"
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "opencv_camera_enumeration_dependency_unavailable"
    except Exception:
        logger.exception("Camera enumeration failed")
        result["state"] = "unknown"
        result["data"] = None
    publish_response("camera_catalog", result)


def camera_series_snapshot(payload: dict, *, device_id, publish_response, finish_request, datetime_module, logger) -> None:
    request_id = payload.get("id")
    result = {
        "type": "camera_series_snapshot", "schema": "xider.camera-series.v1",
        "feature_id": "D02-F14", "device_id": device_id,
        "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
        "state": "unknown", "frame_count": None, "image": None, "checked_at": None,
    }
    try:
        import camera_series
        encoded, count = camera_series.capture_contact_sheet(
            payload.get("series_count"), payload.get("camera_id"),
            cancel_event=payload.get("_media_cancel_event"),
        )
        result.update({"state": "supported", "frame_count": count,
                       "image": base64.b64encode(encoded).decode("ascii")})
    except ValueError:
        result["state"] = "rejected"
    except camera_series.CameraSeriesCancelled:
        result["state"] = "cancelled"
    except Exception:
        logger.exception("Bounded camera series failed")
    result["checked_at"] = datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds")
    if isinstance(request_id, str):
        finish_request(request_id)
    publish_response("camera_series", result)


def media_history(payload: dict, *, device_id, publish_response, history_path, read_events, datetime_module, logger) -> None:
    request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
    limit = payload.get("limit", 20)
    result = {
        "type": "media_history", "schema": "xider.media-history.v1", "feature_id": "D02-F24",
        "device_id": device_id, "request_id": request_id, "state": "unknown",
        "source": "local_metadata_journal",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": None,
    }
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        result["detail"] = "invalid_limit"
    else:
        try:
            result["data"] = {"events": read_events(history_path, limit=limit)}
            result["state"] = "supported"
        except Exception:
            logger.exception("Не удалось прочитать локальную историю media-операций")
            result["detail"] = "history_unavailable"
    publish_response("media_history", result)


def capture_webcam(payload: dict, *, resolve_camera, device_id, publish_response, logger) -> None:
    """Capture one JPEG frame from the default or selected camera."""
    import cv2

    selected_id = payload.get("camera_id")
    try:
        if selected_id is None:
            cap = cv2.VideoCapture(0)
        else:
            selected = resolve_camera(selected_id)
            cap = cv2.VideoCapture(selected["index"], selected["backend"])
    except (ValueError, LookupError):
        publish_response("webcam", {
            "type": "webcam", "device_id": device_id, "image": None,
            "error": "camera_selection_stale",
        })
        return
    try:
        if not cap.isOpened():
            logger.error("Камера не найдена или нет доступа")
            publish_response("webcam", {"type": "webcam", "device_id": device_id, "image": None})
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        for _ in range(15):
            cap.read()
        ret, frame = cap.read()
        if not ret:
            logger.error("Не удалось получить кадр")
            publish_response("webcam", {"type": "webcam", "device_id": device_id, "image": None})
            return
        _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        encoded = base64.b64encode(buffer).decode("ascii")
        publish_response("webcam", {"type": "webcam", "device_id": device_id, "image": encoded})
        logger.info("Снимок вебки отправлен (%d байт)", len(buffer))
    finally:
        cap.release()


def camera_quality_snapshot(
    payload: dict, *, device_id, publish_response, analysis_source,
    analyze_frame, datetime_module, logger,
) -> None:
    """Analyze one explicitly requested camera frame locally; never send pixels."""
    request_id = payload.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        request_id = None
    result = {
        "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
        "feature_id": "D02-F13", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": analysis_source,
        "checked_at": None, "data": None,
    }
    cap = None
    frame = None
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            result["detail"] = "camera_unavailable_or_permission_unknown"
        else:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            ok, frame = cap.read()
            if ok and frame is not None:
                result["data"] = analyze_frame(frame, cv2)
                result["state"] = "supported"
            else:
                result["detail"] = "single_frame_capture_failed"
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "opencv_dependency_unavailable"
        result["detail"] = "camera_analysis_dependency_missing"
    except Exception:
        logger.exception("Camera quality analysis failed")
        result["detail"] = "camera_analysis_failed"
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                logger.debug("Camera release failed", exc_info=True)
        frame = None
        result["checked_at"] = datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds")
    publish_response("camera_quality", result)


def capture_microphone(
    payload: dict, *, device_id, publish_response, media_temp_module, logger,
) -> None:
    """Record the requested short audio clip and publish it as base64 OGG."""
    try:
        import sounddevice as sd
        import soundfile as sf
        if "input_device_id" in payload:
            import microphone_devices
            input_index = microphone_devices.resolve_input_index(sd.query_devices(), payload.get("input_device_id"))
            if input_index is None:
                publish_response("mic", {"type": "mic", "device_id": device_id,
                                          "audio": None, "state": "unavailable",
                                          "error": "selected_microphone_unavailable"})
                return
        else:
            input_index = None
        duration = int(payload.get("duration", 5))
        if duration <= 0 or duration > 60:
            duration = 5
        sample_rate = 44100
        logger.info("Запись микрофона %d сек @ %d Гц", duration, sample_rate)
        kwargs = {"samplerate": sample_rate, "channels": 1}
        if input_index is not None:
            kwargs["device"] = input_index
        recording = sd.rec(int(duration * sample_rate), **kwargs)
        sd.wait()
        temp_file = media_temp_module.create_media_temp_file(".ogg")
        temp_path = temp_file.name
        temp_file.close(retain_reservation=True)
        try:
            sf.write(temp_path, recording, sample_rate)
            temp_file.release()
            with open(temp_path, "rb") as stream:
                encoded_audio = base64.b64encode(stream.read()).decode("ascii")
        finally:
            os.unlink(temp_path)
            temp_file.release()
        publish_response("mic", {"type": "mic", "device_id": device_id, "audio": encoded_audio})
        logger.info("Аудиозапись отправлена")
    except Exception:
        logger.exception("Ошибка микрофона")
        publish_response("mic", {"type": "mic", "device_id": device_id, "audio": None,
                                  "state": "unknown", "error": "microphone_capture_failed"})


def microphone_preview(payload: dict, *, device_id, publish_response, datetime_module, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "microphone_preview", "schema": "xider.microphone-preview.v1",
        "feature_id": "D02-F20", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "local_playback_before_delivery", "audio": None,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        import sounddevice as sd
        import soundfile as sf
        import microphone_devices
        duration = payload.get("duration")
        audio = microphone_devices.preview_microphone_recording(
            sd, sf, input_device_id=payload.get("input_device_id"),
            output_device_id=payload.get("output_device_id"), duration_seconds=duration,
            input_device_name=payload.get("input_device_name"),
            output_device_name=payload.get("output_device_name"),
        )
        result["state"] = "previewed"
        result["duration_ms"] = duration * 1000
        result["audio"] = base64.b64encode(audio).decode("ascii")
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except ValueError as exc:
        result["state"] = "unavailable"
        allowed = {"selected_audio_device_unavailable", "selected_audio_device_changed",
                   "microphone_preview_duration_invalid", "microphone_preview_audio_invalid"}
        result["detail"] = str(exc) if str(exc) in allowed else "microphone_preview_failed"
    except Exception:
        logger.exception("Local microphone preview failed")
        result["state"] = "unknown"
        result["detail"] = "microphone_preview_failed"
    publish_response("microphone_preview", result)


def microphone_test(payload: dict, *, device_id, publish_response, logger) -> None:
    request_id = payload.get("id")
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
    result = {
        "type": "microphone_test", "schema": "xider.microphone-test.v1",
        "feature_id": "D02-F17", "device_id": device_id, "request_id": request_id,
        "state": "unknown", "source": "local_audio_roundtrip", "data": None,
    }
    try:
        import sounddevice as sd
        import soundfile as sf
        import microphone_devices
        data = microphone_devices.run_microphone_roundtrip(
            sd, sf, input_device_id=payload.get("input_device_id"),
            output_device_id=payload.get("output_device_id"),
            input_device_name=payload.get("input_device_name"),
            output_device_name=payload.get("output_device_name"),
        )
        result["state"] = data["state"]
        result["data"] = {"duration_ms": data["duration_ms"], "sample_rate": data["sample_rate"]}
    except ImportError:
        result["state"] = "unsupported"
        result["source"] = "sounddevice_dependency_unavailable"
    except ValueError as exc:
        result["state"] = "unavailable"
        result["detail"] = str(exc) if str(exc) in {"selected_audio_device_unavailable", "selected_audio_device_changed"} else "audio_roundtrip_failed"
    except Exception:
        logger.exception("Local microphone/output test failed")
        result["state"] = "unknown"
        result["detail"] = "audio_roundtrip_failed"
    publish_response("microphone_test", result)
