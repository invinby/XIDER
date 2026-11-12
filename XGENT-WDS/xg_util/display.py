"""Pure display-format and payload transformation helpers."""


def _orientation_value(angle) -> int | None:
    """Map a display angle to the Win32 DMDO_* value."""
    try:
        normalized = int(angle) % 360
    except (TypeError, ValueError):
        return None
    return {0: 0, 90: 1, 180: 2, 270: 3}.get(normalized)


def _night_light_enabled(data: bytes) -> bool:
    """Return whether the known CloudStore state byte marks Night Light on."""
    return bool(data) and len(data) > 18 and data[18] == 0x15


def _night_light_toggle_bytes(data: bytes) -> bytes:
    """Build the corresponding CloudStore blob with Night Light toggled."""
    raw = bytearray(data)
    enabled = _night_light_enabled(raw)
    if enabled:
        new_data = bytearray(41)
        new_data[0:22] = raw[0:22]
        if len(raw) > 25:
            copy_len = min(len(raw) - 25, 43 - 25)
            new_data[23:23 + copy_len] = raw[25:25 + copy_len]
        new_data[18] = 0x13
    else:
        new_data = bytearray(43)
        new_data[0:22] = raw[0:22]
        if len(raw) > 23:
            copy_len = min(len(raw) - 23, 41 - 23)
            new_data[25:25 + copy_len] = raw[23:23 + copy_len]
        new_data[18] = 0x15
        new_data[23] = 0x10
        new_data[24] = 0x00
    for index in range(10, 15):
        if new_data[index] != 0xFF:
            new_data[index] = (new_data[index] + 1) % 256
            break
    return bytes(new_data)
