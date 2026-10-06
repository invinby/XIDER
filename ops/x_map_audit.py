#!/usr/bin/env python3
"""Read-only static inventory of XIDER bot callbacks and agent commands.

This tool parses Python ASTs only. It does not import or execute the bot/agents,
and its report is a source-code compatibility check, not proof of live support.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path


CALLBACK_RE = re.compile(r"^cmd:([a-zA-Z0-9_]+)$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CALLBACK_ALIASES = {"check_update": "agent_update"}
DYNAMIC_ACTION_WRAPPERS = {
    ("publish_command", "publish"): "shared_publish_dispatch",
    ("publish_command", "on_repeat"): "history_replay",
    ("publish_tracked", "_run_text_prank_input"): "prompted_text_prank_dispatch",
    ("simple_command", "on_cmd_prank_generic"): "xlex_prank_dispatch",
    ("publish_tracked", "_simple_command_unlocked"): "simple_command_dispatch",
}

# These profiles describe OS/session gates visible from the implementation.
# They are not claims that the permission was granted or that the operation
# has been accepted on a physical device.
PERMISSION_PROFILES = {
    "agent_account": {
        "label": "Контекст учётной записи агента",
        "windows": "Работа от имени пользователя агента; действуют обычные ACL/UAC. Повышение привилегий не выполняется.",
        "macos": "Работа от имени пользователя агента; действуют обычные ACL и ограничения текущего сеанса. Повышение привилегий не выполняется.",
    },
    "interactive_session": {
        "label": "Активный пользовательский сеанс",
        "windows": "Для видимого UI/ввода нужен интерактивный сеанс пользователя; закрытый экран или иной desktop могут ограничить действие.",
        "macos": "Для видимого UI/ввода нужен активный графический сеанс пользователя.",
    },
    "screen_capture": {
        "label": "Захват экрана",
        "windows": "Нужен доступ к активному desktop; политика и состояние сеанса могут запретить захват.",
        "macos": "Локально выданное разрешение Screen Recording; само наличие handler не подтверждает разрешение.",
    },
    "camera": {
        "label": "Камера",
        "windows": "Нужен доступ desktop-приложений к камере в настройках Privacy; устройство может быть занято.",
        "macos": "Локально выданное разрешение Camera; само наличие handler не подтверждает разрешение.",
    },
    "microphone": {
        "label": "Микрофон",
        "windows": "Нужен доступ desktop-приложений к микрофону в настройках Privacy; устройство может быть занято.",
        "macos": "Локально выданное разрешение Microphone; само наличие handler не подтверждает разрешение.",
    },
    "approximate_location": {
        "label": "Приблизительная IP-геолокация",
        "windows": "Нужен исходящий доступ к провайдеру IP-геолокации; GPS/точная координата из этого профиля не следует.",
        "macos": "Нужен исходящий доступ к провайдеру IP-геолокации; GPS/точная координата из этого профиля не следует.",
    },
    "clipboard": {
        "label": "Буфер обмена",
        "windows": "Доступ к буферу текущей учётной записи и интерактивного сеанса; политики Windows могут ограничивать доступ.",
        "macos": "Доступ к pasteboard текущего пользователя; системное поведение/подтверждение зависит от версии macOS и требует проверки на устройстве.",
    },
    "filesystem_read": {
        "label": "Чтение файлов",
        "windows": "Обычные файловые ACL; защищённые каталоги и чужие профили могут быть недоступны.",
        "macos": "Обычные файловые ACL; защищённые каталоги могут требовать локально выданного Full Disk Access.",
    },
    "filesystem_write": {
        "label": "Запись файлов",
        "windows": "Запись только там, где её разрешают ACL текущей учётной записи; повышение прав не выполняется.",
        "macos": "Запись только там, где её разрешают ACL текущей учётной записи; защищённые каталоги могут требовать локального разрешения.",
    },
    "filesystem_delete": {
        "label": "Удаление файлов",
        "windows": "Удаление подчиняется файловым ACL; защищённые файлы не обходятся.",
        "macos": "Удаление подчиняется файловым ACL; защищённые файлы не обходятся.",
    },
    "process_read": {
        "label": "Сведения о процессах",
        "windows": "Видимость процессов зависит от учётной записи; защищённые процессы могут скрывать детали.",
        "macos": "Видимость процессов зависит от учётной записи; защищённые процессы могут скрывать детали.",
    },
    "process_control": {
        "label": "Управление процессами",
        "windows": "Остановка подчиняется правам процесса и учётной записи; защищённые/чужие процессы могут отказать.",
        "macos": "Остановка подчиняется правам процесса и учётной записи; защищённые/чужие процессы могут отказать.",
    },
    "network_outbound": {
        "label": "Исходящая сеть",
        "windows": "Нужен доступ сети/брандмауэра к внешнему адресу или сервису.",
        "macos": "Нужен доступ сети к внешнему адресу или сервису.",
    },
    "accessibility_input": {
        "label": "Системный ввод и Accessibility",
        "windows": "Нужен доступ к активному desktop; UIPI, блокировка сеанса и политики ввода могут ограничить действие.",
        "macos": "Синтетический ввод/управление указателем требует локального разрешения Accessibility; выдача не проверяется статически.",
    },
    "app_automation": {
        "label": "Управление приложениями/Apple Events",
        "windows": "Действие работает только в пользовательском desktop-сеансе и может быть ограничено UIPI/политиками ОС.",
        "macos": "Управление другим приложением через Apple Events может требовать локального разрешения Automation; выдача не проверяется статически.",
    },
    "audio_output": {
        "label": "Аудиовывод",
        "windows": "Нужен доступный аудиовыход активного пользовательского сеанса.",
        "macos": "Нужен доступный аудиовыход активного пользовательского сеанса.",
    },
    "system_control": {
        "label": "Системное действие",
        "windows": "Действуют привилегии Windows и политика устройства; привилегии не повышаются автоматически.",
        "macos": "Действуют привилегии macOS и политика устройства; привилегии не повышаются автоматически.",
    },
    "agent_lifecycle": {
        "label": "Жизненный цикл агента",
        "windows": "Действие меняет состояние собственного агента; запуск/остановка Guardian и Task Scheduler подчиняются сохранённому намерению владельца.",
        "macos": "Действие меняет состояние собственного агента; LaunchAgent и Guardian подчиняются сохранённому намерению владельца.",
    },
    "sensitive_data": {
        "label": "Чувствительные данные пользователя/устройства",
        "windows": "Вывод может содержать приватные сведения; действуют ACL и права Telegram/X-CORE, обход ограничений ОС не выполняется.",
        "macos": "Вывод может содержать приватные сведения; действуют ACL и права Telegram/X-CORE, обход ограничений ОС не выполняется.",
    },
    "command_execution": {
        "label": "Выполнение команды владельца",
        "windows": "Команда исполняется с правами процесса агента; UAC не обходится, произвольное повышение прав не выполняется.",
        "macos": "Команда исполняется с правами процесса агента; системные ограничения и права macOS не обходятся.",
    },
    "startup_management": {
        "label": "Автозапуск агента",
        "windows": "Изменение собственной задачи Task Scheduler подчиняется правам её владельца; глобальные задачи не обходятся.",
        "macos": "Изменение собственного LaunchAgent подчиняется правам пользователя; системные LaunchDaemon не создаются.",
    },
    "guardian_service": {
        "label": "Управление Guardian",
        "windows": "Управление собственной зарегистрированной задачей Guardian; установка/права ОС должны быть разрешены владельцем устройства.",
        "macos": "Управление собственным пользовательским LaunchAgent Guardian; установка/права ОС должны быть разрешены владельцем устройства.",
    },
}

_FILE_READ_COMMANDS = {
    "dir_list", "disks", "env_get", "file_get", "find_file", "path_open",
    "startup_list", "storage_smart", "sys_history_cmd", "sys_installed_apps",
}
_FILE_WRITE_COMMANDS = {"download_url", "file_put", "wallpaper_set"}
_FILE_DELETE_COMMANDS = {"file_del", "sys_clean_temp"}
_PROCESS_READ_COMMANDS = {"processes"}
_PROCESS_CONTROL_COMMANDS = {"kill_process", "proc_kill_name"}
_OUTBOUND_NETWORK_COMMANDS = {
    "download_url", "ext_ip", "geo_location", "net_ping", "open_url",
    "prank_random_site", "prank_rickroll", "prank_rickroll_terminal",
    "prank_open_browser_memes",
}
_ACCESSIBILITY_COMMANDS = {
    "hotkey", "prank_crazy_cursor", "type_text",
}
_APP_AUTOMATION_COMMANDS = {
    "prank_alert_loop", "prank_dancing_windows", "prank_matrix",
    "prank_minimize_all", "prank_open_notepad_type", "prank_rickroll_terminal",
    "prank_shake_window", "type_text", "hotkey", "wallpaper_set",
    "prank_change_wallpaper", "prank_restore_wallpaper",
}
_AUDIO_OUTPUT_COMMANDS = {
    "sound", "volume_set", "volume_toggle", "prank_beep_morse",
    "prank_laugh_track", "prank_random_beeps",
    "prank_say_whisper", "prank_screamer", "prank_shout_tts", "prank_siren",
    "prank_sound_fart", "prank_sound_spooky", "prank_speak_time",
    "prank_volume_jump",
}
_NETWORK_INFO_COMMANDS = {
    "net_bluetooth_list", "net_wifi_passwords", "netstat", "network",
    "usb_devices", "wifi_info",
}
_SYSTEM_CONTROL_COMMANDS = {"power", "standby_sleep", "wake"}
_STARTUP_COMMANDS = {"autorun_disable", "autorun_enable", "autorun_status"}
_SENSITIVE_DATA_COMMANDS = {
    "clipboard", "env_get", "file_get", "geo_location", "mic", "net_wifi_passwords",
    "processes", "screenshot", "shell", "sys_history_cmd", "sysinfo", "webcam",
}

# The project records these older Mac reports as unresolved until the owner
# repeats the check on the current installed release.
KNOWN_UNRETESTED_REPORTS = {
    ("screenshot", "macos"): "Mac screenshot was previously reported failing; not retested on this release.",
    ("geo_location", "macos"): "Mac geolocation was previously reported failing; not retested on this release.",
}

KNOWN_SOURCE_BEHAVIOR = {
    ("display_brightness", "macos"): {
        "state": "dependency_gated",
        "evidence": "Handler requires the external `brightness` executable; missing dependency returns failure.",
    },
    ("display_night_light", "macos"): {
        "state": "explicitly_unsupported",
        "evidence": "Handler always returns that macOS has no stable public CLI for Night Light.",
    },
    ("display_rotate", "macos"): {
        "state": "dependency_gated",
        "evidence": "Handler requires the external `displayplacer` executable; missing dependency returns failure.",
    },
    ("prank_crazy_cursor", "macos"): {
        "state": "dependency_and_permission_gated",
        "evidence": "Handler requires PyObjC Quartz and Accessibility; otherwise it returns a clear failure.",
    },
    ("prank_cursor_circle", "macos"): {
        "state": "explicitly_unavailable",
        "evidence": "Handler always returns that the feature is unavailable in the current macOS build.",
    },
    ("prank_glitch_cursor", "macos"): {
        "state": "explicitly_disabled",
        "evidence": "Handler always returns that cursor drawing is disabled without Quartz/Accessibility.",
    },
    ("prank_invert_screen", "macos"): {
        "state": "fallback_only",
        "evidence": "Handler shows a joke dialog and explicitly reports that real display inversion was not applied.",
    },
    ("prank_random_clicks", "macos"): {
        "state": "explicitly_unimplemented",
        "evidence": "Handler always returns that random clicks are not implemented safely.",
    },
    ("prank_swap_mouse", "macos"): {
        "state": "explicitly_disabled",
        "evidence": "Handler always returns that remote mouse-button swapping is disabled.",
    },
}


def _string_constants(node: ast.AST) -> set[str]:
    return {
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def _agent_inventory(path: Path) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declared: set[str] = set()
    handlers: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "SUPPORTED_COMMANDS" for target in targets):
                declared.update(_string_constants(node.value))
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Attribute)
                    and value.attr.startswith("_do_")
                ):
                    handlers.add(key.value)

    declared = {item for item in declared if NAME_RE.fullmatch(item)}
    return {
        "supported": sorted(declared),
        "handlers": sorted(handlers),
        "declared_without_handler": sorted(declared - handlers),
        "handler_without_declaration": sorted(handlers - declared),
    }


def _literal_dict_keys(tree: ast.AST, name: str) -> set[str]:
    """Read string keys from a named top-level literal dict without importing it."""
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            return {
                key.value for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    return set()


def _literal_status_values(node: ast.AST) -> set[str]:
    """Read result literals, skipping condition expressions and their labels."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_status_values(node.body) | _literal_status_values(node.orelse)
    return set()


def _feature_status_inventory(path: Path) -> dict[str, list[str]]:
    """Collect feature keys and literal status values from the agent's status function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == "_detect_feature_status"),
        None,
    )
    keys: set[str] = set()
    states: set[str] = set()
    if function is None:
        return {"keys": [], "states": []}

    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                keys.add(key.value)
                states.update(_literal_status_values(value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
                    continue
                if target.value.id != "statuses":
                    continue
                index = target.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    keys.add(index.value)
                    states.update(_literal_status_values(node.value))

    return {"keys": sorted(keys), "states": sorted(states)}


def _guardian_commands(path: Path) -> list[str]:
    """Collect literal command branches from a Guardian.handle(command) method."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "command":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                if NAME_RE.fullmatch(comparator.value):
                    commands.add(comparator.value)
    return sorted(commands)


def _permission_profile_ids(command: str, *, guardian: bool = False) -> list[str]:
    """Return the OS/session gates statically attributable to a command."""
    if guardian:
        return ["guardian_service"]

    profiles = {"agent_account"}
    if command in {"screenshot", "webcam", "mic", "clipboard", "clipboard_set"}:
        profiles.add("interactive_session")
    if command == "screenshot":
        profiles.add("screen_capture")
    if command == "webcam":
        profiles.add("camera")
    if command == "mic":
        profiles.add("microphone")
    if command == "geo_location":
        profiles.update({"approximate_location", "network_outbound"})
    if command in {"clipboard", "clipboard_set", "prank_paste_clipboard_spam"}:
        profiles.add("clipboard")

    if command in _FILE_READ_COMMANDS:
        profiles.add("filesystem_read")
    if command in _FILE_WRITE_COMMANDS:
        profiles.add("filesystem_write")
    if command in _FILE_DELETE_COMMANDS:
        profiles.add("filesystem_delete")
    if command in _PROCESS_READ_COMMANDS:
        profiles.add("process_read")
    if command in _PROCESS_CONTROL_COMMANDS:
        profiles.add("process_control")
    if command in _NETWORK_INFO_COMMANDS:
        profiles.add("sensitive_data")
    if command in _OUTBOUND_NETWORK_COMMANDS:
        profiles.add("network_outbound")
    if command in _ACCESSIBILITY_COMMANDS:
        profiles.update({"accessibility_input", "interactive_session"})
    if command in _APP_AUTOMATION_COMMANDS:
        profiles.update({"app_automation", "interactive_session"})
    if command in _AUDIO_OUTPUT_COMMANDS:
        profiles.update({"audio_output", "interactive_session"})
    if command.startswith("prank_") or command in {
        "open_app", "open_url", "notify", "wallpaper_set",
    }:
        profiles.add("interactive_session")
    if command == "shell":
        profiles.update({"command_execution", "sensitive_data"})
    if command in _SENSITIVE_DATA_COMMANDS:
        profiles.add("sensitive_data")
    if command in _SYSTEM_CONTROL_COMMANDS:
        profiles.add("system_control")
    if command in _STARTUP_COMMANDS:
        profiles.add("startup_management")
    if command in {"agent_update", "stop", "uninstall_agent"}:
        profiles.add("agent_lifecycle")
    if command == "agent_update":
        profiles.add("filesystem_write")
    if command == "uninstall_agent":
        profiles.update({"startup_management", "filesystem_delete"})

    return sorted(profiles)


def _command_source_behavior(command: str, platform: str, source_support: str) -> dict[str, str]:
    if source_support != "declared_and_handler":
        return {
            "state": "source_gap",
            "evidence": "Declared command and handler map do not both exist in this source tree.",
        }
    return KNOWN_SOURCE_BEHAVIOR.get((command, platform), {
        "state": "handler_present",
        "evidence": "A handler is present; runtime outcomes may still depend on permissions, dependencies, device, and session.",
    })


def _command_stability(
    command: str,
    platform: str,
    source_support: str,
    source_behavior: dict[str, str],
) -> dict[str, str]:
    """Never promote an AST match into a physical-device reliability claim."""
    if source_support != "declared_and_handler":
        return {
            "state": "source_gap",
            "evidence": "Declared command and handler map do not both exist in this source tree.",
        }
    reported = KNOWN_UNRETESTED_REPORTS.get((command, platform))
    if reported:
        return {"state": "reported_issue_not_retested", "evidence": reported}
    if source_behavior["state"] in {
        "explicitly_disabled", "explicitly_unavailable", "explicitly_unimplemented",
        "explicitly_unsupported", "fallback_only",
    }:
        return {
            "state": "known_source_limited",
            "evidence": source_behavior["evidence"],
        }
    if source_behavior["state"] in {"dependency_gated", "dependency_and_permission_gated"}:
        return {
            "state": "dependency_gated_unverified",
            "evidence": source_behavior["evidence"],
        }
    return {
        "state": "source_only_unverified",
        "evidence": "AST confirms command declaration and handler only; no current physical-device acceptance is recorded.",
    }


def _command_matrix(
    actions: set[str],
    platforms: dict[str, dict[str, list[str]]],
    guardians: dict[str, list[str]],
) -> dict:
    worker_names = set(actions) - {"guardian"}
    for inventory in platforms.values():
        worker_names.update(inventory["supported"])
        worker_names.update(inventory["handlers"])

    worker_rows = []
    for command in sorted(worker_names):
        platform_rows = {}
        for platform, inventory in platforms.items():
            declared = command in inventory["supported"]
            handler = command in inventory["handlers"]
            source_support = "declared_and_handler" if declared and handler else (
                "declared_without_handler" if declared else (
                    "handler_without_declaration" if handler else "not_declared"
                )
            )
            source_behavior = _command_source_behavior(command, platform, source_support)
            platform_rows[platform] = {
                "source_support": source_support,
                "source_behavior": source_behavior,
                "permission_profile_ids": _permission_profile_ids(command),
                "stability": _command_stability(command, platform, source_support, source_behavior),
            }
        worker_rows.append({"command": command, "platforms": platform_rows})

    guardian_names = set(guardians["windows"]) | set(guardians["macos"])
    guardian_rows = []
    for command in sorted(guardian_names):
        platform_rows = {}
        for platform, commands in guardians.items():
            supported = command in commands
            source_support = "source_branch_present" if supported else "source_branch_missing"
            platform_rows[platform] = {
                "source_support": source_support,
                "source_behavior": {
                    "state": "handler_present" if supported else "source_gap",
                    "evidence": (
                        "AST found the platform Guardian command branch; runtime behavior remains unverified."
                        if supported else "No literal Guardian command branch was found in this platform source."
                    ),
                },
                "permission_profile_ids": _permission_profile_ids(command, guardian=True),
                "stability": {
                    "state": "source_only_unverified" if supported else "source_gap",
                    "evidence": (
                        "AST confirms a literal Guardian command branch only; no current physical-device acceptance is recorded."
                        if supported else "No literal Guardian command branch was found in this platform source."
                    ),
                },
            }
        guardian_rows.append({"command": command, "platforms": platform_rows})

    permission_profile_gaps = sorted({
        profile_id
        for row in worker_rows + guardian_rows
        for platform_row in row["platforms"].values()
        for profile_id in platform_row["permission_profile_ids"]
        if profile_id not in PERMISSION_PROFILES
    })
    return {
        "source_only": True,
        "worker_command_count": len(worker_rows),
        "guardian_command_count": len(guardian_rows),
        "permission_profile_gaps": permission_profile_gaps,
        "permission_profiles": PERMISSION_PROFILES,
        "worker_commands": worker_rows,
        "guardian_commands": guardian_rows,
    }


def _enclosing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = parents.get(parent)
    return None


def build_report(root: Path) -> dict:
    bot_path = root / "TG-BOT-SERVER" / "bot.py"
    tree = ast.parse(bot_path.read_text(encoding="utf-8"), filename=str(bot_path))
    capability_features = _literal_dict_keys(tree, "_CAPABILITY_LABELS")
    capability_states = _literal_dict_keys(tree, "_CAPABILITY_STATES")
    callbacks: set[str] = set()
    actions: set[str] = set()
    dynamic_action_sites: list[dict[str, str | int]] = []
    dynamic_action_wrappers: list[dict[str, str | int]] = []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        for value in _string_constants(node):
            match = CALLBACK_RE.fullmatch(value)
            if match:
                callbacks.add(match.group(1))
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.id if isinstance(function, ast.Name) else (
            function.attr if isinstance(function, ast.Attribute) else ""
        )
        if name == "publish_tracked":
            action_arg = node.args[0] if node.args else None
        elif name == "publish_command":
            action_arg = node.args[1] if len(node.args) > 1 else None
        elif name == "simple_command":
            action_arg = node.args[1] if len(node.args) > 1 else None
        else:
            continue
        if isinstance(action_arg, ast.Constant) and isinstance(action_arg.value, str):
            actions.add(CALLBACK_ALIASES.get(action_arg.value, action_arg.value))
        else:
            function_name = _enclosing_function_name(node, parents)
            wrapper_kind = DYNAMIC_ACTION_WRAPPERS.get((name, function_name or ""))
            site = {"line": node.lineno, "call": name, "function": function_name or "<module>"}
            if wrapper_kind:
                dynamic_action_wrappers.append({**site, "classification": wrapper_kind})
            else:
                dynamic_action_sites.append(site)

    platforms = {
        "windows": _agent_inventory(root / "XGENT-WDS" / "xgent_wds.py"),
        "macos": _agent_inventory(root / "XGENT-MCS" / "xgent_mcs.py"),
    }
    feature_status = {
        "expected_features": sorted(capability_features),
        "allowed_states": sorted(capability_states),
        "source_only": True,
        "platforms": {},
    }
    for platform, agent_path in {
        "windows": root / "XGENT-WDS" / "xgent_wds.py",
        "macos": root / "XGENT-MCS" / "xgent_mcs.py",
    }.items():
        inventory = _feature_status_inventory(agent_path)
        agent_features = set(inventory["keys"])
        agent_states = set(inventory["states"])
        feature_status["platforms"][platform] = {
            **inventory,
            "missing_features": sorted(capability_features - agent_features),
            "unexpected_features": sorted(agent_features - capability_features),
            "unrecognized_states": sorted(agent_states - capability_states),
        }
    guardians = {
        "windows": _guardian_commands(root / "XGENT-WDS" / "xider_guardian_wds.py"),
        "macos": _guardian_commands(root / "XGENT-MCS" / "xider_guardian.py"),
    }
    windows = set(platforms["windows"]["supported"])
    macos = set(platforms["macos"]["supported"])
    worker_actions = actions - {"guardian"}
    return {
        "schema": "x-map-static-audit-v5",
        "source_only": True,
        "bot_callbacks": sorted(callbacks),
        "callback_count": len(callbacks),
        "bot_actions": sorted(actions),
        "dynamic_action_calls_not_classified": len(dynamic_action_sites),
        "dynamic_action_sites": sorted(dynamic_action_sites, key=lambda item: int(item["line"])),
        "dynamic_action_wrappers": sorted(dynamic_action_wrappers, key=lambda item: int(item["line"])),
        "action_agent_coverage": {
            "covered_on_both": sorted(worker_actions & windows & macos),
            "missing_on_windows": sorted(worker_actions - windows),
            "missing_on_macos": sorted(worker_actions - macos),
        },
        "guardian_commands": guardians,
        "command_matrix": _command_matrix(actions, platforms, guardians),
        "feature_status": feature_status,
        "guardian_coverage": {
            "required": "guardian" in actions,
            "missing_on_windows": ["guardian"] if "guardian" in actions and not guardians["windows"] else [],
            "missing_on_macos": ["guardian"] if "guardian" in actions and not guardians["macos"] else [],
        },
        "platforms": platforms,
        "platform_parity": {
            "windows_only": sorted(windows - macos),
            "macos_only": sorted(macos - windows),
            "shared_count": len(windows & macos),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only XIDER command compatibility audit")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    parser.add_argument("--matrix-only", action="store_true", help="emit only the per-command support/permission/stability matrix")
    args = parser.parse_args()
    report = build_report(args.root.resolve())
    payload = report["command_matrix"] if args.matrix_only else report
    output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
