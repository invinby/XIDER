"""In-memory, single-frame camera diagnostics; image content is never returned."""

ANALYSIS_SOURCE = "opencv_laplacian_local"


def analyze_frame(frame, cv2_module=None) -> dict:
    if cv2_module is None:
        import cv2 as cv2_module
    if frame is None or getattr(frame, "size", 0) <= 0:
        raise ValueError("empty camera frame")
    shape = getattr(frame, "shape", ())
    if len(shape) < 2 or not 1 <= int(shape[0]) <= 4320 or not 1 <= int(shape[1]) <= 7680:
        raise ValueError("unsupported camera frame dimensions")
    gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    pixels = int(width) * int(height)
    if pixels <= 0:
        raise ValueError("empty camera frame")
    laplacian = cv2_module.Laplacian(gray, cv2_module.CV_64F)
    mean_luma = cv2_module.mean(gray)[0]
    dark = cv2_module.countNonZero(cv2_module.inRange(gray, 0, 15))
    bright = cv2_module.countNonZero(cv2_module.inRange(gray, 240, 255))
    sharpness = float(laplacian.var())
    values = (sharpness, float(mean_luma), float(dark), float(bright))
    if any(value != value or value in (float("inf"), float("-inf")) for value in values):
        raise ValueError("invalid camera analysis")
    return {
        "width_px": int(width),
        "height_px": int(height),
        "sharpness_variance": round(sharpness, 2),
        "mean_luma": round(float(mean_luma), 1),
        "dark_clipped_percent": round(float(dark) * 100 / pixels, 1),
        "bright_clipped_percent": round(float(bright) * 100 / pixels, 1),
    }
