from types import SimpleNamespace
import importlib
import importlib.util
import base64
import io

import pytest
import numpy as np
import sys
from threading import Event
from PIL import Image

import xgent_mcs as mcs


def _camera_devices():
    assert importlib.util.find_spec("camera_devices") is not None
    return importlib.import_module("camera_devices")


def test_camera_catalog_exposes_bounded_labels_and_opaque_ids(monkeypatch):
    camera_devices = _camera_devices()
    infos = [
        SimpleNamespace(index=0, backend=1200, name="FaceTime Camera", path="private-unique-id", vid=None, pid=None),
        SimpleNamespace(index=1, backend=1200, name="USB\nCamera", path="other-private-id", vid=56, pid=78),
    ]
    monkeypatch.setattr(camera_devices, "_enumerate_platform_cameras", lambda: infos)

    rows = camera_devices.list_cameras()

    assert [row["name"] for row in rows] == ["FaceTime Camera", "USB Camera"]
    assert len({row["camera_id"] for row in rows}) == 2
    assert all(len(row["camera_id"]) == 16 for row in rows)
    assert all("path" not in row and "vid" not in row and "pid" not in row for row in rows)


def test_camera_selection_resolves_only_current_catalog_entries():
    camera_devices = _camera_devices()
    rows = [{"camera_id": "a" * 16, "name": "FaceTime Camera", "index": 0, "backend": 1200}]

    assert camera_devices.resolve_camera("a" * 16, rows) == rows[0]
    with pytest.raises(LookupError):
        camera_devices.resolve_camera("b" * 16, rows)
    with pytest.raises(ValueError):
        camera_devices.resolve_camera("0; rm -rf", rows)


def test_camera_catalog_action_publishes_a_correlated_privacy_limited_result(monkeypatch):
    camera_devices = _camera_devices()
    monkeypatch.setattr(camera_devices, "list_cameras", lambda: [{"camera_id": "a" * 16, "name": "FaceTime Camera", "index": 0, "backend": 1200}])
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_camera_catalog({"id": "catalog-ticket"})

    topic, result = responses[0]
    assert topic == "camera_catalog"
    assert result["schema"] == "xider.camera-catalog.v1"
    assert result["feature_id"] == "D02-F11"
    assert result["request_id"] == "catalog-ticket"
    assert result["state"] == "supported"
    assert result["data"] == {"cameras": [{"camera_id": "a" * 16, "name": "FaceTime Camera"}]}


def test_webcam_series_returns_bounded_in_memory_contact_sheet(monkeypatch):
    class Capture:
        released = False
        reads = 0

        def isOpened(self):
            return True

        def set(self, *_args):
            return True

        def read(self):
            self.reads += 1
            shade = (40 * self.reads) % 256
            return True, np.full((24, 32, 3), shade, dtype=np.uint8)

        def release(self):
            self.released = True

    capture = Capture()
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        VideoCapture=lambda *_args: capture,
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        IMWRITE_JPEG_QUALITY=5, imencode=lambda *_args: (True, b"jpeg"),
    ))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_camera_series({"id": "series-ticket", "series_count": 3})

    topic, result = responses[0]
    assert topic == "camera_series"
    assert result["type"] == "camera_series_snapshot"
    assert result.get("schema") == "xider.camera-series.v1"
    assert result["feature_id"] == "D02-F14"
    assert result["request_id"] == "series-ticket"
    assert result["state"] == "supported"
    assert result["frame_count"] == 3
    assert isinstance(result["image"], str) and result["image"]
    with Image.open(io.BytesIO(base64.b64decode(result["image"], validate=True))) as sheet:
        assert sheet.format == "JPEG"
        assert sheet.size == (664, 384)
        sheet.load()
    assert capture.reads == 3 and capture.released


def test_webcam_series_rejects_out_of_range_frame_count_before_camera_open(monkeypatch):
    opened = []
    class Capture:
        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        VideoCapture=lambda *_args: (opened.append(True), Capture())[1],
    ))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_camera_series({"id": "series-ticket", "series_count": 5})

    assert opened == []
    assert responses[0][0] == "camera_series"
    assert responses[0][1]["type"] == "camera_series_snapshot"
    assert responses[0][1].get("schema") == "xider.camera-series.v1"
    assert responses[0][1]["state"] == "rejected"


def test_camera_series_cancellation_returns_no_frame_payload(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))
    cancel_event = Event()
    cancel_event.set()

    client._do_camera_series({"id": "series-cancelled", "series_count": 4,
                              "_media_cancel_event": cancel_event})

    assert responses[0][0] == "camera_series"
    assert responses[0][1]["state"] == "cancelled"
    assert responses[0][1]["image"] is None
    assert responses[0][1]["frame_count"] is None


def test_camera_series_stops_before_next_frame_when_cancellation_arrives(monkeypatch):
    import camera_series
    import camera_devices

    cancel_event = Event()
    class Capture:
        reads = 0
        released = False
        def isOpened(self):
            return True
        def set(self, *_args):
            return True
        def read(self):
            self.reads += 1
            cancel_event.set()
            return True, np.full((24, 32, 3), 80, dtype=np.uint8)
        def release(self):
            self.released = True

    capture = Capture()
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        VideoCapture=lambda *_args: capture,
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
    ))
    monkeypatch.setattr(camera_devices, "resolve_camera", lambda camera_id: {
        "index": 0, "backend": 700, "camera_id": camera_id,
    })

    with pytest.raises(camera_series.CameraSeriesCancelled):
        camera_series.capture_contact_sheet(4, "a" * 16, cancel_event=cancel_event)

    assert capture.reads == 1
    assert capture.released
