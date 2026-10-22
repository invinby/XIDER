"""macOS Core Audio system output selection with name-only public results."""

import ctypes
import ctypes.util

from microphone_devices import project_output_devices


def switch_output(backend, *, output_id: object, output_name: object) -> dict[str, str]:
    if (not isinstance(output_id, str) or not output_id or len(output_id) > 128
            or not isinstance(output_name, str) or not output_name.strip()
            or len(output_name) > 80 or not output_name.isprintable()):
        return {"state": "unavailable"}
    try:
        outputs = list(backend.list_outputs())
        matches = [(device_id, name) for device_id, name in outputs
                   if device_id == output_id and name == output_name]
        same_name = [item for item in outputs if item[1] == output_name]
        if len(matches) != 1:
            return {"state": "unavailable"}
        if len(same_name) != 1:
            return {"state": "ambiguous"}
        previous_id = backend.get_default()
        previous_name = next((name for device_id, name in outputs if device_id == previous_id), None)
        if previous_id is None or previous_name is None:
            return {"state": "unknown"}
        if previous_id == output_id:
            return {"state": "unchanged", "output_name": output_name,
                    "previous_output_name": previous_name}
        backend.set_default(output_id)
        if backend.verify_default(output_id):
            return {"state": "changed", "output_name": output_name,
                    "previous_output_name": previous_name}
        backend.set_default(previous_id)
        return {"state": "unknown" if backend.verify_default(previous_id) else "rollback_failed"}
    except Exception:
        try:
            if "previous_id" in locals() and previous_id is not None:
                backend.set_default(previous_id)
                if not backend.verify_default(previous_id):
                    return {"state": "rollback_failed"}
        except Exception:
            return {"state": "rollback_failed"}
        return {"state": "unknown"}


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


class _CoreAudio:
    class Address(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                    ("element", ctypes.c_uint32)]

    def __init__(self):
        core_path = ctypes.util.find_library("CoreAudio")
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not core_path or not cf_path:
            raise OSError("Core Audio unavailable")
        self.core = ctypes.CDLL(core_path)
        self.cf = ctypes.CDLL(cf_path)
        self.get_data = self.core.AudioObjectGetPropertyData
        self.get_data.argtypes = [ctypes.c_uint32, ctypes.POINTER(self.Address), ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self.get_data.restype = ctypes.c_int32
        self.set_data = self.core.AudioObjectSetPropertyData
        self.set_data.argtypes = [ctypes.c_uint32, ctypes.POINTER(self.Address), ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.set_data.restype = ctypes.c_int32
        self.cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                               ctypes.c_long, ctypes.c_uint32]
        self.cf.CFStringGetCString.restype = ctypes.c_bool
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]

    def _get(self, object_id: int, selector: str, ctype):
        address = self.Address(_fourcc(selector), _fourcc("glob"), 0)
        size = ctypes.c_uint32(ctypes.sizeof(ctype))
        value = ctype()
        status = self.get_data(object_id, ctypes.byref(address), 0, None,
                               ctypes.byref(size), ctypes.byref(value))
        if status or size.value != ctypes.sizeof(ctype):
            raise OSError("Core Audio property unavailable")
        return value.value

    def devices(self):
        address = self.Address(_fourcc("dev#"), _fourcc("glob"), 0)
        size = ctypes.c_uint32(0)
        if self.get_data(1, ctypes.byref(address), 0, None, ctypes.byref(size), None):
            raise OSError("Core Audio device list unavailable")
        if size.value == 0 or size.value > 4 * 256:
            return []
        count = size.value // ctypes.sizeof(ctypes.c_uint32)
        values = (ctypes.c_uint32 * count)()
        if self.get_data(1, ctypes.byref(address), 0, None, ctypes.byref(size), values):
            raise OSError("Core Audio device list unavailable")
        return [int(value) for value in values]

    def name(self, device_id: int) -> str:
        address = self.Address(_fourcc("lnam"), _fourcc("glob"), 0)
        size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
        value = ctypes.c_void_p()
        if self.get_data(device_id, ctypes.byref(address), 0, None,
                         ctypes.byref(size), ctypes.byref(value)) or not value.value:
            raise OSError("Core Audio device name unavailable")
        try:
            buffer = ctypes.create_string_buffer(512)
            if not self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                raise OSError("Core Audio device name unavailable")
            return buffer.value.decode("utf-8", errors="strict")
        finally:
            self.cf.CFRelease(value)

    def default_output(self) -> int:
        return int(self._get(1, "dOut", ctypes.c_uint32))

    def set_default_output(self, device_id: int) -> None:
        address = self.Address(_fourcc("dOut"), _fourcc("glob"), 0)
        value = ctypes.c_uint32(device_id)
        if self.set_data(1, ctypes.byref(address), 0, None,
                         ctypes.sizeof(value), ctypes.byref(value)):
            raise OSError("Core Audio rejected default output change")


def switch_system_output(sounddevice, *, output_id: object, output_name: object) -> dict[str, str]:
    try:
        core = _CoreAudio()
        rows = list(sounddevice.query_devices())
        outputs = project_output_devices(rows)
        core_devices = [(device_id, core.name(device_id)) for device_id in core.devices()]

        class Backend:
            def _core_id(self, name):
                matches = [device_id for device_id, system_name in core_devices
                           if system_name == name]
                return matches[0] if len(matches) == 1 else None

            def list_outputs(self):
                return [(row["output_id"], row["name"]) for row in outputs
                        if self._core_id(row["name"]) is not None]

            def get_default(self):
                current_name = core.name(core.default_output())
                matches = [row["output_id"] for row in outputs
                           if row["name"] == current_name and self._core_id(row["name"]) is not None]
                return matches[0] if len(matches) == 1 else None

            def set_default(self, selected_id):
                name = next(row["name"] for row in outputs if row["output_id"] == selected_id)
                device_id = self._core_id(name)
                if device_id is None:
                    raise OSError("selected Core Audio device unavailable")
                core.set_default_output(device_id)

            def verify_default(self, selected_id):
                return self.get_default() == selected_id

        return switch_output(Backend(), output_id=output_id, output_name=output_name)
    except Exception:
        return {"state": "unknown"}
