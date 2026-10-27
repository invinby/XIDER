"""XIDER Guardian for Windows.

Отдельный supervisor для Windows-агента. Он не выполняет произвольный shell:
его задача — держать MQTT-связь, показывать состояние и по явно включённой
политике запускать обратно только XGENT-WDS.
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
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
    write_runtime_health,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xider.guardian.wds")
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = CONFIG_DIR / "guardian.json"
LEGACY_STATE_FILE = CONFIG_DIR / "guardian-windows.json"
AGENT_TASK = "XIDER Agent"
GUARDIAN_TASK = "XIDER Guardian"
AGENT_STABLE_SECONDS = 60.0
AGENT_HEALTH_STALE_SECONDS = 180.0


def _load_state() -> dict:
    for path in (STATE_FILE, LEGACY_STATE_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    return {
        "state_version": 2, "auto_restart": True,
        "desired_running": True, "startup_enabled": True,
    }


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


def _task_exists(task_name: str) -> bool | None:
    """Return None when Task Scheduler could not prove either state."""
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", task_name],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return True
    message = f"{result.stdout or ''}\n{result.stderr or ''}".casefold()
    missing_markers = (
        "cannot find the file specified",
        "the system cannot find",
        "не удается найти указанный файл",
        "не удаётся найти указанный файл",
        "системе не удается найти",
        "системе не удаётся найти",
    )
    if any(marker in message for marker in missing_markers):
        return False
    return None


class Guardian:
    def __init__(self) -> None:
        self.state = _load_state()
        original_state = dict(self.state)
        try:
            state_version = int(self.state.get("state_version", 1))
        except (TypeError, ValueError):
            state_version = 1
        if state_version < 2:
            # Earlier Windows installs shipped with recovery disabled by default.
            # Turn it on once, while preserving an explicit stopped-worker state.
            self.state["auto_restart"] = True
            self.state["state_version"] = 2
        self.state.setdefault("auto_restart", True)
        self.state.setdefault("desired_running", True)
        self.state.setdefault("startup_enabled", True)
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
        # Once initialized, a missing state file means the owner removed the
        # Guardian installation. Do not infer permission to recreate the worker.
        self._intent_file_required = True
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error: str | None = None
        self._observed_agent_pid: int | None = None
        self._observed_agent_since = 0.0
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xider-guardian-windows-{DEVICE_ID}",
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
        payload = dict(body)
        payload.setdefault("type", "guardian")
        payload.setdefault("device_id", DEVICE_ID)
        info = self.client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/guardian", self._envelope(payload), qos=1
        )
        try:
            info.wait_for_publish(timeout=3)
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.warning("Guardian MQTT connection failed: %s", reason_code)
            try:
                write_runtime_health("guardian", False)
            except Exception:
                log.exception("Could not update local Guardian MQTT health")
            return
        client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=1)
        client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=1)
        self._publish(self.status_payload())
        try:
            write_runtime_health("guardian", True)
        except Exception:
            log.exception("Could not write local Guardian MQTT health")
        log.info("Windows Guardian connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        try:
            write_runtime_health("guardian", False)
        except Exception:
            log.exception("Could not reset local Guardian MQTT health")
        log.warning("Windows Guardian MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            payload = verify_message(json.loads(message.payload.decode("utf-8")))
            if payload is None:
                return
            if ENCRYPT_PAYLOAD or "enc" in payload:
                payload = decrypt_payload(payload)
            if isinstance(payload, dict) and payload.get("action") == "guardian":
                threading.Thread(target=self.handle, args=(payload,), daemon=True).start()
        except Exception:
            log.exception("Guardian command handling failed")

    def agent_process(self) -> psutil.Process | None:
        current = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["pid"] == current:
                    continue
                name = str(proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "xgent-wds.exe" in name or "xgent_wds.py" in cmdline:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _agent_health_is_stale(self, pid: int) -> bool:
        """Treat only this Agent PID's old local heartbeat as an unresponsive worker."""
        try:
            path = CONFIG_DIR / "agent-health.json"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                return False
            health = json.loads(path.read_text(encoding="utf-8"))
            stamp = health.get("updated_at") if isinstance(health, dict) else None
            return (
                health.get("component") == "agent"
                and health.get("pid") == pid
                and type(stamp) in (int, float)
                and 0 <= time.time() - stamp > AGENT_HEALTH_STALE_SECONDS
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _terminate_unresponsive_agent(self, proc: psutil.Process) -> None:
        """Stop only the process currently identified as the XIDER Agent."""
        current = self.agent_process()
        if current is None or current.pid != proc.pid:
            return
        try:
            current.terminate()
            try:
                current.wait(timeout=5)
            except psutil.TimeoutExpired:
                current.kill()
                current.wait(timeout=5)
        except psutil.NoSuchProcess:
            return

    def _agent_command(self) -> list[str]:
        exe = SCRIPT_DIR / "XGENT-WDS.exe"
        if exe.exists():
            return [str(exe)]
        python = SCRIPT_DIR / "venv" / "Scripts" / "python.exe"
        if not python.exists():
            python = Path(sys.executable)
        return [str(python), str(SCRIPT_DIR / "xgent_wds.py")]

    def status_payload(self) -> dict:
        proc = self.agent_process()
        agent_task_loaded = _task_exists(AGENT_TASK)
        guardian_task_loaded = _task_exists(GUARDIAN_TASK)
        def task_label(value: bool | None) -> str:
            if value is None:
                return "статус неизвестен"
            return "зарегистрирована" if value else "не зарегистрирована"
        return {
            "type": "guardian",
            "device_id": DEVICE_ID,
            "ok": True,
            "version": VERSION,
            "guardian": "1.0-windows",
            "agent_running": bool(proc),
            "agent_pid": proc.pid if proc else None,
            "launchd_loaded": agent_task_loaded,
            "guardian_task_registered": guardian_task_loaded,
            "auto_restart": bool(self.state.get("auto_restart")),
            "desired_running": bool(self.state.get("desired_running")),
            "text": (
                f"🛡 Windows Guardian 1.0 · {DEVICE_NAME}\n"
                f"Агент: {'онлайн' if proc else 'остановлен'}\n"
                f"Задача агента: {task_label(agent_task_loaded)}\n"
                f"Задача Guardian: {task_label(guardian_task_loaded)}\n"
                f"Автовосстановление: {'включено' if self.state.get('auto_restart') else 'выключено'}\n"
                f"Запуск агента: {'разрешён' if self.state.get('desired_running') else 'остановлен владельцем'}"
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

    def _require_running_intent(self) -> None:
        """Recovery remains subordinate to the owner's persisted intent."""
        if getattr(self, "_intent_file_required", False) and not STATE_FILE.is_file():
            raise RuntimeError("Guardian state was removed; worker recovery is disabled.")
        self._refresh_worker_intent()
        if not getattr(self, "state", {}).get("desired_running", True):
            raise RuntimeError("Agent was explicitly stopped; worker recovery is disabled.")

    def start_agent(self) -> int | None:
        self._require_running_intent()
        proc = self.agent_process()
        if proc:
            return proc.pid
        env = os.environ.copy()
        env["XIDER_NO_AUTOSTART"] = "1"
        log_path = CONFIG_DIR / "xgent.log"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8")
        child = subprocess.Popen(
            self._agent_command(), cwd=str(SCRIPT_DIR), env=env,
            stdout=handle, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self.state["desired_running"] = True
        _save_state(self.state, "desired_running")
        return child.pid

    def stop_agent(self) -> None:
        proc = self.agent_process()
        self.state["desired_running"] = False
        _save_state(self.state, "desired_running")
        # Stop the scheduled task first so its restart-on-failure policy does not
        # race the owner's explicit stop command.
        if _task_exists(AGENT_TASK) is True:
            subprocess.run(
                ["schtasks.exe", "/End", "/TN", AGENT_TASK],
                capture_output=True, text=True, check=False,
            )
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (psutil.TimeoutExpired, psutil.NoSuchProcess):
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass

    def _reset_recovery_backoff(self) -> None:
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error = None

    def _observe_agent_process(self, proc: psutil.Process | int | None, now: float) -> None:
        previous_pid = getattr(self, "_observed_agent_pid", None)
        started_at = getattr(self, "_observed_agent_since", 0.0)
        pid = proc if isinstance(proc, int) else proc.pid if proc else None
        if pid is None:
            if previous_pid is None:
                return
            self._observed_agent_pid = None
            self._observed_agent_since = 0.0
            if now - started_at < AGENT_STABLE_SECONDS:
                self._record_recovery_failure(
                    RuntimeError(f"агент PID {previous_pid} завершился до стабильного запуска")
                )
            else:
                self._reset_recovery_backoff()
            return
        if previous_pid != pid:
            self._observed_agent_pid = pid
            self._observed_agent_since = now
        elif now - started_at >= AGENT_STABLE_SECONDS:
            self._reset_recovery_backoff()

    def _record_recovery_failure(self, exc: Exception) -> None:
        message = str(exc)
        delay = getattr(self, "_restart_backoff_seconds", 5.0)
        self._next_restart_at = time.monotonic() + delay
        self._restart_backoff_seconds = min(delay * 2, 300.0)
        if message != getattr(self, "_last_recovery_error", None):
            self._publish({"ok": False, "text": f"⚠️ Windows Guardian не смог стабильно запустить агент: {message}"})
            self._last_recovery_error = message
        log.warning("Windows Guardian restart failed; retry in %.0f seconds: %s", delay, message)

    def handle(self, payload: dict) -> None:
        lock = getattr(self, "lock", None)
        if lock is None:
            lock = threading.RLock()
            self.lock = lock
        with lock:
            self._handle_locked(payload)

    def _handle_locked(self, payload: dict) -> None:
        command = str(payload.get("command") or "status").lower()
        if command == "status":
            result = self.status_payload()
        elif command == "start":
            self._reset_recovery_backoff()
            self.state["desired_running"] = True
            _save_state(self.state, "desired_running")
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"✅ Windows-агент запущен Guardian (PID {pid or '?'})"
        elif command == "stop":
            self.stop_agent()
            result = self.status_payload()
            result["text"] = "⏹ Windows-агент остановлен. Guardian остаётся доступен."
        elif command == "restart":
            self._reset_recovery_backoff()
            self.stop_agent()
            self.state["desired_running"] = True
            _save_state(self.state, "desired_running")
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"🔄 Windows-агент перезапущен Guardian (PID {pid or '?'})"
        elif command == "auto_restart":
            enabled = bool(payload.get("enabled"))
            self.state["auto_restart"] = enabled
            _save_state(self.state, "auto_restart")
            result = self.status_payload()
            result["text"] = f"🛡 Windows Guardian: {'автовосстановление ВКЛ' if enabled else 'автовосстановление ВЫКЛ'}"
        else:
            result = {"ok": False, "text": f"Неизвестная команда Guardian: {command}"}
        if payload.get("id"):
            result["id"] = payload["id"]
        self._publish(result)

    def monitor(self) -> None:
        while not self.stop_event.wait(5):
            with self.lock:
                self._refresh_worker_intent()
                if not (self.state.get("auto_restart") and self.state.get("desired_running")):
                    continue
                now = time.monotonic()
                proc = self.agent_process()
                if proc and self._agent_health_is_stale(proc.pid):
                    log.warning("Windows Agent PID %s heartbeat is stale; restarting the unresponsive worker", proc.pid)
                    try:
                        self._terminate_unresponsive_agent(proc)
                        proc = self.agent_process()
                        if proc:
                            raise RuntimeError("зависший процесс агента не завершился")
                    except Exception as exc:
                        log.exception("Windows Guardian could not stop stale Agent")
                        self._record_recovery_failure(exc)
                        continue
                self._observe_agent_process(proc, now)
                if proc or now < self._next_restart_at:
                    continue
                try:
                    pid = self.start_agent()
                    if not pid:
                        raise RuntimeError("процесс агента не появился после команды запуска")
                    self._observe_agent_process(pid, time.monotonic())
                    self._publish({"ok": True, "text": f"🛡 Windows Guardian запустил агент (PID {pid}); проверяю стабильность."})
                except Exception as exc:
                    log.exception("Windows Guardian restart failed")
                    self._record_recovery_failure(exc)

    def run(self) -> None:
        self.client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        threading.Thread(target=self.monitor, daemon=True, name="xider-guardian-monitor").start()
        try:
            while not self.stop_event.wait(1):
                pass
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XIDER Windows Guardian")
    parser.add_argument(
        "--set-desired-running",
        choices=("start", "stop"),
        help="Persist the owner's worker start/stop intent, then exit without starting Guardian.",
    )
    args = parser.parse_args(argv)
    if args.set_desired_running:
        set_guardian_desired_running(args.set_desired_running == "start")
        return 0

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        CONFIG_DIR / "guardian.log", maxBytes=2 * 1024 * 1024,
        backupCount=3, encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )
    Guardian().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
