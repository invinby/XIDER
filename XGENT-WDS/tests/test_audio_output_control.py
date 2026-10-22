import audio_output_control as control
import sys
from types import SimpleNamespace

import xgent_wds as wds


class Backend:
    def __init__(self):
        self.outputs = [("a", "Speakers"), ("b", "Headphones")]
        self.default = "a"
        self.fail_verify = False

    def list_outputs(self):
        return list(self.outputs)

    def get_default(self):
        return self.default

    def set_default(self, output_id):
        self.default = output_id

    def verify_default(self, output_id):
        return not (self.fail_verify and output_id == "b") and self.default == output_id


def test_switches_only_the_fresh_owner_selected_system_output():
    backend = Backend()

    result = control.switch_output(backend, output_id="b", output_name="Headphones")

    assert result == {"state": "changed", "output_name": "Headphones", "previous_output_name": "Speakers"}
    assert backend.default == "b"


def test_rejects_stale_or_mismatched_selection_without_changing_system_output():
    backend = Backend()

    result = control.switch_output(backend, output_id="b", output_name="Speakers")

    assert result == {"state": "unavailable"}
    assert backend.default == "a"


def test_rejects_ambiguous_names_without_guessing():
    backend = Backend()
    backend.outputs.append(("c", "Headphones"))

    result = control.switch_output(backend, output_id="b", output_name="Headphones")

    assert result == {"state": "ambiguous"}
    assert backend.default == "a"


def test_rolls_back_when_system_does_not_confirm_new_default():
    backend = Backend()
    backend.fail_verify = True

    result = control.switch_output(backend, output_id="b", output_name="Headphones")

    assert result == {"state": "unknown"}
    assert backend.default == "a"


def test_rejects_malformed_selection_before_calling_backend():
    backend = Backend()

    result = control.switch_output(backend, output_id="", output_name="Headphones")

    assert result == {"state": "unavailable"}
    assert backend.default == "a"


def test_agent_reports_only_verified_switch_metadata(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setattr(control, "switch_system_output", lambda _sd, **_kwargs: {
        "state": "changed", "output_name": "Headphones", "previous_output_name": "Speakers",
    })
    client = object.__new__(wds.XgentClient)
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


def test_windows_backend_matches_portaudio_host_suffix_to_core_audio_friendly_name(monkeypatch):
    import types

    class Endpoint:
        def __init__(self, endpoint_id, name):
            self.id = endpoint_id
            self.FriendlyName = name

    class AudioUtilities:
        endpoints = [Endpoint("endpoint-speakers", "Speakers (Realtek Audio)"),
                     Endpoint("endpoint-headphones", "Headphones (USB DAC)")]
        current = "endpoint-speakers"
        set_calls = []

        @classmethod
        def GetAllDevices(cls, *, data_flow, device_state):
            assert (data_flow, device_state) == (0, 1)
            return cls.endpoints

        @classmethod
        def GetSpeakers(cls):
            return next(item for item in cls.endpoints if item.id == cls.current)

        @classmethod
        def SetDefaultDevice(cls, endpoint_id, *, roles):
            cls.current = endpoint_id
            cls.set_calls.append((endpoint_id, roles))

    multimedia = object()
    module = types.ModuleType("pycaw.pycaw")
    module.AudioUtilities = AudioUtilities
    module.EDataFlow = types.SimpleNamespace(eRender=types.SimpleNamespace(value=0))
    module.ERole = types.SimpleNamespace(eMultimedia=multimedia)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", module)
    sounddevice = SimpleNamespace(query_devices=lambda: [
        {"name": "Speakers (Realtek Audio), Windows WASAPI", "max_output_channels": 2},
        {"name": "Headphones (USB DAC), Windows WASAPI", "max_output_channels": 2},
    ])

    result = control.switch_system_output(sounddevice, output_id="1", output_name="Headphones (USB DAC), Windows WASAPI")

    assert result == {"state": "changed", "output_name": "Headphones (USB DAC), Windows WASAPI",
                      "previous_output_name": "Speakers (Realtek Audio), Windows WASAPI"}
    assert AudioUtilities.current == "endpoint-headphones"
    assert AudioUtilities.set_calls == [("endpoint-headphones", [multimedia])]
