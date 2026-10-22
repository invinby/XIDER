"""Bounded side-by-side visual comparison for two owner-selected screenshots."""

from __future__ import annotations

import io
import warnings

from PIL import Image, ImageChops


MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_SIDE = 960
GUTTER = 12
DIFF_THRESHOLD = 22


def _decode_screenshot(data: bytes, *, max_pixels: int) -> Image.Image:
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("screenshot is empty or exceeds the byte limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as source:
                width, height = source.size
                if width < 1 or height < 1 or width * height > max_pixels:
                    raise ValueError("screenshot exceeds the pixel limit")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("animated screenshots are unsupported")
                source.load()
                result = source.convert("RGB")
                result.thumbnail((MAX_SIDE, MAX_SIDE), resample=Image.Resampling.LANCZOS)
                return result
    except ValueError:
        raise
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("screenshot is not a supported readable image") from exc


def compare_screenshots(
    before_bytes: bytes,
    after_bytes: bytes,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> bytes:
    """Create a bounded two-panel PNG; red marks changed pixels in the second panel."""
    if not isinstance(max_pixels, int) or max_pixels < 1:
        raise ValueError("invalid image pixel limit")
    before = _decode_screenshot(before_bytes, max_pixels=max_pixels)
    after = _decode_screenshot(after_bytes, max_pixels=max_pixels)
    if before.size != after.size:
        raise ValueError("screenshots must have the same dimensions for an honest comparison")

    difference = ImageChops.difference(before, after).convert("L")
    mask = difference.point(lambda value: 255 if value >= DIFF_THRESHOLD else 0)
    overlay = Image.new("RGBA", after.size, (255, 24, 24, 0))
    overlay.putalpha(mask.point(lambda value: 200 if value else 0))
    marked_after = Image.alpha_composite(after.convert("RGBA"), overlay).convert("RGB")

    width, height = before.size
    canvas = Image.new("RGB", (width * 2 + GUTTER, height), "#262d36")
    canvas.paste(before, (0, 0))
    canvas.paste(marked_after, (width + GUTTER, 0))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    result = output.getvalue()
    if not result or len(result) > MAX_IMAGE_BYTES:
        raise ValueError("comparison image exceeds the byte limit")
    return result
