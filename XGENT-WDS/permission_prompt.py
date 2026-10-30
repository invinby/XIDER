"""Owner-visible Windows consent entry points.

Windows location consent must be requested by a foreground UI action. The
agent is otherwise headless and receives MQTT commands on worker threads, so
this helper creates a small local window, brings it to the foreground, and
calls the OS API from its UI thread. Windows then owns the Allow/Deny prompt.
"""

from __future__ import annotations


def request_windows_location_consent() -> dict[str, str]:
    """Show a local explanation, then ask Windows for one-time location access.

    This function never reads a location. A caller may perform a separate,
    explicit one-shot read only after this returns ``granted``.
    """
    import asyncio
    import sys

    if sys.platform != "win32":
        return {"state": "unsupported", "text": "Системный запрос геолокации доступен только в Windows."}

    try:
        import tkinter as tk
        from winrt.windows.devices.geolocation import Geolocator
    except (ImportError, OSError):
        return {"state": "unavailable", "text": "Не удалось загрузить локальное окно или API геолокации Windows."}

    outcome = {"state": "cancelled", "text": "Локальный запрос геолокации отменён; доступ не запрашивался."}
    try:
        root = tk.Tk()
    except Exception:
        return {"state": "unavailable", "text": "Нет доступного рабочего стола для системного запроса Windows."}

    root.title("XIDER · разрешение геолокации")
    root.resizable(False, False)
    root.geometry("440x150")
    root.columnconfigure(0, weight=1)

    tk.Label(
        root,
        text="Windows сейчас покажет системный запрос геолокации для XIDER.",
        font=("Segoe UI", 11, "bold"),
        wraplength=400,
        justify="left",
    ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="w")
    tk.Label(
        root,
        text=("Выбери разрешение в окне Windows. Координаты этим запросом не читаются; "
              "их получение запускается отдельно."),
        font=("Segoe UI", 10),
        wraplength=400,
        justify="left",
    ).grid(row=1, column=0, padx=20, pady=(0, 14), sticky="w")
    status = tk.StringVar(value="Ожидаю ответ Windows. Закрой это окно, чтобы отменить запрос.")
    tk.Label(root, textvariable=status, font=("Segoe UI", 9), wraplength=400, justify="left").grid(
        row=2, column=0, padx=20, pady=(0, 8), sticky="w"
    )

    buttons = tk.Frame(root)
    buttons.grid(row=3, column=0, padx=20, pady=(0, 16), sticky="e")
    request_after_id = None

    def cancel() -> None:
        if request_after_id is not None:
            try:
                root.after_cancel(request_after_id)
            except Exception:
                pass
        root.destroy()

    def ask_windows() -> None:
        nonlocal outcome
        cancel_button.configure(state="disabled")
        root.attributes("-topmost", False)
        status.set("Жду решение в системном окне Windows…")
        root.update_idletasks()
        try:
            async def request_access():
                return await Geolocator.request_access_async()

            access = int(asyncio.run(request_access()))
            if access == 0:  # GeolocationAccessStatus.Allowed
                outcome = {"state": "granted", "text": "Windows разрешила геолокацию. Координаты ещё не запрашивались."}
            elif access == 1:  # GeolocationAccessStatus.Denied
                outcome = {"state": "denied", "text": "В системном окне Windows отказано в геолокации."}
            else:  # Unspecified, including a system policy result
                outcome = {"state": "unknown", "text": "Windows не вернула однозначный ответ о разрешении геолокации."}
        except Exception:
            outcome = {"state": "unavailable", "text": "Не удалось завершить системный запрос Windows; доступ не считается выданным."}
        finally:
            try:
                root.destroy()
            except Exception:
                pass

    cancel_button = tk.Button(buttons, text="Отмена", command=cancel)
    cancel_button.pack(side="left")
    root.protocol("WM_DELETE_WINDOW", cancel)
    def show_system_prompt() -> None:
        try:
            root.lift()
            root.focus_force()
            root.update_idletasks()
            ask_windows()
        except Exception:
            cancel()

    request_after_id = root.after(250, show_system_prompt)
    root.mainloop()
    return outcome
