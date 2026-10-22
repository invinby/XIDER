from types import SimpleNamespace
import sys

import xgent_mcs as mcs


def test_camera_quality_captures_one_local_frame_and_never_returns_image(monkeypatch):
    from camera_quality import ANALYSIS_SOURCE

    class Capture:
        released = False
        reads = 0

        def isOpened(self):
            return True

        def set(self, *_args):
            return True

        def read(self):
            self.reads += 1
            return True, object()

        def release(self):
            self.released = True

    capture = Capture()
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        VideoCapture=lambda _device: capture,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
    ))
    monkeypatch.setattr("camera_quality.analyze_frame", lambda *_args: {
        "width_px": 1280, "height_px": 720, "sharpness_variance": 123.4,
        "mean_luma": 110.0, "dark_clipped_percent": 0.2, "bright_clipped_percent": 1.0,
    })
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_camera_quality({"id": "quality-ticket"})

    assert len(responses) == 1
    topic, result = responses[0]
    assert topic == "camera_quality"
    assert result["schema"] == "xider.camera-quality.v1"
    assert result["feature_id"] == "D02-F13"
    assert result["request_id"] == "quality-ticket"
    assert result["state"] == "supported"
    assert result["source"] == ANALYSIS_SOURCE
    assert result["data"]["sharpness_variance"] == 123.4
    assert "image" not in result and "frame" not in result
    assert capture.reads == 1 and capture.released


def test_camera_quality_reports_capture_failure_without_sending_an_image(monkeypatch):
    class Capture:
        released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    capture = Capture()
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda _device: capture))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_camera_quality({"id": "failed-ticket"})

    assert responses[0][0] == "camera_quality"
    assert responses[0][1]["request_id"] == "failed-ticket"
    assert responses[0][1]["state"] == "unknown"
    assert responses[0][1]["data"] is None
    assert "image" not in responses[0][1]
    assert capture.released
