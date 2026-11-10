import pytest

import window_capture as capture


def _rows():
    return [
        {"native_id": 412, "pid": 77, "title": "Editor\n draft", "app": "editor.exe", "bounds": (10, 20, 810, 620)},
        {"native_id": 991, "pid": 88, "title": "Browser", "app": "browser.exe", "bounds": (20, 30, 1020, 730)},
    ]


def test_catalog_exposes_only_bounded_owner_selectable_metadata():
    result = capture.build_window_catalog(_rows())

    assert len(result["catalog_id"]) == 16
    assert [row["id"] for row in result["windows"]] == ["window-1", "window-2"]
    assert result["windows"][0] == {
        "id": "window-1", "title": "Browser", "app": "browser.exe", "width": 1000, "height": 700,
    }
    assert "native_id" not in str(result)
    assert "pid" not in str(result)
    assert "bounds" not in str(result)


def test_catalog_is_capped_and_rejects_duplicate_or_invalid_native_rows():
    rows = [{**row, "native_id": row["native_id"] + index * 10000, "pid": row["pid"] + index * 10000}
            for index in range(30) for row in _rows()]
    assert len(capture.build_window_catalog(rows)["windows"]) <= capture.MAX_WINDOWS
    with pytest.raises(ValueError):
        capture.build_window_catalog([_rows()[0], _rows()[0]])
    invalid = {**_rows()[0], "bounds": (0, 0, 0, 600)}
    with pytest.raises(ValueError):
        capture.build_window_catalog([invalid])


def test_window_catalog_token_detects_stale_selection_and_maps_only_opaque_id():
    catalog = capture.build_window_catalog(_rows())
    native_id = capture.resolve_window(_rows(), catalog["windows"][0]["id"], catalog["catalog_id"])
    assert native_id == 991

    with pytest.raises(ValueError, match="catalog"):
        capture.resolve_window(_rows(), "window-2", "0" * 16)
    with pytest.raises(ValueError):
        capture.resolve_window(_rows(), "window-99", catalog["catalog_id"])


def test_image_encoder_returns_bounded_metadata_free_png(monkeypatch):
    from PIL import Image

    image = Image.new("RGB", (32, 24), "teal")
    encoded, manifest = capture.encode_window_image(image, "window-1", "Editor", "editor.exe")
    assert encoded.startswith(b"\x89PNG\r\n\x1a\n")
    assert manifest == {"id": "window-1", "title": "Editor", "app": "editor.exe", "width": 32, "height": 24}
    assert image.getpixel((0, 0)) == (0, 128, 128)
    image.close()

    monkeypatch.setattr(capture, "MAX_WINDOW_PIXELS", 10)
    oversized = Image.new("RGB", (4, 4), "red")
    with pytest.raises(ValueError, match="pixel"):
        capture.encode_window_image(oversized, "window-1", "x", "x")
    oversized.close()
