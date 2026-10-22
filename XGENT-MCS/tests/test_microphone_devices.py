import microphone_devices
from pathlib import Path
import sys
from types import SimpleNamespace

import xgent_mcs as mcs


def test_preview_recording_plays_locally_before_returning_audio_and_cleans_temp(tmp_path, monkeypatch):
    assert hasattr(microphone_devices, "preview_microphone_recording")
    import numpy as np
    samples = np.ones((96000, 1), dtype="float32") * 0.25
    calls = []
    temp_path = tmp_path / "xider-preview.ogg"

    class TempFile:
        name = str(temp_path)
        def close(self):
            pass

    monkeypatch.setattr(microphone_devices, "_create_preview_temp_file", lambda: TempFile())

    class SoundFile:
        @staticmethod
        def write(path, audio, sample_rate, format):
            calls.append(("write", audio.copy(), sample_rate, format))
            Path(path).write_bytes(b"ogg-preview")

    class SoundDevice:
        @staticmethod
        def query_devices():
            return [
                {"name": "Input", "max_input_channels": 1, "default_samplerate": 48000},
                {"name": "Output", "max_output_channels": 2},
            ]
        @staticmethod
        def rec(frames, *, samplerate, channels, device, dtype):
            calls.append(("rec", frames, samplerate, channels, device, dtype))
            return samples
        @staticmethod
        def wait():
            calls.append(("wait",))
        @staticmethod
        def play(audio, *, samplerate, device):
            calls.append(("play", audio.copy(), samplerate, device))

    audio = microphone_devices.preview_microphone_recording(
        SoundDevice, SoundFile, input_device_id="0", output_device_id="1", duration_seconds=2,
        input_device_name="Input", output_device_name="Output",
    )

    assert audio == b"ogg-preview"
    assert calls[0] == ("rec", 96000, 48000, 1, 0, "float32")
    assert calls[1] == ("wait",)
    assert calls[2][0] == "play" and calls[2][2:] == (48000, 1)
    assert calls[3] == ("wait",)
    assert calls[4][0] == "write" and calls[4][2:] == (48000, "OGG")
    assert not temp_path.exists()
    assert not samples.any()


def test_projects_only_bounded_input_device_metadata():
    rows = [
        {"name": "USB Mic", "max_input_channels": 2, "max_output_channels": 0,
         "default_samplerate": 48000, "hostapi": 7, "id": "private-backend-id"},
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2,
         "default_samplerate": 44100},
    ]
    assert microphone_devices.project_input_devices(rows) == [
        {"input_id": "0", "name": "USB Mic", "channels": 2, "sample_rate": 48000}
    ]


def test_projects_only_bounded_output_device_metadata():
    rows = [
        {"name": "USB Mic", "max_input_channels": 2, "max_output_channels": 0,
         "default_samplerate": 48000, "hostapi": 7, "id": "private-backend-id"},
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2,
         "default_samplerate": 44100, "hostapi": 8, "id": "private-output-id"},
    ]

    assert microphone_devices.project_output_devices(rows) == [
        {"output_id": "1", "name": "Speakers", "channels": 2, "sample_rate": 44100}
    ]


def test_mac_agent_reports_verified_system_output_switch_metadata(monkeypatch):
    import audio_output_control
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setattr(audio_output_control, "switch_system_output", lambda _sd, **_kwargs: {
        "state": "changed", "output_name": "Headphones", "previous_output_name": "Speakers",
    })
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_audio_output_switch({"id": "fresh-ticket", "output_device_id": "2",
                                    "output_device_name": "Headphones"})

    topic, result = responses[0]
    assert topic == "audio_output_switch"
    assert result["schema"] == "xider.audio-output-switch.v1"
    assert result["feature_id"] == "D02-F18"
    assert result["request_id"] == "fresh-ticket"
    assert result["state"] == "changed"
    assert result["data"] == {"output_name": "Headphones", "previous_output_name": "Speakers"}


def test_agent_publishes_correlated_output_catalog_without_backend_fields(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_audio_output_catalog")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(query_devices=lambda: [
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2,
         "default_samplerate": 44100, "hostapi": 9, "id": "private-output-id"},
    ]))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_audio_output_catalog({"id": "output-ticket"})

    topic, result = responses[0]
    assert topic == "audio_output_catalog"
    assert result["schema"] == "xider.audio-output-catalog.v1"
    assert result["feature_id"] == "D02-F19"
    assert result["request_id"] == "output-ticket"
    assert result["state"] == "supported"
    assert result["data"] == {"outputs": [
        {"output_id": "0", "name": "Speakers", "channels": 2, "sample_rate": 44100}
    ]}
    assert "private-output-id" not in repr(result)


def test_drops_malformed_rows_and_caps_catalog_and_names():
    rows = [
        {"name": "M" * 120, "max_input_channels": 1, "default_samplerate": 44100},
        {"name": "bad", "max_input_channels": "many", "default_samplerate": 44100},
        *[{"name": f"Mic {i}", "max_input_channels": 1, "default_samplerate": 44100}
          for i in range(20)],
    ]

    projected = microphone_devices.project_input_devices(rows)

    assert len(projected) == 16
    assert projected[0] == {"input_id": "0", "name": "M" * 80, "channels": 1, "sample_rate": 44100}
    assert all(set(row) == {"input_id", "name", "channels", "sample_rate"} for row in projected)


def test_resolves_only_a_current_valid_input_index():
    devices = [
        {"name": "Output", "max_input_channels": 0},
        {"name": "Mic", "max_input_channels": 1},
    ]

    assert microphone_devices.resolve_input_index(devices, "1") == 1
    assert microphone_devices.resolve_input_index(devices, "0") is None
    assert microphone_devices.resolve_input_index(devices, "9") is None
    assert microphone_devices.resolve_input_index(devices, "1; open other device") is None


def test_agent_publishes_correlated_microphone_catalog_without_backend_fields(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(query_devices=lambda: [
        {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 48000,
         "hostapi": 9, "id": "private-backend-id"},
    ]))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_microphone_catalog({"id": "catalog-ticket"})

    topic, result = responses[0]
    assert topic == "microphone_catalog"
    assert result["schema"] == "xider.microphone-catalog.v1"
    assert result["feature_id"] == "D02-F15"
    assert result["request_id"] == "catalog-ticket"
    assert result["state"] == "supported"
    assert result["data"] == {"inputs": [
        {"input_id": "0", "name": "USB Mic", "channels": 1, "sample_rate": 48000}
    ]}
    assert "private-backend-id" not in repr(result)


def test_selected_microphone_is_resolved_again_before_recording(monkeypatch):
    calls = []
    def rec(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("test stops before recording")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda: [
            {"name": "Output", "max_input_channels": 0},
            {"name": "USB Mic", "max_input_channels": 1},
        ], rec=rec, wait=lambda: None,
    ))
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    client = object.__new__(mcs.XgentClient)
    monkeypatch.setattr(client, "_publish_response", lambda *_args: None)

    client._do_mic({"duration": 1, "input_device_id": "1"})

    assert calls == [((44100,), {"samplerate": 44100, "channels": 1, "device": 1})]


def test_stale_microphone_selection_does_not_start_recording(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda: [{"name": "Output", "max_input_channels": 0}],
        rec=lambda *args, **kwargs: calls.append((args, kwargs)), wait=lambda: None,
    ))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_mic({"duration": 1, "input_device_id": "4"})

    assert calls == []
    assert responses[0][0] == "mic"
    assert responses[0][1]["audio"] is None
    assert responses[0][1]["state"] == "unavailable"


def test_microphone_level_meter_returns_only_aggregates_and_never_audio(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_microphone_level")
    import microphone_level
    import numpy as np

    opened = []
    class InputStream:
        def __init__(self, *, device, channels, samplerate, callback):
            opened.append((device, channels, samplerate))
            self.callback = callback
        def __enter__(self):
            self.callback(np.array([[0.5], [-0.5]], dtype="float32"), 2, None, None)
            return self
        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda: [
            {"name": "Output", "max_input_channels": 0},
            {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 48000},
        ], InputStream=InputStream,
    ))
    monkeypatch.setattr(microphone_level.time, "sleep", lambda _seconds: None)
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_microphone_level({"id": "meter-ticket", "input_device_id": "1"})

    topic, result = responses[0]
    assert topic == "microphone_level"
    assert result["schema"] == "xider.microphone-level.v1"
    assert result["feature_id"] == "D02-F16"
    assert result["request_id"] == "meter-ticket"
    assert result["state"] == "supported"
    assert result["data"]["sample_count"] == 2
    assert "audio" not in result and "samples" not in result
    assert opened == [(1, 1, 48000)]


def test_stale_microphone_level_request_never_opens_input_stream(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_microphone_level")
    opened = []
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda: [{"name": "Mic", "max_input_channels": 1}],
        InputStream=lambda **kwargs: opened.append(kwargs),
    ))
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_microphone_level({"id": "meter-ticket", "input_device_id": "8"})

    assert opened == []
    assert responses[0][0] == "microphone_level"
    assert responses[0][1]["state"] == "unavailable"


def test_microphone_roundtrip_rejects_stale_output_before_recording():
    assert hasattr(microphone_devices, "run_microphone_roundtrip")
    calls = []
    class SoundDevice:
        @staticmethod
        def query_devices():
            return [{"name": "Mic", "max_input_channels": 1, "default_samplerate": 44100}]
        @staticmethod
        def rec(*args, **kwargs):
            calls.append((args, kwargs))
    try:
        microphone_devices.run_microphone_roundtrip(
            SoundDevice, object(), input_device_id="0", output_device_id="9",
        )
    except ValueError as exc:
        assert str(exc) == "selected_audio_device_unavailable"
    else:
        raise AssertionError("stale output selection must be rejected")
    assert calls == []


def test_agent_microphone_test_publishes_only_result_metadata(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_microphone_test")
    import microphone_devices
    monkeypatch.setattr(microphone_devices, "run_microphone_roundtrip", lambda *_a, **_k: {
        "state": "played", "duration_ms": 750, "sample_rate": 48000,
    })
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, result: responses.append((topic, result)))

    client._do_microphone_test({"id": "roundtrip-ticket", "input_device_id": "0", "output_device_id": "1"})

    topic, result = responses[0]
    assert "microphone_test" in mcs.SUPPORTED_COMMANDS
    assert topic == "microphone_test"
    assert result["schema"] == "xider.microphone-test.v1"
    assert result["feature_id"] == "D02-F17"
    assert result["request_id"] == "roundtrip-ticket"
    assert result["state"] == "played"
    assert result["data"] == {"duration_ms": 750, "sample_rate": 48000}
    assert "audio" not in result and "samples" not in result


def test_agent_microphone_preview_returns_audio_only_after_local_preview(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_microphone_preview")
    monkeypatch.setattr(microphone_devices, "preview_microphone_recording", lambda *_a, **_k: b"reviewed-audio")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace())
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, result: responses.append((topic, result)))

    client._do_microphone_preview({"id": "preview-ticket", "duration": 2,
                                   "input_device_id": "0", "output_device_id": "1"})

    topic, result = responses[0]
    assert "microphone_preview" in mcs.SUPPORTED_COMMANDS
    assert topic == "microphone_preview"
    assert result["schema"] == "xider.microphone-preview.v1"
    assert result["feature_id"] == "D02-F20"
    assert result["request_id"] == "preview-ticket"
    assert result["state"] == "previewed"
    assert result["audio"] == "cmV2aWV3ZWQtYXVkaW8="


def test_stereo_channel_balance_returns_only_metrics_and_zeroes_sample_buffer():
    assert hasattr(microphone_devices, "measure_stereo_channel_balance")
    import numpy as np
    samples = np.tile(np.array([[0.5, -0.25], [-0.5, 0.25]], dtype="float32"), (24000, 1))

    class SoundDevice:
        @staticmethod
        def query_devices():
            return [{"name": "Stereo mic", "max_input_channels": 2, "default_samplerate": 48000}]

        @staticmethod
        def rec(frames, *, samplerate, channels, device, dtype):
            assert frames == 48000 and samplerate == 48000 and channels == 2 and device == 0
            assert dtype == "float32"
            return samples

        @staticmethod
        def wait():
            pass

    result = microphone_devices.measure_stereo_channel_balance(
        SoundDevice, input_device_id="0", input_device_name="Stereo mic",
    )

    assert result == {"state": "measured", "duration_ms": 1000, "sample_rate": 48000,
                     "left_dbfs": -6.02, "right_dbfs": -12.04, "difference_db": 6.02}
    assert not samples.any()


def test_stereo_channel_balance_rejects_mono_or_changed_input_before_capture():
    assert hasattr(microphone_devices, "measure_stereo_channel_balance")
    calls = []

    class SoundDevice:
        @staticmethod
        def query_devices():
            return [{"name": "Mono mic", "max_input_channels": 1, "default_samplerate": 44100}]

        @staticmethod
        def rec(*args, **kwargs):
            calls.append((args, kwargs))

        @staticmethod
        def wait():
            calls.append("wait")

    try:
        microphone_devices.measure_stereo_channel_balance(
            SoundDevice, input_device_id="0", input_device_name="Renamed mic",
        )
    except ValueError as exc:
        assert str(exc) == "selected_stereo_microphone_unavailable"
    else:
        raise AssertionError("mono or changed input must fail before capture")
    assert calls == []


def test_agent_stereo_balance_publishes_only_correlated_scalar_metrics(monkeypatch):
    assert hasattr(mcs.XgentClient, "_do_microphone_balance")
    monkeypatch.setattr(microphone_devices, "measure_stereo_channel_balance", lambda *_a, **_k: {
        "state": "measured", "duration_ms": 1000, "sample_rate": 48000,
        "left_dbfs": -6.02, "right_dbfs": -12.04, "difference_db": 6.02,
    })
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, result: responses.append((topic, result)))

    client._do_microphone_balance({"id": "balance-ticket", "input_device_id": "2", "input_device_name": "Mic"})

    topic, result = responses[0]
    assert "microphone_balance" in mcs.SUPPORTED_COMMANDS
    assert topic == "microphone_balance"
    assert result["schema"] == "xider.microphone-balance.v1"
    assert result["feature_id"] == "D02-F21"
    assert result["request_id"] == "balance-ticket"
    assert result["state"] == "supported"
    assert result["data"]["difference_db"] == 6.02
    assert "audio" not in result and "samples" not in result


def test_microphone_roundtrip_deletes_temp_audio_when_playback_fails(tmp_path, monkeypatch):
    import numpy as np
    from pathlib import Path
    temp_path = tmp_path / "xider-media-failed.wav"
    class TempFile:
        name = str(temp_path)
        def close(self):
            pass
    monkeypatch.setattr(microphone_devices, "_create_roundtrip_temp_file", lambda: TempFile())
    class SoundFile:
        @staticmethod
        def write(path, *_args, **_kwargs):
            Path(path).write_bytes(b"private test audio")
        @staticmethod
        def read(path, **_kwargs):
            return np.ones((3, 1), dtype="float32"), 48000
    class SoundDevice:
        stopped = False
        @staticmethod
        def query_devices():
            return [{"name": "Mic", "max_input_channels": 1, "default_samplerate": 48000},
                    {"name": "Headphones", "max_output_channels": 2}]
        @staticmethod
        def rec(*_args, **_kwargs):
            return np.ones((3, 1), dtype="float32")
        @staticmethod
        def wait():
            pass
        @staticmethod
        def play(*_args, **_kwargs):
            raise RuntimeError("device removed")
        @classmethod
        def stop(cls):
            cls.stopped = True
    try:
        microphone_devices.run_microphone_roundtrip(
            SoundDevice, SoundFile, input_device_id="0", output_device_id="1",
            input_device_name="Mic", output_device_name="Headphones",
        )
    except RuntimeError as exc:
        assert str(exc) == "device removed"
    else:
        raise AssertionError("playback device failure must be reported")
    assert not temp_path.exists() and SoundDevice.stopped
