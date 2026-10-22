"""Owner-selected system audio output switching with rollback on uncertainty."""

import re


def switch_output(backend, *, output_id: object, output_name: object) -> dict[str, str]:
    """Switch one freshly selected output and verify the OS default changed.

    ``backend`` is a small platform adapter implementing ``list_outputs``,
    ``get_default``, ``set_default`` and ``verify_default``. Only sanitized
    friendly names are returned; platform endpoint IDs stay local.
    """
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
        previous = next((name for device_id, name in outputs if device_id == previous_id), None)
        if previous_id is None or previous is None:
            return {"state": "unknown"}
        if previous_id == output_id:
            return {"state": "unchanged", "output_name": output_name,
                    "previous_output_name": previous}
        backend.set_default(output_id)
        if backend.verify_default(output_id):
            return {"state": "changed", "output_name": output_name,
                    "previous_output_name": previous}
        backend.set_default(previous_id)
        if backend.verify_default(previous_id):
            return {"state": "unknown"}
        return {"state": "rollback_failed"}
    except Exception:
        try:
            if "previous_id" in locals() and previous_id is not None:
                backend.set_default(previous_id)
                if not backend.verify_default(previous_id):
                    return {"state": "rollback_failed"}
        except Exception:
            return {"state": "rollback_failed"}
        return {"state": "unknown"}


def switch_system_output(sounddevice, *, output_id: object, output_name: object) -> dict[str, str]:
    """Use the current PortAudio catalog and the Windows multimedia endpoint."""
    try:
        from pycaw.pycaw import AudioUtilities, EDataFlow, ERole

        rows = list(sounddevice.query_devices())
        def normalized_name(value):
            return re.sub(r",\s*(?:windows\s+)?(?:wasapi|directsound|mme)\s*$", "",
                          " ".join(value.split()), flags=re.IGNORECASE).casefold()

        projected = []
        for index, row in enumerate(rows):
            if (isinstance(row, dict) and isinstance(row.get("max_output_channels"), int)
                    and not isinstance(row.get("max_output_channels"), bool)
                    and row["max_output_channels"] > 0 and isinstance(row.get("name"), str)):
                projected.append((str(index), row["name"].strip()))
        class Backend:
            def __init__(self):
                self.endpoints = list(AudioUtilities.GetAllDevices(
                    data_flow=EDataFlow.eRender.value, device_state=1,
                ))

            def _endpoint(self, name):
                matches = [item for item in self.endpoints
                           if normalized_name(item.FriendlyName) == normalized_name(name)]
                return matches[0] if len(matches) == 1 else None

            def list_outputs(self):
                return [(index, name) for index, name in projected
                        if self._endpoint(name) is not None]

            def get_default(self):
                current = AudioUtilities.GetSpeakers()
                matches = [(index, name) for index, name in projected
                           if self._endpoint(name) is not None
                           and self._endpoint(name).id == current.id]
                return matches[0][0] if len(matches) == 1 else None

            def set_default(self, selected_id):
                name = next(name for index, name in projected if index == selected_id)
                endpoint = self._endpoint(name)
                if endpoint is None:
                    raise OSError("selected endpoint is unavailable")
                AudioUtilities.SetDefaultDevice(endpoint.id, roles=[ERole.eMultimedia])

            def verify_default(self, selected_id):
                return self.get_default() == selected_id

        return switch_output(Backend(), output_id=output_id, output_name=output_name)
    except ImportError:
        return {"state": "unsupported"}
    except Exception:
        return {"state": "unknown"}
