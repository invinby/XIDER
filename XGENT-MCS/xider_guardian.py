"""XIDER Guardian: прозрачный supervisor для macOS-агента.

Guardian работает отдельным видимым LaunchAgent. Он не выполняет shell-команды
из Telegram и не собирает камеру/микрофон/геолокацию. Его задача — держать
MQTT-связь, сообщать состояние и по явно включённой политике перезапускать
рабочий xgent_mcs.py.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
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
from guardian_recovery import RECOVERY_FILES, restore_missing_agent_files
from update_package import UPDATE_STATE_NAME, _read_update_state, rollback_unhealthy_agent
from xgencrypto import decrypt_payload, encrypt_payload

try:
    import fcntl
except ImportError:
    fcntl = None

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
        self._intent_file_required = True
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

    def _require_running_intent(self) -> None:
        """Recovery is subordinate to the owner's explicit stop/uninstall."""
        if getattr(self, "_intent_file_required", False) and not STATE_FILE.is_file():
            raise RuntimeError("Состояние Guardian удалено; восстановление и запуск отключены.")
        if hasattr(self, "state"):
            self._refresh_worker_intent()
        if not getattr(self, "state", {}).get("desired_running", True):
            raise RuntimeError("Агент явно остановлен; восстановление и запуск отключены.")

    def _restore_missing_agent_files(self) -> list[str]:
        """Fill missing source files only from a verified local release package."""
        self._require_running_intent()
        required_env = (
            "SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS",
            "ENCRYPT_PAYLOAD", "MQTT_USERNAME", "MQTT_PASSWORD",
        )
        missing_env = [key for key in required_env if not os.environ.get(key, "").strip()]
        if not (SCRIPT_DIR / ".env").is_file() and missing_env:
            raise RuntimeError(
                "Автовосстановление остановлено: .env (нет параметров: "
                + ", ".join(missing_env) + "). Секреты из recovery-пакета не пересоздаются; "
                "нужна проверенная повторная установка X-DOCK."
            )
        with self._recover_missing_worker_transaction():
            return restore_missing_agent_files(
                SCRIPT_DIR, CONFIG_DIR / "recovery", RECOVERY_FILES,
                before_restore=self._require_running_intent,
            )

    @contextmanager
    def _recover_missing_worker_transaction(self):
        """Let an idle interrupted update roll back before cache gap filling.

        A live worker/updater owns this same flock. Its files and journal must
        remain untouched, even if process-name discovery temporarily misses it.
        The lock is released before start_agent spawns the replacement worker.
        """
        worker_path = SCRIPT_DIR / "xgent_mcs.py"
        journal = CONFIG_DIR / UPDATE_STATE_NAME
        if worker_path.is_file() or not (journal.exists() or journal.is_symlink()):
            yield
            return
        if fcntl is None:
            raise RuntimeError("Не доступен singleton flock агента; откат Guardian отключён.")
        lock_path = Path(tempfile.gettempdir()) / "xgent_mcs.lock"
        if lock_path.is_symlink():
            raise ValueError("Singleton lock агента не может быть symlink.")
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Агент или обновление удерживает singleton lock; файлы не изменены.") from exc
            try:
                self._require_running_intent()
                # Another valid start may have completed before this lock was
                # claimed. Only the missing-worker journal case is ours to fix.
                if not worker_path.is_file():
                    state = _read_update_state(journal, SCRIPT_DIR)
                    if state is not None:
                        self._require_running_intent()
                        result = rollback_unhealthy_agent(journal, SCRIPT_DIR)
                        log.info("Rolled back interrupted missing-worker update: %s", result)
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def start_agent(self) -> int | None:
        self._require_running_intent()
        pid = self.agent_pid()
        if pid:
            return pid
        restored = self._restore_missing_agent_files()
        if restored:
            log.info("Restored signed release files: %s", ", ".join(restored))
        self._require_running_intent()
        env = os.environ.copy()
        env["XIDER_NO_AUTOSTART"] = "1"
        log_path = SCRIPT_DIR / "agent.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                [self._worker_python(), str(SCRIPT_DIR / "xgent_mcs.py")],
                cwd=str(SCRIPT_DIR), env=env,
                stdout=log_handle, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
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
