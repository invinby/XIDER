from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

import screenshot_comparison


def _png(mark: bool = False, *, size=(320, 200)) -> bytes:
    image = Image.new("RGB", size, "white")
    if mark:
        ImageDraw.Draw(image).rectangle((40, 40, 120, 120), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=None)
    return output.getvalue()


def test_comparison_is_same_size_side_by_side_and_highlights_changed_pixels():
    before = _png()
    after = _png(True)
    before_bytes = before[:]
    after_bytes = after[:]

    result = screenshot_comparison.compare_screenshots(before, after)

    assert before == before_bytes and after == after_bytes
    with Image.open(io.BytesIO(result)) as comparison:
        assert comparison.format == "PNG"
        assert comparison.size == (652, 200)
        assert comparison.info == {}
        pixels = comparison.convert("RGB").tobytes()
        assert any(r > 170 and r > g * 1.5 and r > b * 1.5 for r, g, b in zip(pixels[0::3], pixels[1::3], pixels[2::3]))


def test_identical_screenshots_produce_no_red_difference_overlay():
    image = _png()
    with Image.open(io.BytesIO(screenshot_comparison.compare_screenshots(image, image))) as comparison:
        right = comparison.crop((332, 0, 652, 200)).convert("RGB")
        pixels = right.tobytes()
        assert not any(r > 180 and r > g * 1.5 and r > b * 1.5 for r, g, b in zip(pixels[0::3], pixels[1::3], pixels[2::3]))


def test_comparison_rejects_different_dimensions_and_oversized_sources():
    with pytest.raises(ValueError, match="same dimensions"):
        screenshot_comparison.compare_screenshots(_png(), _png(size=(300, 200)))
    with pytest.raises(ValueError, match="pixel limit"):
        screenshot_comparison.compare_screenshots(_png(size=(320, 200)), _png(size=(320, 200)), max_pixels=100)


def test_comparison_rejects_invalid_and_oversized_byte_inputs():
    with pytest.raises(ValueError, match="supported readable image"):
        screenshot_comparison.compare_screenshots(b"not an image", _png())
    with pytest.raises(ValueError, match="byte limit"):
        screenshot_comparison.compare_screenshots(b"x" * (24 * 1024 * 1024 + 1), _png())
