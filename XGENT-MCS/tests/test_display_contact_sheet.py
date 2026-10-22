from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import display_contact_sheet as capture
import xgent_mcs as agent


def test_logical_backing_resolution_report_is_sanitized_and_compares_scale():
    rows = [
        {"id": "display-1", "width": 1470, "height": 956, "primary": True, "bounds": (0, 0, 1470, 956), "native_id": 1},
    ]
    result = capture.build_resolution_report(rows, {
        "display-1": (2940, 1912),
    }, source="macos_coregraphics_pixels")

    assert result == {
        "source": "macos_coregraphics_pixels",
        "displays": [
            {"id": "display-1", "logical_width": 1470, "logical_height": 956,
             "backing_width": 2940, "backing_height": 1912, "scale_x": 2.0, "scale_y": 2.0, "primary": True},
        ],
    }
    assert "native_id" not in str(result) and "bounds" not in str(result)


def test_logical_backing_resolution_report_rejects_incomplete_or_oversized_data():
    with pytest.raises(ValueError, match="dimensions"):
        capture.build_resolution_report([
            {"id": "display-1", "width": 0, "height": 720, "primary": True},
        ], {"display-1": (2560, 1440)}, source="macos_coregraphics_pixels")
    with pytest.raises(ValueError, match="backing"):
        capture.build_resolution_report([
            {"id": "display-1", "width": 1280, "height": 720, "primary": True},
        ], {}, source="macos_coregraphics_pixels")


def test_agent_returns_correlated_logical_backing_resolution_report(monkeypatch):
    sent = []
    report = {"topology_id": "a" * 16, "source": "macos_coregraphics_pixels", "displays": [
        {"id": "display-1", "logical_width": 1470, "logical_height": 956,
         "backing_width": 2940, "backing_height": 1912, "scale_x": 2.0, "scale_y": 2.0, "primary": True},
    ]}
    monkeypatch.setattr(capture, "get_resolution_report", lambda *_args: report)
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_resolution({"id": "resolution-ticket", "display_id": "display-1", "topology_id": "a" * 16})

    assert "display_resolution" in agent.SUPPORTED_COMMANDS
    topic, result = sent[0]
    assert topic == "display_resolution"
    assert result["feature_id"] == "D02-F10"
    assert result["request_id"] == "resolution-ticket"
    assert result["data"] == report
    assert "device_name" not in str(result) and "native_id" not in str(result)


def test_macos_resolution_query_maps_private_ids_to_sanitized_logical_backing_rows(monkeypatch):
    rows = [{"id": "display-1", "width": 1470, "height": 956, "primary": True,
             "bounds": (0, 0, 1470, 956), "native_id": 12345}]
    quartz = SimpleNamespace(CGDisplayPixelsWide=lambda native_id: 2940 if native_id == 12345 else 0,
                             CGDisplayPixelsHigh=lambda native_id: 1912 if native_id == 12345 else 0)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(capture, "_normalized_displays", lambda: rows)

    result = capture.get_resolution_report("display-1", capture._topology_id(rows))

    assert result["topology_id"] and result["displays"][0]["backing_width"] == 2940
    assert "native_id" not in str(result) and "12345" not in str(result)


def test_selection_is_explicit_ordered_and_bound_to_current_topology():
    catalog = [
        {"id": "display-1", "width": 800, "height": 600, "primary": True},
        {"id": "display-2", "width": 1920, "height": 1080, "primary": False},
    ]

    chosen = capture.validate_selection(catalog, ["display-2"], "topology-a", "topology-a")

    assert chosen == [catalog[1]]
    with pytest.raises(ValueError, match="topology"):
        capture.validate_selection(catalog, ["display-2"], "topology-a", "topology-b")
    with pytest.raises(ValueError, match="selection"):
        capture.validate_selection(catalog, ["display-2", "display-2"], "topology-a", "topology-a")
    with pytest.raises(ValueError, match="selection"):
        capture.validate_selection(catalog, ["display-3"], "topology-a", "topology-a")


def test_contact_sheet_contains_only_selected_images_and_is_bounded():
    red = Image.new("RGB", (1600, 900), "red")
    blue = Image.new("RGB", (640, 480), "blue")
    try:
        encoded, manifest = capture.build_contact_sheet([
            {"id": "display-2", "image": blue, "primary": False},
        ])
    finally:
        red.close()
        blue.close()

    with Image.open(io.BytesIO(encoded)) as sheet:
        assert sheet.format == "JPEG"
        assert sheet.width <= capture.MAX_SHEET_WIDTH
        assert sheet.height <= capture.MAX_SHEET_HEIGHT
        pixel = sheet.getpixel((sheet.width // 2, min(sheet.height - 1, 48)))[:3]
        assert pixel[2] > 220 and pixel[0] < 40 and pixel[1] < 40
    assert manifest == [{"id": "display-2", "width": 640, "height": 480, "primary": False}]
    assert len(encoded) <= capture.MAX_SHEET_BYTES


def test_contact_sheet_rejects_empty_or_oversized_selection():
    with pytest.raises(ValueError, match="selection"):
        capture.build_contact_sheet([])
    image = Image.new("RGB", (2, 2), "black")
    try:
        with pytest.raises(ValueError, match="selection"):
            capture.build_contact_sheet([
                {"id": f"display-{index}", "image": image, "primary": False}
                for index in range(capture.MAX_SELECTED_DISPLAYS + 1)
            ])
    finally:
        image.close()


def test_catalog_exposes_no_physical_coordinates_and_topology_is_repeatable(monkeypatch):
    displays = [
        {"bounds": (0, 0, 800, 600), "primary": True},
        {"bounds": (-1920, 0, 0, 1080), "primary": False},
    ]
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: displays)

    first = capture.get_display_catalog()
    second = capture.get_display_catalog()

    assert first == second
    assert len(first["topology_id"]) == 16
    assert first["displays"] == [
        {"id": "display-1", "width": 800, "height": 600, "primary": True},
        {"id": "display-2", "width": 1920, "height": 1080, "primary": False},
    ]


def test_capture_rereads_topology_and_never_captures_unselected(monkeypatch):
    displays = [
        {"bounds": (0, 0, 800, 600), "primary": True},
        {"bounds": (800, 0, 1440, 900), "primary": False},
    ]
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: displays)
    calls = []

    def capture_one(display):
        calls.append(display["id"])
        return Image.new("RGB", (display["width"], display["height"]), "blue")

    monkeypatch.setattr(capture, "_capture_display", capture_one)
    catalog = capture.get_display_catalog()
    encoded, manifest = capture.capture_contact_sheet(["display-2"], catalog["topology_id"])

    assert calls == ["display-2"]
    assert manifest == [{"id": "display-2", "width": 640, "height": 900, "primary": False}]
    assert encoded.startswith(b"\xff\xd8\xff")


def test_capture_rejects_stale_catalog_without_touching_screens(monkeypatch):
    displays = [{"bounds": (0, 0, 800, 600), "primary": True}]
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: displays)
    monkeypatch.setattr(capture, "_capture_display", lambda *_: pytest.fail("must not capture stale topology"))

    with pytest.raises(ValueError, match="topology"):
        capture.capture_contact_sheet(["display-1"], "old-topology-id")


def test_capture_display_region_crops_only_requested_rectangle_and_rejects_out_of_bounds(monkeypatch):
    displays = [{"bounds": (0, 0, 8, 6), "primary": True, "native_id": 1}]
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: displays)
    monkeypatch.setattr(capture, "_capture_display", lambda _display: Image.new("RGB", (8, 6), "red"))
    catalog = capture.get_display_catalog()

    encoded, manifest = capture.capture_display_region(
        "display-1", catalog["topology_id"], {"x": 2, "y": 1, "width": 3, "height": 2}
    )

    with Image.open(io.BytesIO(encoded)) as image:
        assert image.size == (3, 2)
        assert image.getpixel((0, 0))[:3] == (255, 0, 0)
    assert manifest == {"id": "display-1", "x": 2, "y": 1, "width": 3, "height": 2}
    with pytest.raises(ValueError, match="region"):
        capture.capture_display_region(
            "display-1", catalog["topology_id"], {"x": 7, "y": 1, "width": 3, "height": 2}
        )


def test_capture_checks_combined_pixel_limit_before_reading_selected_displays(monkeypatch):
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: [
        {"bounds": (index * 7000, 0, index * 7000 + 6000, 6000), "primary": index == 0, "native_id": index + 1}
        for index in range(2)
    ])
    monkeypatch.setattr(capture, "_capture_display", lambda *_: pytest.fail("oversized selection must be rejected before capture"))
    catalog = capture.get_display_catalog()
    with pytest.raises(ValueError, match="pixel limit"):
        capture.capture_contact_sheet(["display-1", "display-2"], catalog["topology_id"])


def test_single_display_capture_is_full_resolution_and_rejects_a_stale_catalog(monkeypatch):
    displays = [
        {"bounds": (0, 0, 800, 600), "primary": True, "native_id": 11},
        {"bounds": (800, 0, 1440, 900), "primary": False, "native_id": 12},
    ]
    monkeypatch.setattr(capture, "_enumerate_display_bounds", lambda: displays)
    calls = []

    def capture_one(display):
        calls.append(display["id"])
        return Image.new("RGB", (display["width"], display["height"]), "blue")

    monkeypatch.setattr(capture, "_capture_display", capture_one)
    catalog = capture.get_display_catalog()
    encoded, manifest = capture.capture_single_display("display-2", catalog["topology_id"])

    assert calls == ["display-2"]
    assert manifest == {"id": "display-2", "width": 640, "height": 900, "primary": False}
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.format == "PNG"
        assert image.size == (640, 900)
        assert image.getpixel((320, 450)) == (0, 0, 255)

    with pytest.raises(ValueError, match="topology"):
        capture.capture_single_display("display-2", "old-topology-id")
    assert calls == ["display-2"]


def test_agent_advertises_and_returns_correlated_catalog(monkeypatch):
    sent = []
    monkeypatch.setattr(capture, "get_display_catalog", lambda: {
        "topology_id": "a" * 16,
        "displays": [{"id": "display-1", "width": 800, "height": 600, "primary": True}],
    })
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_catalog({"id": "catalog-ticket"})

    assert "display_catalog" in agent.SUPPORTED_COMMANDS
    topic, result = sent[0]
    assert topic == "display_catalog"
    assert result["feature_id"] == "D02-F06"
    assert result["request_id"] == "catalog-ticket"
    assert result["data"]["displays"][0]["id"] == "display-1"
    assert "bounds" not in str(result)


def test_agent_returns_only_selected_contact_sheet(monkeypatch):
    sent = []
    png = b"\xff\xd8\xffcontact-sheet"
    manifest = [{"id": "display-2", "width": 640, "height": 480, "primary": False}]
    monkeypatch.setattr(capture, "capture_contact_sheet", lambda selected, topology: (png, manifest))
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_contact_sheet({"id": "sheet-ticket", "selected_displays": ["display-2"], "topology_id": "a" * 16})

    assert "display_contact_sheet" in agent.SUPPORTED_COMMANDS
    topic, result = sent[0]
    assert topic == "display_contact_sheet"
    assert result["request_id"] == "sheet-ticket"
    assert result["data"]["displays"] == manifest
    assert result["image"] == __import__("base64").b64encode(png).decode("ascii")


def test_agent_returns_correlated_owner_window_catalog_and_selected_image(monkeypatch):
    import base64
    import window_capture

    sent = []
    catalog = {"catalog_id": "a" * 16, "windows": [{"id": "window-1", "title": "Editor", "app": "Editor", "width": 640, "height": 480}]}
    monkeypatch.setattr(window_capture, "get_window_catalog", lambda: catalog)
    monkeypatch.setattr(window_capture, "capture_window", lambda window_id, token: (b"\x89PNG\r\n\x1a\nimage", {"id": window_id, "title": "Editor", "app": "Editor", "width": 640, "height": 480}))
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_window_catalog({"id": "window-list-1"})
    client._do_window_screenshot({"id": "window-shot-1", "window_id": "window-1", "catalog_id": "a" * 16})

    assert {"window_catalog", "window_screenshot"} <= set(agent.SUPPORTED_COMMANDS)
    assert sent[0][1]["feature_id"] == "D02-F02"
    assert sent[0][1]["request_id"] == "window-list-1"
    assert sent[0][1]["data"] == catalog
    assert sent[1][1]["request_id"] == "window-shot-1"
    assert sent[1][1]["data"]["id"] == "window-1"
    assert sent[1][1]["image"] == base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode("ascii")


def test_agent_returns_correlated_full_resolution_single_display(monkeypatch):
    sent = []
    image = b"\x89PNG\r\n\x1a\nselected-display"
    manifest = {"id": "display-2", "width": 640, "height": 900, "primary": False}
    monkeypatch.setattr(capture, "capture_single_display", lambda display, topology: (image, manifest))
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_screenshot({"id": "display-ticket", "display_id": "display-2", "topology_id": "a" * 16})

    assert "display_screenshot" in agent.SUPPORTED_COMMANDS
    topic, result = sent[0]
    assert topic == "display_screenshot"
    assert result["feature_id"] == "D02-F01"
    assert result["request_id"] == "display-ticket"
    assert result["data"] == manifest
    assert result["image"] == __import__("base64").b64encode(image).decode("ascii")


def test_agent_returns_correlated_selected_region_image(monkeypatch):
    sent = []
    manifest = {"id": "display-1", "x": 2, "y": 1, "width": 3, "height": 2}
    monkeypatch.setattr(capture, "capture_display_region", lambda *_args: (b"png-region", manifest))
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_region_screenshot({
        "id": "region-ticket", "display_id": "display-1", "topology_id": "a" * 16,
        "region": {"x": 2, "y": 1, "width": 3, "height": 2},
    })

    assert "display_region_screenshot" in agent.SUPPORTED_COMMANDS
    topic, result = sent[0]
    assert topic == "display_region_screenshot"
    assert result["feature_id"] == "D02-F03"
    assert result["request_id"] == "region-ticket"
    assert result["data"] == manifest
    assert result["image"] == __import__("base64").b64encode(b"png-region").decode("ascii")


def test_agent_rejects_bad_topology_without_capture(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    monkeypatch.setattr(capture, "capture_single_display", lambda *_: pytest.fail("invalid topology must not capture"))
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_display_screenshot({"id": "display-ticket", "display_id": "display-2", "topology_id": "bad"})
    assert sent[0][0] == "display_screenshot"
    assert sent[0][1]["state"] == "invalid_request"


def test_contact_sheet_requires_interactive_console_session(monkeypatch):
    client = object.__new__(agent.XgentClient)
    responses = []
    client._publish_response = lambda topic, result: responses.append((topic, result)) or True
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: False)
    monkeypatch.setattr(capture, "capture_contact_sheet", lambda *_: pytest.fail("must not capture without console session"))
    client._do_display_contact_sheet({"id": "sheet-ticket", "selected_displays": ["display-1"], "topology_id": "a" * 16})
    assert responses[-1][1]["state"] == "session_unavailable"
