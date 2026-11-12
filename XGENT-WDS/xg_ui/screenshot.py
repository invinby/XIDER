"""Windows screenshot capture and interactive-desktop probes."""

from __future__ import annotations

import ctypes
import logging
import threading
from concurrent.futures import Future


def bounded_screen_grab(capture_lock, timeout: float, logger: logging.Logger):
    """Capture all screens with one in-flight native call and a bounded wait."""
    from PIL import ImageGrab

    if not capture_lock.acquire(blocking=False):
        raise TimeoutError("Previous native screen capture is still running")
    result = Future()

    def capture():
        try:
            result.set_result(ImageGrab.grab(all_screens=True))
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            capture_lock.release()

    def discard_late_image(completed):
        if completed.exception() is None:
            image = completed.result()
            if image is not None:
                try:
                    image.close()
                except Exception:
                    logger.warning("Could not close late screenshot")

    try:
        threading.Thread(target=capture, name="xider-screen-capture", daemon=True).start()
    except BaseException:
        capture_lock.release()
        raise
    try:
        return result.result(timeout=timeout)
    except TimeoutError:
        result.add_done_callback(discard_late_image)
        raise


def screenshot_session_available() -> bool:
    """Read-only check that the current process can access the input desktop."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        try:
            return True
        finally:
            user32.CloseDesktop(desktop)
    except (OSError, AttributeError):
        return False
