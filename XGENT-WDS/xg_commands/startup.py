"""Windows startup-task command handlers."""

from __future__ import annotations

import json


def autorun_status(payload: dict, *, device_id, publish_response, config_dir, task_exists, task_name) -> None:
    try:
        state_path = config_dir / "guardian.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        startup_enabled = bool(state.get("startup_enabled", True))
        enabled = task_exists() and startup_enabled
        text = (
            f"🚀 Автозапуск Windows: {'✅ ВКЛЮЧЕН' if enabled else '❌ ВЫКЛЮЧЕН'}\n"
            f"Источник: Scheduled Task «{task_name}»"
        )
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка проверки автозапуска: {exc}"
        ok = False
    publish_response("output", {
        "type": "autorun_status", "device_id": device_id, "ok": ok, "text": text,
    })


def autorun_enable(
    payload: dict, *, device_id, publish_response, task_exists, task_name,
    run_without_console, run, set_startup_enabled,
) -> None:
    try:
        if not task_exists():
            raise RuntimeError(
                f"Задача «{task_name}» отсутствует; переустанови подписанный Windows-пакет."
            )
        task = run_without_console(
            run, ["schtasks.exe", "/Change", "/TN", task_name, "/ENABLE"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if task.returncode != 0:
            raise RuntimeError((task.stderr or task.stdout or "не удалось включить Scheduled Task").strip())
        set_startup_enabled(True)
        result = run_without_console(
            run, ["schtasks.exe", "/Run", "/TN", task_name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "не удалось запустить Scheduled Task").strip())
        text = f"✅ Автозапуск Windows включён; задача «{task_name}» запущена."
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка включения автозапуска: {exc}"
        ok = False
    publish_response("output", {
        "type": "autorun_enable", "device_id": device_id, "ok": ok, "text": text,
    })


def autorun_disable(
    payload: dict, *, device_id, publish_response, task_exists, task_name,
    run_without_console, run, winreg_module, set_startup_enabled, logger,
) -> None:
    startup_state_changed = False
    try:
        set_startup_enabled(False)
        startup_state_changed = True
        if task_exists():
            disabled = run_without_console(
                run, ["schtasks.exe", "/Change", "/TN", task_name, "/DISABLE"],
                capture_output=True, text=True, check=False,
            )
            if disabled.returncode != 0:
                raise RuntimeError((disabled.stderr or disabled.stdout or "не удалось отключить Scheduled Task").strip())
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg_module.OpenKey(
                winreg_module.HKEY_CURRENT_USER, key_path, 0, winreg_module.KEY_ALL_ACCESS,
            ) as key:
                for value_name in ("XGENT", "XGentAgent"):
                    try:
                        winreg_module.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
        text = "🛑 Автозапуск Windows отключён. Текущий агент продолжит работать до остановки."
        ok = True
    except Exception as exc:
        if startup_state_changed:
            try:
                set_startup_enabled(True)
            except Exception:
                logger.exception("Could not restore Guardian startup state after autorun-disable failure")
        text = f"⚠️ Ошибка отключения автозапуска: {exc}"
        ok = False
    publish_response("output", {
        "type": "autorun_disable", "device_id": device_id, "ok": ok, "text": text,
    })
