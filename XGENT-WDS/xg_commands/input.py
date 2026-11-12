"""Keyboard and text input command handlers."""

from __future__ import annotations


def clipboard(payload: dict, *, device_id, publish_response, logger, pyperclip_module=None) -> None:
    if pyperclip_module is None:
        import pyperclip
        pyperclip_module = pyperclip
    text = pyperclip_module.paste() or ""
    publish_response("clipboard", {
        "type": "clipboard", "device_id": device_id, "text": text,
    })
    logger.info("Буфер обмена отправлен (%d символов)", len(text))


def clipboard_set(payload: dict, *, device_id, publish_response, logger, pyperclip_module=None) -> None:
    if pyperclip_module is None:
        import pyperclip
        pyperclip_module = pyperclip
    text = str(payload.get("text", ""))
    pyperclip_module.copy(text)
    publish_response("clipboard_set", {
        "type": "clipboard_set", "device_id": device_id, "length": len(text),
    })
    logger.info("В буфер обмена записано %d символов", len(text))


def hotkey(payload: dict, *, device_id, publish_response, map_hotkeys, ctypes_module, threading_module) -> None:
    spec = (payload.get("keys") or "").strip()
    try:
        vks = map_hotkeys(spec)
    except ValueError as exc:
        publish_response("hotkey", {"type": "hotkey", "device_id": device_id,
                                     "ok": False, "error": str(exc)})
        return
    keyeventf_keyup = 0x0002

    def press():
        for vk in vks:
            ctypes_module.windll.user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(vks):
            ctypes_module.windll.user32.keybd_event(vk, 0, keyeventf_keyup, 0)
    threading_module.Thread(target=press, name="xgent-hotkey", daemon=True).start()
    publish_response("hotkey", {"type": "hotkey", "device_id": device_id,
                                 "ok": True, "keys": spec})


def type_text(payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module) -> None:
    text = (payload.get("text") or "")[:500]
    keyeventf_unicode = 0x0004
    input_keyboard = 1

    class KEYBDINPUT(ctypes_module.Structure):
        _fields_ = [("wVk", ctypes_module.c_ushort), ("wScan", ctypes_module.c_ushort),
                    ("dwFlags", ctypes_module.c_ulong), ("time", ctypes_module.c_ulong),
                    ("dwExtraInfo", ctypes_module.POINTER(ctypes_module.c_ulong))]

    class UNION(ctypes_module.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes_module.c_ubyte * 32)]

    class INPUT(ctypes_module.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes_module.c_ulong), ("u", UNION)]

    def send_char(ch: str) -> None:
        inp = INPUT(type=input_keyboard)
        inp.u.ki = KEYBDINPUT(0, ord(ch), keyeventf_unicode, 0, None)
        arr = (INPUT * 1)(inp)
        ctypes_module.windll.user32.SendInput(1, arr, ctypes_module.sizeof(INPUT))

    def typer():
        for ch in text:
            send_char(ch)
            time_module.sleep(0.01)

    threading_module.Thread(target=typer, name="xgent-typer", daemon=True).start()
    publish_response("type_text", {
        "type": "type_text", "device_id": device_id, "ok": True, "len": len(text),
    })
