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
import re
import shutil
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
    MQTT_TRANSPORT,
    MQTT_WS_PATH,
    MQTT_USERNAME,
    VERSION,
    set_guardian_desired_running,
    set_guardian_startup_enabled,
    update_guardian_state,
    write_runtime_health,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

if not (getattr(sys, "frozen", False) or globals().get("__compiled__") is not None):
    _release_module_dir = str(Path(__file__).resolve().parent.parent / "XGENT-MCS")
    if _release_module_dir not in sys.path:
        sys.path.insert(0, _release_module_dir)
from update_package import download_verified_release_asset

log = logging.getLogger("xider.guardian.wds")


def _read_component_logs() -> dict:
    """Read bounded local logs so Guardian can diagnose a stopped Agent."""
    sources = (
        ("agent", CONFIG_DIR / "xgent.log"),
        ("guardian", CONFIG_DIR / "guardian.log"),
        ("agent-runtime", SCRIPT_DIR / "agent.log"),
    )
    secret_patterns = (
        re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization|private[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    )
    rows, unavailable, truncated = [], [], False
    for label, path in sources:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 16 * 1024), os.SEEK_SET)
                raw = stream.read(16 * 1024)
        except FileNotFoundError:
            continue
        except OSError:
            unavailable.append(label)
            continue
        truncated = truncated or size > 16 * 1024
        lines = raw.decode("utf-8", errors="replace").splitlines()
        truncated = truncated or len(lines) > 40
        for line in lines[-40:]:
            for pattern in secret_patterns:
                line = pattern.sub(
                    lambda match: f"{match.group(1)}=[REDACTED]"
                    if match.lastindex and match.lastindex >= 3 else "Bearer [REDACTED]",
                    line,
                )
            rows.append(f"[{label}] {line[-480:]}")
    if not rows:
        state = "unavailable" if unavailable else "missing"
        text = ("Не удалось прочитать локальные журналы XIDER-компонентов."
                if unavailable else "Локальные журналы XIDER-компонентов ещё не созданы.")
        return {"ok": False, "state": state, "lines": 0, "text": text}
    text = "\n".join(rows)
    truncated = truncated or len(rows) > 80 or len(text) > 8000
    return {
        "ok": True, "state": "available", "lines": min(len(rows), 80),
        "truncated": truncated, "partial": bool(unavailable), "text": text[-8000:],
    }


def _runtime_install_dir() -> Path:
    """Resolve the installed bundle directory instead of Nuitka's temp folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    compiled = globals().get("__compiled__")
    if compiled is not None:
        containing_dir = getattr(compiled, "containing_dir", None)
        if not isinstance(containing_dir, (str, os.PathLike)) or not str(containing_dir):
            raise RuntimeError("Nuitka Guardian install directory is unavailable.")
        return Path(containing_dir).resolve()
    return Path(__file__).resolve().parent


SCRIPT_DIR = _runtime_install_dir()
STATE_FILE = CONFIG_DIR / "guardian.json"
LEGACY_STATE_FILE = CONFIG_DIR / "guardian-windows.json"
AGENT_TASK = "XIDER Agent"
GUARDIAN_TASK = "XIDER Guardian"
AGENT_STABLE_SECONDS = 60.0
AGENT_HEALTH_STALE_SECONDS = 180.0
FROZEN_AGENT_UPDATE_PROTOCOL = 1
UPDATE_WAIT_SECONDS = 90
_UPDATE_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_UPDATE_RELEASE_TAG = re.compile(r"^v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
_UPDATE_MANIFEST_DIGEST = re.compile(r"^[0-9a-f]{64}$")
UPDATE_JOURNAL_NAME = "windows-agent-update.json"
UPDATE_RECEIPT_NAME = "windows-agent-update-result.json"


def validate_frozen_update_request(payload: dict) -> tuple[str, str, str]:
    """Accept only a bot-issued operation bound to a stable release manifest."""
    operation_id = payload.get("operation_id") or payload.get("id")
    release_tag = payload.get("release_tag")
    manifest_digest = payload.get("manifest_digest")
    if not isinstance(operation_id, str) or not _UPDATE_OPERATION_ID.fullmatch(operation_id):
        raise ValueError("Некорректный ID операции обновления.")
    if not isinstance(release_tag, str) or not _UPDATE_RELEASE_TAG.fullmatch(release_tag):
        raise ValueError("Некорректный тег стабильного релиза.")
    if not isinstance(manifest_digest, str) or not _UPDATE_MANIFEST_DIGEST.fullmatch(manifest_digest):
        raise ValueError("Некорректный digest подписанного manifest.")
    if payload.get("component") != "windows_agent":
        raise ValueError("Guardian принимает только пакет Windows-агента.")
    return operation_id, release_tag, manifest_digest


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


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing symlink update journal: {path.name}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _update_receipt_path(operation_id: str) -> Path:
    if not isinstance(operation_id, str) or not _UPDATE_OPERATION_ID.fullmatch(operation_id):
        raise ValueError("Некорректный ID операции обновления.")
    return CONFIG_DIR / "windows-agent-update-results" / f"{operation_id}.json"


def _update_journal_path() -> Path:
    return CONFIG_DIR / UPDATE_JOURNAL_NAME


def _read_update_journal() -> dict | None:
    path = _update_journal_path()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict) or value.get("schema") != 1
        or not isinstance(value.get("operation_id"), str)
        or not _UPDATE_OPERATION_ID.fullmatch(value["operation_id"])
        or not isinstance(value.get("release_tag"), str)
        or not _UPDATE_RELEASE_TAG.fullmatch(value["release_tag"])
        or not isinstance(value.get("manifest_digest"), str)
        or not _UPDATE_MANIFEST_DIGEST.fullmatch(value["manifest_digest"])
        or value.get("component") != "windows_agent"
        or value.get("phase") not in {"prepared", "installing", "committed"}
    ):
        return None
    return value


def _read_update_receipt(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict) or value.get("schema") != 1
        or not isinstance(value.get("operation_id"), str)
        or not _UPDATE_OPERATION_ID.fullmatch(value["operation_id"])
        or not isinstance(value.get("release_tag"), str)
        or not _UPDATE_RELEASE_TAG.fullmatch(value["release_tag"])
        or not isinstance(value.get("manifest_digest"), str)
        or not _UPDATE_MANIFEST_DIGEST.fullmatch(value["manifest_digest"])
        or value.get("component") != "windows_agent"
        or value.get("update_state") not in {"succeeded", "failed", "rolled_back"}
        or type(value.get("updated_at")) not in (int, float)
    ):
        return None
    return {key: value[key] for key in (
        "type", "device_id", "guardian", "agent_update_protocol", "ok", "text",
        "component", "operation_id", "release_tag", "manifest_digest", "update_state", "updated_at",
    ) if key in value}


def _persist_update_receipt(payload: dict) -> dict:
    """Persist an immutable terminal update result for replay after reconnect."""
    operation_id, release_tag, manifest_digest = validate_frozen_update_request({
        "operation_id": payload.get("operation_id"),
        "release_tag": payload.get("release_tag"),
        "manifest_digest": payload.get("manifest_digest"),
        "component": payload.get("component"),
    })
    if payload.get("update_state") not in {"succeeded", "failed", "rolled_back"}:
        raise ValueError("Update receipt must be terminal.")
    receipt = {
        "schema": 1,
        "type": "guardian",
        "device_id": DEVICE_ID,
        "guardian": "1.0-windows",
        "agent_update_protocol": FROZEN_AGENT_UPDATE_PROTOCOL,
        "ok": bool(payload.get("ok")),
        "text": str(payload.get("text") or "")[:1000],
        "component": "windows_agent",
        "operation_id": operation_id,
        "release_tag": release_tag,
        "manifest_digest": manifest_digest,
        "update_state": payload["update_state"],
        "updated_at": payload.get("updated_at") if type(payload.get("updated_at")) in (int, float) else time.time(),
    }
    path = _update_receipt_path(operation_id)
    _atomic_json(path, receipt)
    # Keep one latest receipt so a Guardian reconnect can repair a lost MQTT delivery.
    _atomic_json(CONFIG_DIR / UPDATE_RECEIPT_NAME, receipt)
    return _read_update_receipt(path) or receipt


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
        self._update_lock = threading.Lock()
        self._active_update_id: str | None = None
        self._mqtt_connected = False
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error: str | None = None
        self._observed_agent_pid: int | None = None
        self._observed_agent_since = 0.0
        self._last_status_publish = 0.0
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xider-guardian-windows-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
            transport=MQTT_TRANSPORT,
        )
        if MQTT_TRANSPORT == "websockets":
            self.client.ws_set_options(path=MQTT_WS_PATH)
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

    def _publish_update_progress(self, operation_id: str, release_tag: str,
                                 manifest_digest: str, state: str) -> None:
        payload = {
            "type": "agent_update_progress", "device_id": DEVICE_ID,
            "component": "windows_agent", "operation_id": operation_id,
            "release_tag": release_tag, "manifest_digest": manifest_digest,
            "update_state": state,
        }
        info = self.client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/update_progress", self._envelope(payload), qos=1,
        )
        try:
            info.wait_for_publish(timeout=3)
        except Exception:
            pass

    def _recover_interrupted_agent_update(self) -> None:
        """Roll back an interrupted EXE swap before normal Guardian monitoring."""
        journal = _read_update_journal()
        if journal is None:
            return
        journal_path = _update_journal_path()
        operation_id = journal["operation_id"]
        release_tag = journal["release_tag"]
        manifest_digest = journal["manifest_digest"]
        staged = SCRIPT_DIR / f".XGENT-WDS.{operation_id}.new"
        backup = SCRIPT_DIR / f".XGENT-WDS.{operation_id}.backup"
        live = SCRIPT_DIR / "XGENT-WDS.exe"
        manifest = CONFIG_DIR / f"agent-update-{operation_id}.manifest.json"
        if journal["phase"] == "committed":
            receipt = _persist_update_receipt({
                "ok": True, "text": f"✅ Windows-агент обновлён до {release_tag}; подтверждение восстановлено после перезапуска Guardian.",
                "component": "windows_agent", "operation_id": operation_id,
                "release_tag": release_tag, "manifest_digest": manifest_digest,
                "update_state": "succeeded", "updated_at": journal.get("updated_at", time.time()),
            })
            journal_path.unlink(missing_ok=True)
            self._publish(receipt)
            return
        if (live.is_symlink() or backup.is_symlink() or not backup.is_file()
                or staged.is_symlink() or manifest.is_symlink()):
            raise RuntimeError("Незавершённое обновление найдено, но резервная копия отсутствует или небезопасна.")

        desired_running = bool(self.state.get("desired_running"))
        if desired_running:
            self._stop_agent_for_update()
        else:
            self.stop_agent()
        os.replace(backup, live)
        restored = False
        if desired_running:
            started_at = time.time()
            pid = self.start_agent()
            restored = bool(pid and self._wait_for_agent_health(pid, None, started_at))
        text = (
            "↩️ Guardian вернул предыдущий Windows EXE после прерванного обновления; агент подтвердил heartbeat."
            if restored else
            "↩️ Guardian вернул предыдущий Windows EXE после прерванного обновления; запуск остался выключен владельцем."
            if not desired_running else
            "⚠️ Предыдущий Windows EXE восстановлен, но агент не подтвердил heartbeat."
        )
        receipt = _persist_update_receipt({
            "ok": restored or not desired_running, "text": text,
            "component": "windows_agent", "operation_id": operation_id,
            "release_tag": release_tag, "manifest_digest": manifest_digest,
            "update_state": "rolled_back", "updated_at": time.time(),
        })
        staged.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        self._publish(receipt)

    def _update_agent_package(self, payload: dict) -> None:
        operation_id = None
        release_tag = None
        manifest_digest = None
        component = "windows_agent"
        live = SCRIPT_DIR / "XGENT-WDS.exe"
        staged = backup = manifest_path = None
        journal = _update_journal_path()
        owns_temp_paths = False
        installed = False
        agent_stopped = False
        journal_written = False
        started_at = 0.0
        try:
            operation_id, release_tag, manifest_digest = validate_frozen_update_request(payload)
            staged = SCRIPT_DIR / f".XGENT-WDS.{operation_id}.new"
            backup = SCRIPT_DIR / f".XGENT-WDS.{operation_id}.backup"
            manifest_path = CONFIG_DIR / f"agent-update-{operation_id}.manifest.json"
            if not self._update_lock.acquire(blocking=False):
                raise RuntimeError("Другое обновление агента уже выполняется.")
            try:
                with self.lock:
                    self._refresh_worker_intent()
                    if not self.state.get("desired_running"):
                        raise RuntimeError("Запуск агента остановлен владельцем; обновление отменено.")
                if live.is_symlink() or not live.is_file():
                    raise FileNotFoundError("Установленный XGENT-WDS.exe не найден рядом с Guardian.")
                if any(path.is_symlink() or path.exists() for path in (staged, backup, manifest_path)):
                    raise FileExistsError("Для этой операции уже есть файл; повторную замену не начинаю.")
                owns_temp_paths = True
                self._publish_update_progress(operation_id, release_tag, manifest_digest, "downloading")
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                verified_tag = download_verified_release_asset(
                    manifest_path, staged, "XGENT-WDS.exe",
                    release_tag=release_tag,
                    expected_manifest_digest=manifest_digest,
                )
                if verified_tag != release_tag:
                    raise ValueError("GitHub вернул другой тег релиза.")
                self._publish_update_progress(operation_id, release_tag, manifest_digest, "verifying")
                if staged.stat().st_size < 1024 * 1024:
                    raise ValueError("Подписанный EXE меньше минимального размера сборки.")
                started_at = time.time()
                self._publish_update_progress(operation_id, release_tag, manifest_digest, "installing")
                shutil.copy2(live, backup)
                _atomic_json(journal, {
                    "schema": 1, "component": component,
                    "operation_id": operation_id, "release_tag": release_tag,
                    "manifest_digest": manifest_digest, "phase": "prepared",
                    "updated_at": time.time(),
                })
                journal_written = True
                self._stop_agent_for_update()
                agent_stopped = True
                _atomic_json(journal, {
                    "schema": 1, "component": component,
                    "operation_id": operation_id, "release_tag": release_tag,
                    "manifest_digest": manifest_digest, "phase": "installing",
                    "updated_at": time.time(),
                })
                os.replace(staged, live)
                installed = True
                self._publish_update_progress(operation_id, release_tag, manifest_digest, "reconnecting")
                pid = self.start_agent()
                if not pid:
                    raise RuntimeError("Guardian не получил PID нового агента.")
                self._publish_update_progress(operation_id, release_tag, manifest_digest, "health_check")
                if not self._wait_for_agent_health(pid, release_tag[1:], started_at):
                    raise RuntimeError("Новая версия не подтвердила свежий heartbeat за 90 секунд.")
                committed_at = time.time()
                _atomic_json(journal, {
                    "schema": 1, "component": component,
                    "operation_id": operation_id, "release_tag": release_tag,
                    "manifest_digest": manifest_digest, "phase": "committed",
                    "updated_at": committed_at,
                })
                receipt = _persist_update_receipt({
                    "ok": True, "text": f"✅ Windows-агент обновлён до {release_tag}; свежий heartbeat подтверждён.",
                    "component": component, "operation_id": operation_id,
                    "release_tag": release_tag, "manifest_digest": manifest_digest,
                    "update_state": "succeeded", "updated_at": committed_at,
                })
                self._publish(receipt)
                journal.unlink(missing_ok=True)
                journal_written = False
                agent_stopped = False
            finally:
                self._update_lock.release()
        except Exception as exc:
            log.exception("Windows frozen agent update failed")
            outcome = "failed"
            rollback_complete = False
            if installed:
                try:
                    self._publish_update_progress(operation_id, release_tag, manifest_digest, "rolling_back")
                    self._stop_agent_for_update()
                    if not backup.is_file() or backup.is_symlink():
                        raise RuntimeError("Резервная копия EXE отсутствует или небезопасна.")
                    os.replace(backup, live)
                    restored_pid = self.start_agent()
                    if restored_pid and self._wait_for_agent_health(
                        restored_pid, None, time.time() - 1,
                    ):
                        outcome = "rolled_back"
                        rollback_complete = True
                except Exception:
                    log.exception("Windows frozen agent rollback failed")
            elif agent_stopped:
                try:
                    self.start_agent()
                    rollback_complete = True
                except Exception:
                    log.exception("Could not restart the unchanged Windows Agent after update preparation failed")
            receipt = _persist_update_receipt({
                "ok": False, "text": f"❌ Обновление Windows-агента не завершено: {exc}",
                "component": component, "operation_id": operation_id,
                "release_tag": release_tag, "manifest_digest": manifest_digest,
                "update_state": outcome, "updated_at": time.time(),
            })
            self._publish(receipt)
            if journal_written and rollback_complete:
                journal.unlink(missing_ok=True)
        finally:
            if owns_temp_paths and manifest_path is not None:
                manifest_path.unlink(missing_ok=True)
            if owns_temp_paths and staged is not None:
                staged.unlink(missing_ok=True)
            if operation_id and getattr(self, "_active_update_id", None) == operation_id:
                with self.lock:
                    self._active_update_id = None

    def _stop_agent_for_update(self) -> None:
        """Stop the worker without changing the owner's desired-running state."""
        with self.lock:
            self._refresh_worker_intent()
            if not self.state.get("desired_running"):
                raise RuntimeError("Запуск агента остановлен владельцем.")
            proc = self.agent_process()
            if _task_exists(AGENT_TASK) is True:
                result = subprocess.run(
                    ["schtasks.exe", "/End", "/TN", AGENT_TASK],
                    capture_output=True, text=True, check=False, timeout=15,
                )
                if result.returncode not in (0, 1):
                    raise RuntimeError("Task Scheduler не остановил задачу агента.")
            if proc is None:
                return
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if self.agent_process() is not None:
                raise RuntimeError("Процесс агента остался запущен; EXE не заменён.")

    def _wait_for_agent_health(self, expected_pid: int, expected_version: str | None,
                               after: float) -> bool:
        deadline = time.monotonic() + UPDATE_WAIT_SECONDS
        health_path = CONFIG_DIR / "agent-health.json"
        while time.monotonic() < deadline:
            try:
                if health_path.is_symlink() or health_path.stat().st_size > 4096:
                    time.sleep(1)
                    continue
                health = json.loads(health_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                time.sleep(1)
                continue
            stamp = health.get("updated_at") if isinstance(health, dict) else None
            version = health.get("version") if isinstance(health, dict) else None
            current = self.agent_process()
            if (
                isinstance(health, dict)
                and health.get("component") == "agent"
                and health.get("device_id") == DEVICE_ID
                and health.get("pid") == expected_pid
                and type(stamp) in (int, float) and stamp >= after
                and (expected_version is None or version == expected_version)
                and current is not None and current.pid == expected_pid
            ):
                return True
            time.sleep(1)
        return False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            self._mqtt_connected = False
            log.warning("Guardian MQTT connection failed: %s", reason_code)
            try:
                write_runtime_health("guardian", False)
            except Exception:
                log.exception("Could not update local Guardian MQTT health")
            return
        self._mqtt_connected = True
        client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=1)
        client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=1)
        self._publish(self.status_payload())
        latest_receipt = _read_update_receipt(CONFIG_DIR / UPDATE_RECEIPT_NAME)
        if latest_receipt is not None:
            self._publish(latest_receipt)
        try:
            write_runtime_health("guardian", True)
        except Exception:
            log.exception("Could not write local Guardian MQTT health")
        log.info("Windows Guardian connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = False
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
            "agent_update_protocol": FROZEN_AGENT_UPDATE_PROTOCOL,
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
        elif command == "logs":
            result = _read_component_logs()
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
        elif command == "update_agent":
            try:
                operation_id, release_tag, manifest_digest = validate_frozen_update_request(payload)
                receipt = _read_update_receipt(_update_receipt_path(operation_id))
                if receipt is not None:
                    if (receipt.get("release_tag") != release_tag
                            or receipt.get("manifest_digest") != manifest_digest):
                        raise ValueError("ID операции уже привязан к другому релизу.")
                    result = receipt
                elif self._active_update_id == operation_id:
                    result = self.status_payload()
                    result.update({
                        "operation_id": operation_id, "release_tag": release_tag,
                        "manifest_digest": manifest_digest,
                        "text": f"⏳ Обновление {release_tag} уже выполняется.",
                    })
                elif self._active_update_id is not None or self._update_lock.locked():
                    raise RuntimeError("Другое обновление агента уже выполняется.")
                else:
                    if not self.state.get("desired_running"):
                        raise RuntimeError("Запуск агента остановлен владельцем; обновление отменено.")
                    self._active_update_id = operation_id
                    worker = threading.Thread(
                        target=self._update_agent_package, args=(dict(payload),),
                        daemon=True, name=f"xider-agent-update-{operation_id[:8]}",
                    )
                    try:
                        worker.start()
                    except Exception:
                        self._active_update_id = None
                        raise
                    result = self.status_payload()
                    result.update({
                        "operation_id": operation_id,
                        "release_tag": release_tag,
                        "manifest_digest": manifest_digest,
                        "text": f"📦 Guardian принял обновление {release_tag}; проверяю подписанный EXE.",
                    })
            except Exception as exc:
                result = {
                    "ok": False, "agent_update_protocol": FROZEN_AGENT_UPDATE_PROTOCOL,
                    "text": f"❌ Guardian не принял обновление: {exc}",
                }
        else:
            result = {"ok": False, "text": f"Неизвестная команда Guardian: {command}"}
        if payload.get("id"):
            result["id"] = payload["id"]
        self._publish(result)

    def monitor(self) -> None:
        while not self.stop_event.wait(5):
            with self.lock:
                self._refresh_worker_intent()
                now = time.monotonic()
                if getattr(self, "_mqtt_connected", False) and now - getattr(self, "_last_status_publish", 0.0) >= 30:
                    self._publish(self.status_payload())
                    self._last_status_publish = now
                if not (self.state.get("auto_restart") and self.state.get("desired_running")):
                    continue
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
        try:
            self._recover_interrupted_agent_update()
        except Exception:
            log.exception("Could not recover an interrupted Windows Agent update")
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
