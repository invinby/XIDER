"""Bounded, explicitly requested camera burst rendered as one in-memory contact sheet."""

from __future__ import annotations

import io
from threading import Event

from PIL import Image


MIN_FRAMES = 2
MAX_FRAMES = 4
MAX_JPEG_BYTES = 4 * 1024 * 1024
TILE = (320, 180)
GAP = 8


class CameraSeriesCancelled(Exception):
    """Raised when the owner cancels a series between camera reads."""


def capture_contact_sheet(
    frame_count: int, camera_id: str | None = None, *, cancel_event: Event | None = None,
) -> tuple[bytes, int]:
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or not MIN_FRAMES <= frame_count <= MAX_FRAMES:
        raise ValueError("frame_count_out_of_range")
    if cancel_event is not None and cancel_event.is_set():
        raise CameraSeriesCancelled("media_request_cancelled")

    import cv2
    import camera_devices

    if camera_id is None:
        cap = cv2.VideoCapture(0)
    else:
        selected = camera_devices.resolve_camera(camera_id)
        cap = cv2.VideoCapture(selected["index"], selected["backend"])

    try:
        if not cap.isOpened():
            raise RuntimeError("camera_unavailable")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        frames = []
        for _ in range(frame_count):
            if cancel_event is not None and cancel_event.is_set():
                raise CameraSeriesCancelled("media_request_cancelled")
            ok, frame = cap.read()
            if cancel_event is not None and cancel_event.is_set():
                raise CameraSeriesCancelled("media_request_cancelled")
            if not ok or frame is None or getattr(frame, "ndim", 0) != 3 or frame.shape[2] < 3:
                raise RuntimeError("camera_frame_unavailable")
            image = Image.fromarray(frame[:, :, :3][:, :, ::-1]).convert("RGB")
            image.thumbnail(TILE, resample=Image.Resampling.LANCZOS)
            tile = Image.new("RGB", TILE, "#171c24")
            tile.paste(image, ((TILE[0] - image.width) // 2, (TILE[1] - image.height) // 2))
            frames.append(tile)

        columns = 2
        rows = (frame_count + columns - 1) // columns
        width = columns * TILE[0] + (columns + 1) * GAP
        height = rows * TILE[1] + (rows + 1) * GAP
        sheet = Image.new("RGB", (width, height), "#262d36")
        for index, frame in enumerate(frames):
            if cancel_event is not None and cancel_event.is_set():
                raise CameraSeriesCancelled("media_request_cancelled")
            x = GAP + (index % columns) * (TILE[0] + GAP)
            y = GAP + (index // columns) * (TILE[1] + GAP)
            sheet.paste(frame, (x, y))
        output = io.BytesIO()
        sheet.save(output, format="JPEG", quality=72, optimize=True)
        encoded = output.getvalue()
        if not encoded or len(encoded) > MAX_JPEG_BYTES:
            raise RuntimeError("camera_contact_sheet_too_large")
        return encoded, frame_count
    finally:
        cap.release()
