"""Ask macOS once for every consent this app can hold.

Screen capture and the media devices are requested from the agent process
itself, so the resulting TCC entries belong to the installed bundle instead of
a throw-away shell. Requests are best effort: a missing framework or an
already-decided permission is simply skipped. Nothing here reads user data.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import threading

_STATE_AUTHORIZED = 3
_STATE_NOT_DETERMINED = 0


def _screen_recording() -> bool:
    try:
        coregraphics = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        preflight = coregraphics.CGPreflightScreenCaptureAccess
        preflight.restype = ctypes.c_bool
        if preflight():
            return True
        request = coregraphics.CGRequestScreenCaptureAccess
        request.restype = ctypes.c_bool
        return bool(request())
    except (OSError, AttributeError):
        return False


def _media_access(media_type: str) -> bool:
    try:
        import AVFoundation
        from Foundation import NSDate, NSRunLoop
    except ImportError:
        return False
    try:
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media_type)
        if status == _STATE_AUTHORIZED:
            return True
        if status != _STATE_NOT_DETERMINED:
            return False
        finished = threading.Event()
        outcome: dict[str, bool] = {}

        def _handler(granted: bool) -> None:
            outcome["granted"] = bool(granted)
            finished.set()

        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            media_type, _handler
        )
        for _ in range(600):
            if finished.is_set():
                break
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        return bool(outcome.get("granted"))
    except Exception:  # noqa: BLE001 - a failed consent must not stop the agent
        return False


def _photos_access() -> bool:
    try:
        import Photos
        from Foundation import NSDate, NSRunLoop
    except ImportError:
        return False
    try:
        status = Photos.PHPhotoLibrary.authorizationStatus()
        if status == _STATE_AUTHORIZED:
            return True
        if status != _STATE_NOT_DETERMINED:
            return False
        finished = threading.Event()
        outcome: dict[str, bool] = {}

        def _handler(granted) -> None:
            outcome["granted"] = int(granted) == _STATE_AUTHORIZED
            finished.set()

        Photos.PHPhotoLibrary.requestAuthorization_(_handler)
        for _ in range(600):
            if finished.is_set():
                break
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        return bool(outcome.get("granted"))
    except Exception:  # noqa: BLE001
        return False


def warm_up() -> dict[str, bool]:
    """Trigger the system consent dialogs and report what was granted."""
    results = {
        "screen_recording": _screen_recording(),
        "camera": _media_access("vide"),
        "microphone": _media_access("soun"),
        "photos": _photos_access(),
    }
    return results
