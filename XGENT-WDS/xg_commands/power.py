"""Power command behavior, isolated from the MQTT client class."""

from __future__ import annotations


def power(
    payload: dict, *, device_id, publish_response, logger,
    subprocess_module, time_module, ctypes_module, run_without_console,
    threading_module,
) -> None:
    action = payload.get("power_action") or payload.get("action", "")
    request_id = payload.get("id") or payload.get("request_id")
    request_id = request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None

    def publish(state: str, ok: bool, text: str, *, effect_confirmed: bool) -> None:
        result = {
            "type": "power", "device_id": device_id, "action": action,
            "ok": ok, "state": state, "effect_confirmed": effect_confirmed,
            "source": "SetSuspendState" if action == "sleep" else "shutdown.exe",
            "text": text,
        }
        if request_id is not None:
            result["id"] = request_id
            result["request_id"] = request_id
        publish_response("power", result)
        publish_response("output", result)

    if action == "shutdown":
        logger.info("Выключение машины…")
        try:
            subprocess_module.Popen(
                ["shutdown.exe", "/s", "/f", "/t", "3", "/c", "XGENT: Выключение"],
                creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            logger.warning("Не удалось запустить shutdown.exe: %s", exc)
            publish("failed", False, "⚠️ Windows не приняла команду выключения.", effect_confirmed=False)
            return
        publish("launch_accepted", True,
                "⚡ Windows приняла команду выключения с задержкой 3 секунды; итоговое выключение не подтверждено.",
                effect_confirmed=False)
    elif action == "reboot":
        logger.info("Перезагрузка машины…")
        try:
            subprocess_module.Popen(
                ["shutdown.exe", "/r", "/f", "/t", "3", "/c", "XGENT: Перезагрузка"],
                creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            logger.warning("Не удалось запустить shutdown.exe: %s", exc)
            publish("failed", False, "⚠️ Windows не приняла команду перезагрузки.", effect_confirmed=False)
            return
        publish("launch_accepted", True,
                "🔄 Windows приняла команду перезагрузки с задержкой 3 секунды; итоговая перезагрузка не подтверждена.",
                effect_confirmed=False)
    elif action == "sleep":
        logger.info("Устройство уходит в сон…")

        def sleep_worker():
            time_module.sleep(1.0)
            try:
                suspended = bool(ctypes_module.windll.powrprof.SetSuspendState(0, 1, 0))
            except Exception as exc:
                logger.warning("SetSuspendState ctypes failed: %s, fallback to PowerShell", exc)
                suspended = False
            if suspended:
                publish("confirmed", True, "😴 Windows подтвердила переход в спящий режим.", effect_confirmed=True)
                return
            try:
                fallback = run_without_console(
                    subprocess_module.run,
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "Add-Type -AssemblyName System.Windows.Forms; "
                     "$r=[System.Windows.Forms.Application]::SetSuspendState("
                     "[System.Windows.Forms.PowerState]::Suspend, $false, $false); "
                     "[Console]::Out.Write([string]$r)"],
                    capture_output=True, text=True, timeout=15, check=False,
                    creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
                )
                if fallback.returncode == 0 and fallback.stdout.strip().casefold() == "true":
                    publish("confirmed", True, "😴 Windows подтвердила переход в спящий режим.", effect_confirmed=True)
                elif fallback.returncode == 0 and fallback.stdout.strip().casefold() == "false":
                    publish("failed", False, "⚠️ Windows отказалась перейти в спящий режим.", effect_confirmed=False)
                else:
                    publish("unknown", False, "⚠️ Запрос сна отправлен, но Windows не подтвердила результат.", effect_confirmed=False)
            except Exception as exc:
                logger.warning("PowerShell sleep fallback failed: %s", exc)
                publish("unknown", False, "⚠️ Запрос сна отправлен, но результат неизвестен.", effect_confirmed=False)

        try:
            threading_module.Thread(target=sleep_worker, name="xgent-power-sleep", daemon=True).start()
        except RuntimeError as exc:
            logger.warning("Не удалось запустить sleep worker: %s", exc)
            publish("failed", False, "⚠️ Не удалось запустить обработчик перехода в сон.", effect_confirmed=False)
    else:
        logger.warning("Неизвестное действие power: %s", action)
        publish("unsupported", False, "⚠️ Это действие питания не поддерживается.", effect_confirmed=False)
