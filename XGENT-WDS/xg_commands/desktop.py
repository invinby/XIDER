"""Windows desktop launch command handlers."""

from __future__ import annotations


def open_app(payload: dict, *, startfile, subprocess_module, logger) -> None:
    app = (payload.get("app") or "").strip()
    if not app:
        logger.warning("Команда open_app без имени")
        return
    logger.info("open_app: %r", app)
    # Try os.startfile first, then launch directly without a shell.
    try:
        startfile(app)
    except (AttributeError, FileNotFoundError, OSError):
        subprocess_module.Popen(
            app, shell=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )


def lock_screen(payload: dict, *, device_id, lock_workstation, publish_response, logger) -> None:
    ok = bool(lock_workstation())
    publish_response("lock", {
        "type": "lock", "device_id": device_id, "action": "lock",
        "id": payload.get("id") if isinstance(payload.get("id"), str) else None,
        "ok": ok,
        "error": None if ok else "LockWorkStation returned false",
    })
    logger.info("LockWorkStation result: %s", ok)


def prank_swap_mouse(payload: dict, *, device_id, swap_mouse_button, publish_response) -> None:
    swap = bool(payload.get("swap", True))
    try:
        swap_mouse_button(1 if swap else 0)
        ok = True
    except Exception:
        ok = False
    publish_response("prank_swap_mouse", {
        "type": "prank_swap_mouse", "device_id": device_id, "ok": ok, "swap": swap,
    })


def prank_hide_desktop(payload: dict, *, device_id, user32, publish_response) -> None:
    hide = bool(payload.get("hide", True))
    try:
        progman = user32.FindWindowW("Progman", None)
        def_view = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        listview = user32.FindWindowExW(def_view, None, "SysListView32", None)
        if listview:
            cmd = 0 if hide else 5
            user32.ShowWindow(listview, cmd)
        ok = True
    except Exception:
        ok = False
    publish_response("prank_hide_desktop", {
        "type": "prank_hide_desktop", "device_id": device_id, "ok": ok, "hide": hide,
    })
