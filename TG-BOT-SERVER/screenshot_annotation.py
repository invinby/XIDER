"""Bounded, non-destructive annotations for owner-requested screenshot copies."""

from __future__ import annotations

import io
import math
import re
import warnings

from PIL import Image, ImageDraw, ImageFont


MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_ANNOTATION_CHARS = 240
_COORD = r"(?:0(?:\.\d{1,4})?|1(?:\.0{1,4})?)"
_ARROW = re.compile(rf"^arrow\s+({_COORD}),({_COORD}),({_COORD}),({_COORD})$", re.IGNORECASE)
_BOX = re.compile(rf"^box\s+({_COORD}),({_COORD}),({_COORD}),({_COORD})$", re.IGNORECASE)
_TEXT = re.compile(rf"^text\s+({_COORD}),({_COORD}):\s*(.{{1,200}})$", re.IGNORECASE | re.DOTALL)


def _parse_annotation(value: str) -> tuple[str, tuple[float, ...] | str]:
    if not isinstance(value, str) or len(value) > MAX_ANNOTATION_CHARS:
        raise ValueError("annotation must be one short instruction")
    value = value.strip()
    if match := _ARROW.fullmatch(value):
        coords = tuple(float(part) for part in match.groups())
        if coords[:2] == coords[2:]:
            raise ValueError("arrow endpoints must differ")
        return "arrow", coords
    if match := _BOX.fullmatch(value):
        x1, y1, x2, y2 = (float(part) for part in match.groups())
        if x1 >= x2 or y1 >= y2:
            raise ValueError("box corners must be ordered top-left to bottom-right")
        return "box", (x1, y1, x2, y2)
    if match := _TEXT.fullmatch(value):
        x, y, caption = match.groups()
        caption = caption.strip()
        if not caption or len(caption) > 200 or any(ord(char) < 32 and char not in "\t\n\r" for char in caption):
            raise ValueError("caption is empty, too long or contains control characters")
        return "text", (float(x), float(y), caption)
    raise ValueError("use arrow x1,y1,x2,y2; box x1,y1,x2,y2; or text x,y: caption (coordinates between 0 and 1)")


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate_screenshot(
    image_bytes: bytes,
    instruction: str,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> bytes:
    """Return an EXIF/metadata-free PNG copy with one bounded normalized mark."""
    kind, args = _parse_annotation(instruction)
    if not isinstance(image_bytes, bytes) or not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("screenshot is empty or exceeds the byte limit")
    if not isinstance(max_pixels, int) or max_pixels < 1:
        raise ValueError("invalid image dimensions limit")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes), formats=("PNG", "JPEG", "WEBP")) as source:
                width, height = source.size
                if width < 1 or height < 1 or width * height > max_pixels:
                    raise ValueError("screenshot dimensions exceed the pixel limit")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("animated images are not supported")
                source.load()
                copy = source.convert("RGB")
    except ValueError:
        raise
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("screenshot is not a supported readable image") from exc

    draw = ImageDraw.Draw(copy)
    scale = min(width, height)
    line_width = max(2, min(16, round(scale * 0.006)))
    color = (255, 45, 45)
    if kind in {"arrow", "box"}:
        x1, y1, x2, y2 = args  # type: ignore[misc]
        p1 = (round(x1 * (width - 1)), round(y1 * (height - 1)))
        p2 = (round(x2 * (width - 1)), round(y2 * (height - 1)))
        if kind == "box":
            draw.rectangle((*p1, *p2), outline=color, width=line_width)
        else:
            draw.line((p1, p2), fill=color, width=line_width)
            angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
            head = max(10, min(32, round(scale * 0.04)))
            left = (round(p2[0] - head * math.cos(angle - math.pi / 6)), round(p2[1] - head * math.sin(angle - math.pi / 6)))
            right = (round(p2[0] - head * math.cos(angle + math.pi / 6)), round(p2[1] - head * math.sin(angle + math.pi / 6)))
            draw.polygon((p2, left, right), fill=color)
    else:
        x, y, caption = args  # type: ignore[misc]
        origin = (round(x * (width - 1)), round(y * (height - 1)))
        font = _font(max(12, min(36, round(scale * 0.045))))
        bbox = draw.textbbox(origin, caption, font=font, stroke_width=1)
        padding = max(3, line_width)
        rect = (
            max(0, bbox[0] - padding), max(0, bbox[1] - padding),
            min(width - 1, bbox[2] + padding), min(height - 1, bbox[3] + padding),
        )
        draw.rectangle(rect, fill=(0, 0, 0))
        draw.text(origin, caption, font=font, fill=(255, 230, 0), stroke_width=1, stroke_fill=(0, 0, 0))

    output = io.BytesIO()
    copy.save(output, format="PNG", optimize=True)
    result = output.getvalue()
    if not result or len(result) > MAX_IMAGE_BYTES:
        raise ValueError("annotated copy exceeds the byte limit")
    return result
