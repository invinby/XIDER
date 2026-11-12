"""Passive local prerequisite detection for the Windows agent."""

from __future__ import annotations


def capability_snapshot(feature_status: dict[str, str], *, platform: str, location_capability, time_module) -> dict:
    """Build a passive privacy-capability snapshot without inferring OS grants."""
    items = []
    for capability_id, legacy_id, settings_uri in (
        ("screen_recording", "screenshot", None),
        ("camera", "webcam", "ms-settings:privacy-webcam"),
        ("microphone", "microphone", "ms-settings:privacy-microphone"),
        ("location", "geolocation", "ms-settings:privacy-location"),
        ("accessibility", None, None),
        ("file_access", None, None),
    ):
        prerequisite = feature_status.get(legacy_id) if legacy_id else None
        state = "needs_live_test"
        detail = "Permission cannot be queried directly; only an owner-authorized trial action confirms access."
        if prerequisite in {"dependency_missing", "unavailable"}:
            state = "unavailable"
            detail = "Required local tool or library is unavailable; OS permission was not queried."
        elif legacy_id and prerequisite not in {"permission_unverified", "device_unverified", "approximate"}:
            state = "unknown"
            detail = "Diagnostic prerequisites could not be determined; no permission grant inferred."
        if capability_id == "location":
            state, detail = location_capability()
        elif capability_id == "accessibility":
            detail = (
                "A macOS-style TCC Accessibility permission is unsupported on Windows; "
                "interactive session, integrity level and UAC may limit UI control. No UAC auto-consent."
            )
        elif capability_id == "file_access":
            detail = (
                "A blanket TCC file permission is unsupported on Windows; access is path-specific "
                "and depends on ACL/UAC. Only an authorized trial confirms access."
            )
        elif capability_id == "screen_recording" and state == "needs_live_test":
            detail = (
                "A TCC screen recording grant is unsupported on Windows; capture depends on "
                "the interactive session and a permitted trial action."
            )
        items.append({"id": capability_id, "state": state, "detail": detail, "settings_uri": settings_uri})
    return {
        "schema": 1,
        "platform": platform,
        "checked_at": time_module.strftime("%Y-%m-%dT%H:%M:%S+00:00", time_module.gmtime()),
        "items": items,
    }


def detect_features(psutil_module) -> dict:
    """Detect optional libraries without opening devices or requesting consent."""
    features = {
        "notify": True, "lock_screen": True, "power_control": True,
        "screenshot": False, "tts": False, "audio_control": False,
        "camera_api": False, "battery": False, "clipboard": False,
        "mic": False, "shell": True, "open_app": True,
    }
    try:
        from PIL import ImageGrab  # noqa: F401
        features["screenshot"] = True
    except Exception:
        pass
    try:
        import pyttsx3  # noqa: F401
        features["tts"] = True
    except Exception:
        pass
    try:
        import pycaw  # noqa: F401
        import comtypes  # noqa: F401
        features["audio_control"] = True
    except Exception:
        pass
    try:
        import cv2  # noqa: F401
        features["camera_api"] = True
    except Exception:
        pass
    try:
        features["battery"] = psutil_module.sensors_battery() is not None
    except Exception:
        pass
    try:
        import pyperclip  # noqa: F401
        features["clipboard"] = True
    except Exception:
        pass
    try:
        import sounddevice  # noqa: F401
        import soundfile  # noqa: F401
        features["mic"] = True
    except Exception:
        pass
    return features


def module_available(name: str, find_spec) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_feature_status(features: dict, module_available) -> dict[str, str]:
    """Report prerequisites; hardware and privacy settings remain unverified."""
    return {
        "shell": "supported",
        "open_app": "supported",
        "external_ip_watch": "supported",
        "geolocation": "approximate",
        "screenshot": "device_unverified" if features["screenshot"] else "dependency_missing",
        "webcam": "permission_unverified" if module_available("cv2") else "dependency_missing",
        "microphone": (
            "permission_unverified"
            if module_available("sounddevice") and module_available("soundfile")
            else "dependency_missing"
        ),
        "clipboard": "device_unverified" if features["clipboard"] else "dependency_missing",
        "battery": "supported" if features["battery"] else "unavailable",
    }
