"""XIDER Guardian for Windows.

Отдельный supervisor для Windows-агента. Он не выполняет произвольный shell:
его задача — держать MQTT-связь, показывать состояние и по явно включённой
политике запускать обратно только XGENT-WDS.
"""

from __future__ import annotations

import json
import logging
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
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xider.guardian.wds")
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = CONFIG_DIR / "guardian-windows.json"
AGENT_TASK = "XIDER Agent"
GUARDIAN_TASK = "XIDER Guardian"


def _load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"auto_restart": False, "desired_running": True}


def _save_state(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _task_exists(task_name: str) -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


class Guardian:
    def __init__(self) -> None:
        self.state = _load_state()
        self.state.setdefault("auto_restart", False)
        self.state.setdefault("desired_running", True)
        _save_state(self.state)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
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
            return
        client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=1)
        client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=1)
        self._publish(self.status_payload())
        log.info("Windows Guardian connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
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
        task_loaded = _task_exists(GUARDIAN_TASK)
        return {
            "type": "guardian",
            "device_id": DEVICE_ID,
            "ok": True,
            "version": VERSION,
            "guardian": "1.0-windows",
            "agent_running": bool(proc),
            "agent_pid": proc.pid if proc else None,
            "launchd_loaded": task_loaded,
            "auto_restart": bool(self.state.get("auto_restart")),
            "desired_running": bool(self.state.get("desired_running")),
            "text": (
                f"🛡 Windows Guardian 1.0 · {DEVICE_NAME}\n"
                f"Агент: {'онлайн' if proc else 'остановлен'}\n"
                f"Задача Guardian: {'зарегистрирована' if task_loaded else 'не зарегистрирована'}"
            ),
        }

    def start_agent(self) -> int | None:
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
        _save_state(self.state)
        return child.pid

    def stop_agent(self) -> None:
        proc = self.agent_process()
        self.state["desired_running"] = False
        _save_state(self.state)
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

    def handle(self, payload: dict) -> None:
        command = str(payload.get("command") or "status").lower()
        if command == "status":
            result = self.status_payload()
        elif command == "start":
            self.state["desired_running"] = True
            _save_state(self.state)
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"✅ Windows-агент запущен Guardian (PID {pid or '?'})"
        elif command == "stop":
            self.stop_agent()
            result = self.status_payload()
            result["text"] = "⏹ Windows-агент остановлен. Guardian остаётся доступен."
        elif command == "restart":
            self.stop_agent()
            self.state["desired_running"] = True
            _save_state(self.state)
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"🔄 Windows-агент перезапущен Guardian (PID {pid or '?'})"
        elif command == "auto_restart":
            enabled = bool(payload.get("enabled"))
            self.state["auto_restart"] = enabled
            self.state["desired_running"] = True
            _save_state(self.state)
            result = self.status_payload()
            result["text"] = f"🛡 Windows Guardian: {'автовосстановление ВКЛ' if enabled else 'автовосстановление ВЫКЛ'}"
        else:
            result = {"ok": False, "text": f"Неизвестная команда Guardian: {command}"}
        if payload.get("id"):
            result["id"] = payload["id"]
        self._publish(result)

    def monitor(self) -> None:
        while not self.stop_event.wait(5):
            if self.state.get("auto_restart") and self.state.get("desired_running") and not self.agent_process():
                try:
                    pid = self.start_agent()
                    self._publish({"ok": True, "text": f"🛡 Windows Guardian восстановил агент (PID {pid or '?'})"})
                except Exception as exc:
                    log.exception("Windows Guardian restart failed")
                    self._publish({"ok": False, "text": f"⚠️ Windows Guardian не смог запустить агент: {exc}"})

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    Guardian().run()
