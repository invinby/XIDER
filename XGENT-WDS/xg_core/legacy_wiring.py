"""Legacy XGENT runtime wiring and host adapters."""

from __future__ import annotations

import threading
from types import FunctionType

import base64
import binascii
import ast
import csv
import ctypes
import ctypes.wintypes
import datetime
import html
import hashlib
import ipaddress
import importlib.machinery
import importlib.util
import io
import json
import locale
import logging
import math
import random
import re
import socket
import shutil
import subprocess
import tempfile
import uuid
import time
import webbrowser
from urllib.parse import urlsplit
import winreg
import winsound
from logging.handlers import RotatingFileHandler
from concurrent.futures import Future
from energy_usage import parse_srum_energy_csv
from sleep_wake_history import parse_windows_sleep_wake_events
from startup_duration import parse_windows_startup_duration
from sysinfo_schedule import load_schedule, save_schedule
import camera_quality
import camera_devices
import media_temp
import agent_file_inventory
import display_contact_sheet
import dns_diagnostics
import dns_settings
import dns_comparison
import dnssec_check
import gateway_probe
import route_trace
import path_mtu
import captive_portal
import ipv6_connectivity
import tls_certificate
import service_probe
import latency_series
import packet_loss_history
import jitter_measurement
import network_throughput
import network_link_speed
import route_lookup
import endpoint_batch
import wifi_quality
import wifi_channel
import wifi_connection_history
import proxy_status
import vpn_route
import network_traffic
import interface_status_history
from network_traffic_schedule import load_schedule as load_network_traffic_schedule, save_schedule as save_network_traffic_schedule
import window_capture
from media_request_control import MEDIA_REQUEST_ACTIONS, MediaRequestRegistry
import paho.mqtt.client as mqtt
import psutil
from xg_util.helpers import (
    _clamp,
    _collect_env,
    _format_battery,
    _format_disks,
    _format_network,
    _format_processes,
    _format_services,
    _humanize_seconds,
    _parse_key_content,
    _parse_reg_query,
    _parse_wifi_output,
    _parse_wifi_profile_names,
    _primary_mac,
    map_hotkeys,
)
from xg_telemetry.inventory import (
    _read_driver_inventory_snapshot,
    _read_hardware_inventory_snapshot,
    _read_os_support_identity,
    _read_pending_restart_snapshot,
)
from xg_telemetry.history import (
    _memory_pressure_history_sample as _telemetry_memory_pressure_history_sample,
    _read_memory_pressure_sample as _telemetry_read_memory_pressure_sample,
    regional_settings_snapshot as _telemetry_regional_settings_snapshot,
    volume_history_measurement as _telemetry_volume_history_measurement,
)
from xg_telemetry.battery import (
    read_battery_cycle_snapshot as _telemetry_battery_cycle_snapshot,
    read_battery_health_snapshot as _telemetry_battery_health_snapshot,
)
from xg_telemetry.thermal import read_thermal_snapshot as _telemetry_thermal_snapshot
from xg_telemetry.clock import read_clock_drift_snapshot as _telemetry_clock_drift_snapshot
from xg_telemetry.apps import read_installed_app_inventory_snapshot as _telemetry_installed_app_inventory_snapshot
from xg_telemetry.startup import read_startup_inventory_snapshot as _telemetry_startup_inventory_snapshot
from xg_telemetry.services import read_service_inventory_snapshot as _telemetry_service_inventory_snapshot
from xg_telemetry.crash import read_crash_summary_snapshot as _telemetry_crash_summary_snapshot
from xg_telemetry.smart import read_smart_trend_snapshot as _telemetry_smart_trend_snapshot
from xg_telemetry.volumes import read_volume_topology_snapshot as _telemetry_volume_topology_snapshot
from xg_telemetry.events import (
    read_sleep_wake_history_snapshot as _telemetry_sleep_wake_history_snapshot,
    read_startup_duration_snapshot as _telemetry_startup_duration_snapshot,
)
from xg_core.capabilities import (
    capability_snapshot as _core_capability_snapshot,
    detect_feature_status as _core_detect_feature_status,
    detect_features as _core_detect_features,
    module_available as _core_module_available,
)
from xg_telemetry.cache import read_inventory_snapshot
from xg_util.display import _night_light_enabled, _night_light_toggle_bytes, _orientation_value
from xg_util.filesystem import find_matches as _util_find_matches, list_directory as _util_list_directory
from xg_ui.screenshot import (
    bounded_screen_grab as _ui_bounded_screen_grab,
    screenshot_session_available as _ui_screenshot_session_available,
)
from xg_ui.display_control import read_current_display_angle as _ui_read_current_display_angle
from xg_ui.display_control import rotate_screen as _ui_rotate_screen
from xg_util.uninstall import remove_agent_install_tree, resolve_agent_install_root
from xg_util.transfers import (
    decode_file_put_payload as _util_decode_file_put_payload,
    validated_open_url as _util_validated_open_url,
    write_file_put_atomic as _util_write_file_put_atomic,
)
from xg_core import transport as _transport
from xg_telemetry import handlers as _telemetry_handlers
from xg_commands import register as register_command_handlers
from xg_commands.registry import build_command_handlers
from xg_commands import power_telemetry as _power_telemetry_commands
from xg_commands import inventory_telemetry as _inventory_telemetry_commands
from xg_commands import performance_telemetry as _performance_telemetry_commands
from xg_commands import system_events as _system_event_commands
from xg_commands import system_info as _system_info_commands
from xg_commands import capabilities as _capability_commands
from xg_commands import storage_telemetry as _storage_telemetry_commands
from xg_commands import audio as _audio_commands
from xg_commands import diagnostics as _diagnostic_commands
from xg_commands import permissions as _permission_commands
from xg_commands import display as _display_commands
from xg_commands import media as _media_commands
from xg_commands import storage as _storage_commands
from xg_commands import network as _network_commands
from xg_commands import maintenance as _maintenance_commands
from xg_commands import filesystem as _filesystem_commands
from xg_commands import browser as _browser_commands
from xg_commands import input as _input_commands
from xg_commands import desktop as _desktop_commands
from xg_commands import power as _power_commands
from xg_commands import pranks as _prank_commands
from xg_commands import lifecycle as _lifecycle
from xg_commands import startup as _startup_commands
from xg_commands import device_interaction as _device_interaction
from config import (
    CONFIG_DIR,
    set_guardian_desired_running,
    set_guardian_startup_enabled,
    DEFAULT_SHARED_KEY,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    HEARTBEAT_INTERVAL,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_TRANSPORT,
    MQTT_WS_PATH,
    MQTT_USERNAME,
    PLATFORM,
    SHARED_KEY,
    VERSION,
    write_runtime_health,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

MAX_FILE_PUT_BYTES = 30 * 1024 * 1024

MAX_FILE_PUT_B64_CHARS = 4 * ((MAX_FILE_PUT_BYTES + 2) // 3)

MAX_SCREENSHOT_BYTES = 30 * 1024 * 1024

SCREENSHOT_TIMEOUT = 10.0

_SCREENSHOT_CAPTURE_LOCK = threading.Lock()

_OPEN_URL_OPERATION_TTL_SEC = 600.0

_OPEN_URL_OPERATION_LIMIT = 256

_VOLUME_HISTORY_MIN_SPAN_SECONDS = 6 * 60 * 60

_VOLUME_HISTORY_RETENTION_SECONDS = 90 * 24 * 60 * 60

_VOLUME_HISTORY: dict[str, list[dict[str, float | int]]] = {}

_VOLUME_HISTORY_LOCK = threading.Lock()

_MEMORY_PRESSURE_SOURCE = "windows_memory_resource_notification"

_THERMAL_SOURCE = "windows_wmi_MSAcpi_ThermalZoneTemperature"

_BATTERY_HEALTH_SOURCE = "windows_wmi_battery_capacity"

_BATTERY_CYCLE_SOURCE = "windows_wmi_battery_cycle_count"

_SLEEP_WAKE_SOURCE = "windows_system_power_events"

_STARTUP_DURATION_SOURCE = "windows_diagnostics_performance_event_100"

log = logging.getLogger("xgent.wds")

_PRANK_TEMP_PREFIX = "xider-prank-"

_PRANK_TEMP_TTL_SECONDS = 60

MEDIA_REQUESTS = MediaRequestRegistry()

WINDOWS_TASK_NAME = "XIDER Agent"

WINDOWS_GUARDIAN_TASK_NAME = "XIDER Guardian"

_instance_lock_file = None

SUPPORTED_COMMANDS = (
    "open_url",
    "notify",
    "sound",
    "status_request",
    "screenshot",
    "display_catalog",
    "display_resolution",
    "display_contact_sheet",
    "display_screenshot",
    "display_region_screenshot",
    "window_catalog",
    "window_screenshot",
    "media_temp_cleanup",
    "media_history",
    "webcam",
    "camera_series",
    "media_cancel",
    "camera_catalog",
    "camera_quality",
    "microphone_catalog",
    "microphone_level",
    "microphone_balance",
    "audio_output_catalog",
    "audio_output_switch",
    "microphone_test",
    "microphone_preview",
    "sysinfo",
    "sysinfo_interval",
    "network_traffic_interval",
    "cpu_history",
    "memory_history",
    "memory_pressure_history",
    "thermal_snapshot",
    "battery_health",
    "battery_cycles",
    "energy_usage",
    "sleep_wake_history",
    "startup_duration",
    "pending_restart",
    "os_support",
    "hardware_inventory",
    "driver_inventory",
    "installed_app_inventory",
    "startup_inventory",
    "service_inventory",
    "crash_summary",
    "smart_trend",
    "volume_topology",
    "clock_drift",
    "volume_history",
    "processes",
    "battery",
    "geo_location",
    "shell", "shell_cancel",
    "network",
    "services",
    "clipboard",
    "clipboard_set",
    "kill_process",
    "disks",
    "volume_set",
    "mic",
    "open_app",
    "capabilities",
    "permission_settings",
    "lock",
    "volume_toggle",
    "power",
    "stop",
    "agent_logs",
    "agent_files",
    "agent_file_get",
    "dir_list",
    "file_get",
    "file_put",
    "file_del",
    "find_file",
    "path_open",
    "ext_ip",
    "screen_off",
    "screensaver_on",
    "wallpaper_set",
    "msgbox_spam",
    "type_text",
    "hotkey",
    "proc_kill_name",
    "wifi_info",
    "usb_devices",
    "startup_list",
    "env_get",
    "netstat",
    "download_url",
    "prank_screamer",
    "prank_rickroll",
    "prank_matrix",
    "prank_siren",
    "prank_shout_tts",
    "prank_swap_mouse",
    "prank_crazy_cursor",
    "prank_hide_desktop",
    "prank_dancing_windows",
    "prank_black_screen",
    "prank_random_site",
    "display_brightness",
    "display_night_light",
    "display_rotate",
    "net_wifi_passwords",
    "net_bluetooth_list",
    "storage_smart",
    "sys_installed_apps",
    "sys_history_cmd",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep",
    "wake",
    "autorun_status",
    "autorun_enable",
    "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime",
    "sys_clean_temp",
    "net_ping",
    "dns_resolution",
    "dns_settings",
    "dns_comparison",
    "dnssec_check",
    "gateway_probe",
    "route_trace",
    "path_mtu",
    "captive_portal",
    "ipv6_connectivity",
    "tls_certificate",
    "service_probe",
    "latency_series",
    "packet_loss_history",
    "network_jitter",
    "network_throughput",
    "network_link_speed",
    "route_lookup",
    "endpoint_batch",
    "wifi_quality_history",
    "wifi_channel_summary",
    "wifi_connection_history",
    "proxy_status",
    "vpn_route_status",
    "network_traffic",
    "interface_status_history",
    # Расширенная матрица приколов (40 приколов)
    "prank_bsod",
    "prank_fake_update",
    "prank_toast_spam",
    "prank_keyboard_disco",
    "prank_beep_morse",
    "prank_open_notepad_type",
    "prank_hacker_typer",
    "prank_sound_spooky",
    "prank_sound_fart",
    "prank_minimize_all",
    "prank_open_calc_spam",
    "prank_invert_screen",
    "prank_slow_mouse",
    "prank_random_clicks",
    "prank_paste_clipboard_spam",
    "prank_type_reversed",
    "prank_volume_jump",
    "prank_say_whisper",
    "prank_fake_virus",
    "prank_open_cd",
    "prank_change_wallpaper",
    "prank_restore_wallpaper",
    "prank_speak_time",
    "prank_rickroll_terminal",
    "prank_screen_off_brief",
    "prank_alert_loop",
    "prank_open_browser_memes",
    "prank_glitch_cursor",
    "prank_shake_window",
    # Новые мега-приколы и троллинг (до 55 приколов)
    "prank_meme_wallpaper",
    "prank_caps_disco",
    "prank_cursor_circle",
    "prank_fake_error_spam",
    "prank_fake_delete_sys32",
    "prank_ghost_typer",
    "prank_fbi_lock",
    "prank_cat_invaders",
    "prank_fake_ransom_cats",
    "prank_nyan_stream",
    "prank_random_beeps",
    "prank_confetti_winner",
    "prank_low_battery_fake",
    "prank_earthquake",
    "prank_laugh_track",
    "prank_stop_all",
    # Обновление и безопасность
    "agent_update",
    "uninstall_agent",
)

NL_STATE_SUBKEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)

def _run_without_console(function, *args, **kwargs):
    """Run a captured system command without flashing a console window."""
    if os.name == "nt":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return function(*args, **kwargs)


def _read_installed_app_inventory_snapshot() -> dict:
    return _telemetry_installed_app_inventory_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_startup_inventory_snapshot() -> dict:
    return _telemetry_startup_inventory_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_service_inventory_snapshot() -> dict:
    return _telemetry_service_inventory_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_crash_summary_snapshot() -> dict:
    return _telemetry_crash_summary_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_smart_trend_snapshot() -> dict:
    return _telemetry_smart_trend_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_volume_topology_snapshot() -> dict:
    return _telemetry_volume_topology_snapshot(
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_clock_drift_snapshot() -> dict:
    return _telemetry_clock_drift_snapshot(time_module=time, logger=log)


def _read_startup_duration_snapshot() -> dict:
    return _telemetry_startup_duration_snapshot(
        source=_STARTUP_DURATION_SOURCE,
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        parse_output=parse_windows_startup_duration,
        logger=log,
    )


def _read_sleep_wake_history_snapshot() -> dict:
    return _telemetry_sleep_wake_history_snapshot(
        source=_SLEEP_WAKE_SOURCE,
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        parse_output=parse_windows_sleep_wake_events,
        logger=log,
    )


def _read_battery_health_snapshot() -> dict:
    return _telemetry_battery_health_snapshot(
        source=_BATTERY_HEALTH_SOURCE,
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_battery_cycle_snapshot() -> dict:
    return _telemetry_battery_cycle_snapshot(
        source=_BATTERY_CYCLE_SOURCE,
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_energy_usage_snapshot() -> dict:
    """Export SRUM only for this explicit request and return no raw rows or IDs."""
    source = "windows_srum_energy_estimation"
    powercfg = shutil.which("powercfg.exe") or shutil.which("powercfg")
    if not powercfg:
        return {"state": "unsupported", "source": source, "apps": []}
    export_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="xider-energy-", suffix=".csv", delete=False) as handle:
            export_path = handle.name
        completed = _run_without_console(subprocess.run,
            [powercfg, "/srumutil", "/output", export_path, "/csv"],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not os.path.isfile(export_path):
            return {"state": "unknown", "source": source, "apps": []}
        if os.path.getsize(export_path) > 8 * 1024 * 1024:
            return {"state": "unknown", "source": source, "apps": []}
        with open(export_path, "rb") as report:
            raw = report.read(8 * 1024 * 1024 + 1)
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16", errors="strict")
        else:
            text = raw.decode("utf-8-sig", errors="strict")
        return parse_srum_energy_csv(text)
    except (OSError, UnicodeError, subprocess.SubprocessError, csv.Error, ValueError):
        log.info("Windows SRUM energy estimate unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "apps": []}
    finally:
        if export_path:
            try:
                os.unlink(export_path)
            except OSError:
                log.warning("Unable to remove temporary SRUM export file")


def _read_thermal_snapshot() -> dict:
    return _telemetry_thermal_snapshot(
        source=_THERMAL_SOURCE,
        find_executable=shutil.which,
        run_without_console=_run_without_console,
        subprocess_module=subprocess,
        logger=log,
    )


def _read_memory_pressure_sample() -> dict:
    return _telemetry_read_memory_pressure_sample()


def _memory_pressure_history_sample(raw: dict) -> dict:
    return _telemetry_memory_pressure_history_sample(raw)


def _volume_history_measurement(device: str, mountpoint: str, total: int, used: int, now: float) -> dict:
    return _telemetry_volume_history_measurement(
        device, mountpoint, total, used, now,
        history=_VOLUME_HISTORY,
        history_lock=_VOLUME_HISTORY_LOCK,
        min_span_seconds=_VOLUME_HISTORY_MIN_SPAN_SECONDS,
        retention_seconds=_VOLUME_HISTORY_RETENTION_SECONDS,
    )


def _bounded_screen_grab():
    return _ui_bounded_screen_grab(_SCREENSHOT_CAPTURE_LOCK, SCREENSHOT_TIMEOUT, log)


def _screenshot_session_available() -> bool:
    return _ui_screenshot_session_available()


def _validated_open_url(payload: dict) -> tuple[str, int]:
    return _util_validated_open_url(payload)


def _decode_file_put_payload(payload: dict) -> bytes:
    return _util_decode_file_put_payload(
        payload, max_bytes=MAX_FILE_PUT_BYTES, max_b64_chars=MAX_FILE_PUT_B64_CHARS,
    )


def _write_file_put_atomic(path: str, content: bytes) -> None:
    _util_write_file_put_atomic(path, content)


def _remove_prank_temp_file(path: str) -> None:
    target = Path(path)
    if not target.name.startswith(_PRANK_TEMP_PREFIX) or target.suffix.lower() != ".html":
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        log.warning("Unable to remove expired XIDER prank page")


def _schedule_prank_temp_cleanup(path: str) -> None:
    target = Path(path)
    if not target.name.startswith(_PRANK_TEMP_PREFIX) or target.suffix.lower() != ".html":
        return
    timer = threading.Timer(_PRANK_TEMP_TTL_SECONDS, _remove_prank_temp_file, args=(str(target),))
    timer.daemon = True
    timer.start()


def _scheduled_task_exists(task_name: str = WINDOWS_TASK_NAME) -> bool:
    """Проверить реальный Scheduled Task, которым устанавливается агент."""
    result = _run_without_console(subprocess.run,
        ["schtasks.exe", "/Query", "/TN", task_name],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def _remove_scheduled_task(
    task_name: str = WINDOWS_TASK_NAME, *, stop_running: bool = True,
) -> tuple[bool, str]:
    return _lifecycle.remove_scheduled_task(
        task_name, stop_running=stop_running, run_without_console=_run_without_console,
        run=subprocess.run, scheduled_task_exists=_scheduled_task_exists,
    )


def _regional_settings_snapshot(request_id: object = None) -> dict:
    """Read timezone/locale once without changing the process or OS settings."""
    return _telemetry_regional_settings_snapshot(
        request_id,
        device_id=DEVICE_ID,
        datetime_module=datetime,
        locale_module=locale,
        re_module=re,
    )


def _list_directory(path: str) -> dict:
    """Чистая функция: список файлов папки (имя, тип, размер)."""
    return _util_list_directory(path, os_module=os)


def _find_matches(pattern: str, root: str, limit: int = 30, max_depth: int = 4) -> dict:
    """Чистая функция: поиск файлов по подстроке имени (без сторонних зависимостей)."""
    return _util_find_matches(pattern, root, limit, max_depth, os_module=os)


def _kill_by_name(name: str) -> dict:
    """Compatibility alias for callers that imported the previous helper."""
    return _system_info_commands.kill_by_name(name, os_module=os, psutil_module=psutil)


def _rotate_screen(angle: int) -> tuple[bool, str]:
    """Повернуть экран через ChangeDisplaySettingsEx. angle: 0/90/180/270."""
    return _ui_rotate_screen(angle, orientation_value=_orientation_value)


def _read_current_display_angle() -> int | None:
    """Return the current primary-display orientation in degrees, if available."""
    return _ui_read_current_display_angle()


def create_image():
    """Иконка для трея: синий круг с белой кнопкой «play» (64x64)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(30, 144, 255, 255))
    draw.ellipse((9, 9, 55, 55), outline=(255, 255, 255, 255), width=3)
    draw.polygon([(28, 21), (28, 43), (48, 32)], fill=(255, 255, 255, 255))
    return img


def _detect_features() -> dict:
    return _core_detect_features(psutil)


def _module_available(name: str) -> bool:
    return _core_module_available(name, importlib.util.find_spec)


def _detect_feature_status() -> dict[str, str]:
    """Report detected prerequisites; hardware and privacy settings stay unverified."""
    return _core_detect_feature_status(_detect_features(), _module_available)


def _frozen_update_operation_id(payload, context_id=None):
    """Keep rejection correlation available without importing source updater modules."""
    for candidate in (payload.get("id"), context_id, payload.get("operation_id")):
        if isinstance(candidate, str) and 1 <= len(candidate) <= 128:
            return candidate
    return str(uuid.uuid4())


def _capability_snapshot(feature_status: dict[str, str]) -> dict:
    """Passive diagnostics only: prerequisites are never an OS permission grant."""
    from location_adapter import location_capability

    return _core_capability_snapshot(
        feature_status,
        platform=PLATFORM,
        location_capability=location_capability,
        time_module=time,
    )


def ctypes_windll_user32_message_box(text: str) -> None:
    """Обертка над MessageBoxW для тестируемости."""
    ctypes.windll.user32.MessageBoxW(None, text, "XGENT", 0x40)



_FUNCTION_NAMES = ('_run_without_console', '_read_installed_app_inventory_snapshot', '_read_startup_inventory_snapshot', '_read_service_inventory_snapshot', '_read_crash_summary_snapshot', '_read_smart_trend_snapshot', '_read_volume_topology_snapshot', '_read_clock_drift_snapshot', '_read_startup_duration_snapshot', '_read_sleep_wake_history_snapshot', '_read_battery_health_snapshot', '_read_battery_cycle_snapshot', '_read_energy_usage_snapshot', '_read_thermal_snapshot', '_read_memory_pressure_sample', '_memory_pressure_history_sample', '_volume_history_measurement', '_bounded_screen_grab', '_screenshot_session_available', '_validated_open_url', '_decode_file_put_payload', '_write_file_put_atomic', '_remove_prank_temp_file', '_schedule_prank_temp_cleanup', '_scheduled_task_exists', '_remove_scheduled_task', '_regional_settings_snapshot', '_list_directory', '_find_matches', '_kill_by_name', '_rotate_screen', '_read_current_display_angle', 'create_image', '_detect_features', '_module_available', '_detect_feature_status', '_frozen_update_operation_id', '_capability_snapshot', 'ctypes_windll_user32_message_box')


def install(runtime_globals):
    """Restore the legacy xgent_wds namespace and bind adapters into it."""
    excluded = {"FunctionType", "install", "_FUNCTION_NAMES", "__builtins__"}
    for name, value in tuple(globals().items()):
        if not name.startswith("__") and name not in excluded and name not in _FUNCTION_NAMES:
            runtime_globals[name] = value
    for name in _FUNCTION_NAMES:
        function = globals()[name]
        runtime_function = FunctionType(
            function.__code__, runtime_globals, function.__name__, function.__defaults__, function.__closure__
        )
        runtime_function.__kwdefaults__ = function.__kwdefaults__
        runtime_function.__annotations__ = function.__annotations__
        runtime_globals[name] = runtime_function
