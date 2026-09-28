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
import plistlib
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import paho.mqtt.client as mqtt

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
    PLATFORM,
    XIDER_UPDATE_BRANCH,
    VERSION,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xider.guardian")
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = CONFIG_DIR / "guardian.json"
GUARDIAN_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.xider.guardian.plist"
AGENT_FILES = (
    "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
    "requirements.txt", "setup_mac.py", "start_agent.sh", "stop_agent.sh",
    "start_guardian.sh",
)
AGENT_ENV_KEYS = (
    "SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS",
    "MQTT_USERNAME", "MQTT_PASSWORD", "ENCRYPT_PAYLOAD", "XIDER_UPDATE_BRANCH",
)


def _load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"auto_restart": True, "desired_running": True}


def _save_state(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


class Guardian:
    def __init__(self) -> None:
        self.state = _load_state()
        self.state.setdefault("auto_restart", True)
        self.state.setdefault("desired_running", True)
        _save_state(self.state)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
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
        return {
            "type": "guardian",
            "device_id": DEVICE_ID,
            "ok": True,
            "version": VERSION,
            "guardian": "1.0",
            "agent_running": bool(pid),
            "agent_pid": pid,
            "launchd_loaded": self.launchd_loaded(),
            "auto_restart": bool(self.state.get("auto_restart")),
            "desired_running": bool(self.state.get("desired_running")),
            "text": (
                f"🛡 Guardian 1.0 · {DEVICE_NAME}\n"
                f"Агент: {'онлайн' if pid else 'остановлен'}"
            ),
        }

    def _worker_python(self) -> str:
        candidate = SCRIPT_DIR / "venv" / "bin" / "python3"
        return str(candidate if candidate.exists() else Path(sys.executable))

    def _restore_missing_agent_files(self) -> list[str]:
        """Fetch only missing, allow-listed worker files from the pinned branch."""
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        missing = [name for name in AGENT_FILES if not (SCRIPT_DIR / name).is_file()]
        if not (SCRIPT_DIR / ".env").is_file():
            missing_env = True
        else:
            missing_env = False
        if not missing and not missing_env:
            return []

        branch = XIDER_UPDATE_BRANCH.strip() or "main"
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch.split("/"):
            raise RuntimeError("Небезопасное имя ветки обновления агента")

        restored: list[str] = []
        if missing:
            url = f"https://github.com/invinby/XIDER/archive/refs/heads/{branch}.zip"
            with tempfile.TemporaryDirectory(prefix="xider-agent-repair-") as temp_dir:
                archive_path = Path(temp_dir) / "source.zip"
                urllib.request.urlretrieve(url, archive_path)
                with zipfile.ZipFile(archive_path) as archive:
                    entries = archive.namelist()
                    prefix = next(
                        (name.split("/XGENT-MCS/", 1)[0] + "/XGENT-MCS/"
                         for name in entries if "/XGENT-MCS/xgent_mcs.py" in name),
                        None,
                    )
                    if prefix is None:
                        raise RuntimeError("В архиве не найден каталог XGENT-MCS")
                    for name in missing:
                        member = prefix + name
                        if member not in entries:
                            raise RuntimeError(f"В архиве отсутствует файл агента: {name}")
                        data = archive.read(member)
                        if name.endswith(".py"):
                            compile(data.decode("utf-8"), name, "exec")
                        temp_file = SCRIPT_DIR / f".{name}.recovering"
                        temp_file.write_bytes(data)
                        if name.endswith(".sh"):
                            temp_file.chmod(temp_file.stat().st_mode | 0o111)
                        temp_file.replace(SCRIPT_DIR / name)
                        restored.append(name)

        if missing_env:
            values = {key: os.environ.get(key, "") for key in AGENT_ENV_KEYS}
            required = ("SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_TLS", "ENCRYPT_PAYLOAD")
            absent = [key for key in required if not values[key]]
            if absent:
                raise RuntimeError("Не могу восстановить .env: в процессе Guardian нет " + ", ".join(absent))
            env_path = SCRIPT_DIR / ".env"
            env_tmp = SCRIPT_DIR / ".env.recovering"
            def dotenv_value(value: str) -> str:
                escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
                return f'"{escaped}"'

            env_tmp.write_text(
                "".join(f"{key}={dotenv_value(value)}\n" for key, value in values.items() if value),
                encoding="utf-8",
            )
            env_tmp.chmod(0o600)
            env_tmp.replace(env_path)
            restored.append(".env")

        if restored:
            log.warning("Guardian восстановил отсутствующие файлы агента: %s", ", ".join(restored))
        return restored

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
        _save_state(self.state)
        return proc.pid

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
        _save_state(self.state)
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
        command = str(payload.get("command") or "status").lower()
        if command == "status":
            result = self.status_payload()
        elif command == "start":
            self.state["desired_running"] = True
            _save_state(self.state)
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"✅ Агент запущен Guardian (PID {pid or '?'})"
        elif command == "stop":
            self.stop_agent()
            result = self.status_payload()
            result["text"] = "⏹ Рабочий агент остановлен. Guardian остаётся доступен."
        elif command == "restart":
            self.stop_agent()
            self.state["desired_running"] = True
            _save_state(self.state)
            pid = self.start_agent()
            result = self.status_payload()
            result["text"] = f"🔄 Агент перезапущен Guardian (PID {pid or '?'})"
        elif command == "auto_restart":
            enabled = bool(payload.get("enabled"))
            self.state["auto_restart"] = enabled
            self.state["desired_running"] = True
            _save_state(self.state)
            result = self.status_payload()
            result["text"] = f"🛡 Автовосстановление: {'ВКЛ' if enabled else 'ВЫКЛ'}"
        else:
            result = {"ok": False, "text": f"Неизвестная команда Guardian: {command}"}
        if payload.get("id"):
            result["id"] = payload["id"]
        self._publish(result)

    def monitor(self) -> None:
        while not self.stop_event.wait(5):
            if self.state.get("auto_restart") and self.state.get("desired_running"):
                if not self.agent_pid():
                    try:
                        pid = self.start_agent()
                        self._publish({"ok": True, "text": f"🛡 Guardian восстановил агент (PID {pid or '?'})"})
                    except Exception as exc:
                        log.exception("Guardian restart failed")
                        self._publish({"ok": False, "text": f"⚠️ Guardian не смог запустить агент: {exc}"})

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
