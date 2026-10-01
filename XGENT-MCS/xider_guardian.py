"""XIDER Guardian: прозрачный supervisor для macOS-агента.

Guardian работает отдельным видимым LaunchAgent. Он не выполняет shell-команды
из Telegram и не собирает камеру/микрофон/геолокацию. Его задача — держать
MQTT-связь, сообщать состояние и по явно включённой политике перезапускать
рабочий xgent_mcs.py.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import psutil

from config import (
    CONFIG_DIR,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_USERNAME,
    VERSION,
    set_guardian_desired_running,
    set_guardian_startup_enabled,
    update_guardian_state,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xider.guardian")
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = CONFIG_DIR / "guardian.json"


def _load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"state_version": 2, "auto_restart": True, "desired_running": True, "startup_enabled": True}


def _save_state(data: dict, *fields: str) -> None:
    """Persist selected fields with the same lock used by the worker config."""
    if not fields or any(field not in data for field in fields):
        raise ValueError("Guardian state update must name existing fields.")
    merged = update_guardian_state(
        initial_state=data,
        **{field: data[field] for field in fields},
    )
    data.clear()
    data.update(merged)


class Guardian:
    def __init__(self) -> None:
        self.state = _load_state()
        original_state = dict(self.state)
        self.state.setdefault("auto_restart", True)
        self.state.setdefault("desired_running", True)
        self.state.setdefault("startup_enabled", True)
        try:
            state_version = int(self.state.get("state_version", 1))
        except (TypeError, ValueError):
            state_version = 1
        if state_version < 2:
            # Older Guardians shipped with auto-recovery disabled by default.
            # Enable the new recovery policy once, but keep an explicit stop.
            self.state["auto_restart"] = True
            self.state["state_version"] = 2
        current_boot = int(psutil.boot_time())
        try:
            previous_boot = int(self.state.get("boot_time", current_boot))
        except (TypeError, ValueError):
            previous_boot = current_boot
        if previous_boot != current_boot:
            # Apply boot-autostart policy only on a new OS boot, not whenever
            # Guardian itself is restarted during the current session.
            self.state["desired_running"] = bool(self.state["startup_enabled"])
        self.state["boot_time"] = current_boot
        changed_fields = tuple(
            field for field, value in self.state.items()
            if field not in original_state or original_state[field] != value
        )
        if changed_fields:
            _save_state(self.state, *changed_fields)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error: str | None = None
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xider-guardian-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
        )
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        if MQTT_TLS:
            self.client.tls_set()
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    def _envelope(self, body: dict) -> str:
        payload = encrypt_payload(body) if ENCRYPT_PAYLOAD else body
        return json.dumps(sign_message(payload), ensure_ascii=False)

    def _publish(self, body: dict) -> None:
        body = dict(body)
        body.setdefault("type", "guardian")
        body.setdefault("device_id", DEVICE_ID)
        info = self.client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/guardian",
            self._envelope(body),
            qos=1,
        )
        try:
            info.wait_for_publish(timeout=3)
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.warning("Guardian MQTT connection failed: %s", reason_code)
            return
        client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=1)
        client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=1)
        self._publish(self.status_payload())
        log.info("Guardian connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        log.warning("Guardian MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
            payload = verify_message(envelope)
            if payload is None:
                return
            if ENCRYPT_PAYLOAD or "enc" in payload:
                payload = decrypt_payload(payload)
            if not isinstance(payload, dict) or payload.get("action") != "guardian":
                return
            threading.Thread(target=self.handle, args=(payload,), daemon=True).start()
        except Exception:
            log.exception("Guardian command handling failed")

    def agent_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["pgrep", "-f", str(SCRIPT_DIR / "xgent_mcs.py")],
                capture_output=True, text=True, check=False,
            )
            for line in result.stdout.splitlines():
                try:
                    pid = int(line.strip())
                except ValueError:
                    continue
                if pid != os.getpid():
                    return pid
        except OSError:
            pass
        return None

    def launchd_loaded(self) -> bool:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.xgent.agent"],
            capture_output=True, check=False,
        )
        return result.returncode == 0

    def status_payload(self) -> dict:
        pid = self.agent_pid()
        supervisor_loaded = self.launchd_loaded()
        return {
            "type": "guardian",
            "device_id": DEVICE_ID,
            "ok": True,
            "version": VERSION,
            "guardian": "1.0",
            "agent_running": bool(pid),
            "agent_pid": pid,
            "launchd_loaded": supervisor_loaded,
            "guardian_task_registered": supervisor_loaded,
            "auto_restart": bool(self.state.get("auto_restart")),
            "desired_running": bool(self.state.get("desired_running")),
            "text": (
                f"🛡 Guardian 1.0 · {DEVICE_NAME}\n"
                f"Агент: {'онлайн' if pid else 'остановлен'}"
            ),
        }

    def _refresh_worker_intent(self) -> None:
        """Observe explicit stop/autostart changes made by the worker process."""
        try:
            external = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(external, dict):
            return
        for key in ("desired_running", "startup_enabled"):
            if key in external:
                self.state[key] = bool(external[key])

    def _worker_python(self) -> str:
        candidate = SCRIPT_DIR / "venv" / "bin" / "python3"
        return str(candidate if candidate.exists() else Path(sys.executable))

    def _restore_missing_agent_files(self) -> list[str]:
        """Fail closed until a signed, immutable recovery package is installed."""
        missing = [name for name in (
            "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
            "release_signature.py", "update_package.py",
        )
                   if not (SCRIPT_DIR / name).is_file()]
        required_env = ("SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS", "ENCRYPT_PAYLOAD")
        missing_env = [key for key in required_env if not os.environ.get(key, "").strip()]
        if not (SCRIPT_DIR / ".env").is_file() and missing_env:
            missing.append(".env (нет параметров: " + ", ".join(missing_env) + ")")
        if not missing:
            return []
        raise RuntimeError(
            "Автовосстановление остановлено: отсутствуют " + ", ".join(missing) +
            ". Подписанный recovery-пакет ещё не установлен; .env из памяти не пересоздаётся. "
            "Нужна проверенная повторная установка X-DOCK."
        )

    def start_agent(self) -> int | None:
        pid = self.agent_pid()
        if pid:
            return pid
        self._restore_missing_agent_files()
        env = os.environ.copy()
        env["XIDER_NO_AUTOSTART"] = "1"
        log_path = SCRIPT_DIR / "agent.log"
        log_handle = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [self._worker_python(), str(SCRIPT_DIR / "xgent_mcs.py")],
            cwd=str(SCRIPT_DIR), env=env,
            stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.state["desired_running"] = True
        _save_state(self.state, "desired_running")
        return proc.pid

    def _reset_recovery_backoff(self) -> None:
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error = None

    def _record_recovery_failure(self, exc: Exception) -> None:
        message = str(exc)
        delay = self._restart_backoff_seconds
        self._next_restart_at = time.monotonic() + delay
        self._restart_backoff_seconds = min(delay * 2, 300.0)
        if message != self._last_recovery_error:
            self._publish({"ok": False, "text": f"⚠️ Guardian не смог запустить агент: {message}"})
            self._last_recovery_error = message
        log.warning("Guardian restart failed; retry in %.0f seconds: %s", delay, message)

    def stop_agent(self) -> None:
        # Перед остановкой выгружаем старый LaunchAgent, иначе его KeepAlive
        # мгновенно поднимет рабочий процесс обратно. Сам Guardian остаётся
        # загруженным и поэтому может запустить агент снова из Telegram.
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}",
             str(Path.home() / "Library" / "LaunchAgents" / "com.xgent.agent.plist")],
            capture_output=True, check=False,
        )
        try:
            (Path.home() / "Library" / "LaunchAgents" / "com.xgent.agent.plist").unlink()
        except FileNotFoundError:
            pass
        pid = self.agent_pid()
        self.state["desired_running"] = False
        _save_state(self.state, "desired_running")
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 5
        while time.time() < deadline and self.agent_pid():
            time.sleep(0.2)
        pid = self.agent_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def handle(self, payload: dict) -> None:
        lock = getattr(self, "lock", None)
        if lock is None:
            lock = threading.RLock()
            self.lock = lock
        with lock:
            self._handle_locked(payload)

    def _handle_locked(self, payload: dict) -> None:
        command = str(payload.get("command") or "status").lower()
        try:
            if command == "status":
                result = self.status_payload()
            elif command == "start":
                self._reset_recovery_backoff()
                self.state["desired_running"] = True
                _save_state(self.state, "desired_running")
                pid = self.start_agent()
                result = self.status_payload()
                result["text"] = f"✅ Агент запущен Guardian (PID {pid or '?'})"
            elif command == "stop":
                self.stop_agent()
                result = self.status_payload()
                result["text"] = "⏹ Рабочий агент остановлен. Guardian остаётся доступен."
            elif command == "restart":
                self._reset_recovery_backoff()
                self.stop_agent()
                self.state["desired_running"] = True
                _save_state(self.state, "desired_running")
                pid = self.start_agent()
                result = self.status_payload()
                result["text"] = f"🔄 Агент перезапущен Guardian (PID {pid or '?'})"
            elif command == "auto_restart":
                enabled = bool(payload.get("enabled"))
                self.state["auto_restart"] = enabled
                _save_state(self.state, "auto_restart")
                result = self.status_payload()
                result["text"] = f"🛡 Автовосстановление: {'ВКЛ' if enabled else 'ВЫКЛ'}"
            else:
                result = {"ok": False, "text": f"Неизвестная команда Guardian: {command}"}
        except Exception as exc:
            log.exception("Guardian command failed: %s", command)
            result = {"ok": False, "text": f"⚠️ Guardian не выполнил команду: {exc}"}
        if payload.get("id"):
            result["id"] = payload["id"]
        self._publish(result)

    def monitor(self) -> None:
        while not self.stop_event.wait(5):
            with self.lock:
                self._refresh_worker_intent()
                if self.state.get("auto_restart") and self.state.get("desired_running"):
                    if time.monotonic() < self._next_restart_at or self.agent_pid():
                        continue
                    try:
                        pid = self.start_agent()
                        self._reset_recovery_backoff()
                        self._publish({"ok": True, "text": f"🛡 Guardian восстановил агент (PID {pid or '?'})"})
                    except Exception as exc:
                        self._record_recovery_failure(exc)

    def run(self) -> None:
        self.client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        threading.Thread(target=self.monitor, daemon=True).start()
        try:
            while not self.stop_event.wait(1):
                pass
        finally:
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    Guardian().run()
