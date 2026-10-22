from __future__ import annotations

import io

import pytest
from PIL import Image

import screenshot_annotation


def _png() -> bytes:
    image = Image.new("RGB", (320, 200), "white")
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=None)
    return output.getvalue()


@pytest.mark.parametrize("annotation", [
    "arrow 0.1,0.1,0.8,0.8",
    "box 0.1,0.1,0.8,0.8",
    "text 0.1,0.1: проверь этот участок",
])
def test_annotation_returns_a_distinct_same_size_copy_without_mutating_source(annotation):
    original = _png()
    before = original[:]

    annotated = screenshot_annotation.annotate_screenshot(original, annotation)

    assert original == before
    assert annotated != original
    with Image.open(io.BytesIO(original)) as source, Image.open(io.BytesIO(annotated)) as result:
        assert result.size == source.size == (320, 200)
        assert result.format == "PNG"
        assert result.info == {}
        assert result.convert("RGB").tobytes() != source.convert("RGB").tobytes()


@pytest.mark.parametrize("annotation", [
    "erase 0.1,0.1,0.8,0.8",
    "arrow 0.1,0.1,1.5,0.8",
    "box nan,0.1,0.8,0.8",
    "text 0.1,0.1: ",
    "text 0.1,0.1: " + ("x" * 201),
    "arrow 0.1,0.1,0.8,0.8; box 0.2,0.2,0.3,0.3",
])
def test_annotation_rejects_unknown_unbounded_or_ambiguous_input(annotation):
    with pytest.raises(ValueError):
        screenshot_annotation.annotate_screenshot(_png(), annotation)


def test_annotation_rejects_oversized_image_before_decoding_allocation():
    with pytest.raises(ValueError, match="dimensions"):
        screenshot_annotation.annotate_screenshot(_png(), "box 0,0,1,1", max_pixels=100)
