"""XIDER Guardian: прозрачный supervisor для macOS-агента.

Guardian работает отдельным видимым LaunchAgent. Он не выполняет shell-команды
из Telegram и не собирает камеру/микрофон/геолокацию. Его задача — держать
MQTT-связь, сообщать состояние и по явно включённой политике перезапускать
рабочий xgent_mcs.py.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
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
from guardian_recovery import RECOVERY_FILES, restore_missing_agent_files
from mac_bundle_update import install_signed_bundle, restore_backup_set
from update_package import (
    UPDATE_STATE_NAME, _read_update_state, download_verified_release_asset,
    rollback_unhealthy_agent,
)
from xgencrypto import decrypt_payload, encrypt_payload
from mac_service_control import (
    LABELS as MAC_SERVICE_LABELS,
    launch_agent_running,
    start_launch_agent,
    stop_launch_agent,
    write_launch_agent as write_mac_launch_agent,
)

try:
    import fcntl
except ImportError:
    fcntl = None

log = logging.getLogger("xider.guardian")


def _read_component_logs() -> dict:
    """Read bounded local logs so Guardian can diagnose a stopped Agent."""
    sources = (
        ("agent", CONFIG_DIR / "xgent.log"),
        # The frozen LaunchAgent captures this process in CONFIG_DIR. Keep the
        # install-root path for legacy source installs started by old scripts.
        ("guardian", CONFIG_DIR / "guardian.log"),
        ("guardian-runtime", SCRIPT_DIR / "guardian.log"),
        ("agent-runtime", SCRIPT_DIR / "agent.log"),
        ("safari", CONFIG_DIR / "safari.log"),
        ("siri", CONFIG_DIR / "siri.log"),
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
    """Resolve the directory beside the installed apps for source and bundles."""
    if getattr(sys, "frozen", False):
        runtime_dir = Path(sys.executable).resolve().parent
    elif globals().get("__compiled__") is not None:
        compiled = globals()["__compiled__"]
        containing_dir = getattr(compiled, "containing_dir", None)
        if not isinstance(containing_dir, (str, os.PathLike)) or not str(containing_dir):
            raise RuntimeError("Compiled Guardian runtime directory is unavailable.")
        runtime_dir = Path(containing_dir).resolve()
    else:
        return Path(__file__).resolve().parent

    app_bundle = next((path for path in (runtime_dir, *runtime_dir.parents)
                       if path.suffix == ".app"), None)
    return app_bundle.parent if app_bundle is not None else runtime_dir


SCRIPT_DIR = _runtime_install_dir()
STATE_FILE = CONFIG_DIR / "guardian.json"
AGENT_STABLE_SECONDS = 60.0
AGENT_HEALTH_STALE_SECONDS = 180.0
MAX_LAUNCHAGENT_PLISTS = 512
MAX_LAUNCHAGENT_PLIST_BYTES = 256 * 1024
MAC_BUNDLE_UPDATE_PROTOCOL = 1
_BUNDLE_UPDATE_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_BUNDLE_UPDATE_RELEASE_TAG = re.compile(r"^v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
_BUNDLE_UPDATE_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def validate_mac_bundle_update_request(payload: dict) -> tuple[str, str, str]:
    operation_id = payload.get("operation_id") or payload.get("id")
    release_tag = payload.get("release_tag")
    manifest_digest = payload.get("manifest_digest")
    if not isinstance(operation_id, str) or not _BUNDLE_UPDATE_OPERATION_ID.fullmatch(operation_id):
        raise ValueError("Некорректный ID операции обновления Mac.")
    if not isinstance(release_tag, str) or not _BUNDLE_UPDATE_RELEASE_TAG.fullmatch(release_tag):
        raise ValueError("Некорректный тег стабильного релиза.")
    if not isinstance(manifest_digest, str) or not _BUNDLE_UPDATE_DIGEST.fullmatch(manifest_digest):
        raise ValueError("Некорректный digest подписанного manifest.")
    if payload.get("component") != "mac_agent":
        raise ValueError("Guardian принимает только подписанный macOS-бандл.")
    return operation_id, release_tag, manifest_digest


def _layer_health_snapshot() -> dict:
    """Return bounded local Safari/Siri heartbeat records for Guardian status."""
    result = {}
    for role in ("safari", "siri"):
        path = CONFIG_DIR / f"{role}-health.json"
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("component") != role:
            continue
        version, stamp, pid, state = (
            payload.get("version"), payload.get("updated_at"),
            payload.get("pid"), payload.get("state"),
        )
        if (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                or type(stamp) not in (int, float) or not 0 < stamp <= time.time() + 5
                or type(pid) is not int or pid <= 0
                or not isinstance(state, str)
                or state not in {"running", "waiting", "error", "stopped"}):
            continue
        result[role] = {
            "installed": True, "version": version, "version_source": role,
            "state": state, "pid": pid, "last_seen": stamp,
        }
        observed = payload.get("observed_components")
        if isinstance(observed, dict):
            clean_observed = {}
            for peer in ("agent", "guardian", "safari", "siri"):
                peer_record = observed.get(peer)
                if peer == role or not isinstance(peer_record, dict):
                    continue
                peer_state = peer_record.get("state")
                freshness = peer_record.get("freshness")
                if (isinstance(peer_state, str)
                        and peer_state in {"running", "waiting", "error", "stopped", "unknown"}
                        and isinstance(freshness, str)
                        and freshness in {"fresh", "stale", "missing", "invalid"}):
                    clean_observed[peer] = {"state": peer_state, "freshness": freshness}
            if clean_observed:
                result[role]["observed_components"] = clean_observed
    return result


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


def count_launchagent_registrations(
    label: str, *, directories: tuple[Path, ...] | None = None,
) -> int:
    """Count local plist declarations for one label without running shell tools."""
    if not isinstance(label, str) or not label or len(label) > 256:
        return 0
    roots = directories or (
        Path.home() / "Library" / "LaunchAgents",
        Path("/Library/LaunchAgents"),
    )
    count = 0
    inspected = 0
    for root in roots:
        try:
            candidates = sorted(root.glob("*.plist"), key=lambda path: path.name)
        except OSError:
            continue
        for path in candidates:
            inspected += 1
            if inspected > MAX_LAUNCHAGENT_PLISTS:
                return count
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if path.stat().st_size > MAX_LAUNCHAGENT_PLIST_BYTES:
                    continue
                data = plistlib.loads(path.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if isinstance(data, dict) and data.get("Label") == label:
                count += 1
    return count


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
        self._bundle_update_lock = threading.Lock()
        self._active_bundle_update_id: str | None = None
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error: str | None = None
        self._observed_agent_pid: int | None = None
        self._observed_agent_since = 0.0
        self._mqtt_connected = False
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

    def _write_health(self, *, state: str = "running") -> None:
        try:
            write_runtime_health("guardian", self._mqtt_connected, state=state)
        except OSError:
            log.exception("Could not write Guardian health heartbeat")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            self._mqtt_connected = False
            self._write_health(state="waiting")
            log.warning("Guardian MQTT connection failed: %s", reason_code)
            return
        self._mqtt_connected = True
        self._write_health()
        client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=1)
        client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=1)
        self._publish(self.status_payload())
        log.info("Guardian connected to %s:%s", MQTT_BROKER, MQTT_PORT)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = False
        self._write_health(state="waiting")
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
            bundled = SCRIPT_DIR / "XIDER.app/Contents/MacOS/XGENT-MCS"
            worker_pattern = (str(bundled) if bundled.is_file() or self.state.get("worker_runtime") == "bundle"
                              else str(SCRIPT_DIR / "xgent_mcs.py"))
            result = subprocess.run(
                ["pgrep", "-f", worker_pattern],
                capture_output=True, text=True, check=False,
            )
            for line in result.stdout.splitlines():
                try:
                    pid = int(line.strip())
                except ValueError:
                    continue
                if pid != os.getpid():
                    if worker_pattern == str(bundled) and self.state.get("worker_runtime") != "bundle":
                        self.state["worker_runtime"] = "bundle"
                        _save_state(self.state, "worker_runtime")
                    return pid
        except OSError:
            pass
        return None

    def _agent_health_is_stale(self, pid: int) -> bool:
        """Only treat a matching, previously observed Agent heartbeat as stale."""
        try:
            path = CONFIG_DIR / "agent-health.json"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                return False
            health = json.loads(path.read_text(encoding="utf-8"))
            stamp = health.get("updated_at") if isinstance(health, dict) else None
            return (
                health.get("schema") == 1
                and health.get("component") == "agent"
                and health.get("pid") == pid
                and health.get("state") == "running"
                and type(stamp) in (int, float)
                and 0 <= time.time() - stamp > AGENT_HEALTH_STALE_SECONDS
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _terminate_unresponsive_agent(self, pid: int) -> None:
        """Stop only the currently identified XIDER worker after stale health."""
        if self.agent_pid() != pid:
            return
        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except psutil.NoSuchProcess:
            return

    def launchd_loaded(self) -> bool:
        """Whether the worker LaunchAgent is registered in this GUI session."""
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.xgent.agent"],
            capture_output=True, check=False,
        )
        return result.returncode == 0

    def guardian_launchd_loaded(self) -> bool:
        """Whether the separate Guardian supervisor is registered."""
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.xider.guardian"],
            capture_output=True, check=False,
        )
        return result.returncode == 0

    def status_payload(self) -> dict:
        pid = self.agent_pid()
        worker_launchd_loaded = self.launchd_loaded()
        guardian_launchd_loaded = self.guardian_launchd_loaded()
        companions = _layer_health_snapshot()
        worker_registration_count = count_launchagent_registrations("com.xgent.agent")
        guardian_registration_count = count_launchagent_registrations("com.xider.guardian")
        duplicate_summary = (
            f"агент: {max(0, worker_registration_count - 1)}, "
            f"Guardian: {max(0, guardian_registration_count - 1)}"
        )

        def layer_state(role: str) -> str:
            record = companions.get(role)
            if not isinstance(record, dict):
                return "неизвестно"
            age = time.time() - record["last_seen"]
            if not 0 <= age < 150:
                return "неизвестно (отчёт устарел)"
            return f"{record['state']} · {max(0, int(age))} сек назад"

        return {
            "type": "guardian",
            "device_id": DEVICE_ID,
            "ok": True,
            "version": VERSION,
            "guardian": "1.0",
            "agent_update_protocol": MAC_BUNDLE_UPDATE_PROTOCOL if (SCRIPT_DIR / ".xider-signed-release").is_file() else 0,
            "agent_running": bool(pid),
            "agent_pid": pid,
            "companions": companions,
            "launchd_loaded": worker_launchd_loaded,
            "guardian_task_registered": guardian_launchd_loaded,
            "launchagent_registration_counts": {
                "agent": worker_registration_count,
                "guardian": guardian_registration_count,
            },
            "auto_restart": bool(self.state.get("auto_restart")),
            "desired_running": bool(self.state.get("desired_running")),
            "text": (
                f"🛡 Guardian 1.0 · {DEVICE_NAME}\n"
                f"Агент: {'онлайн' if pid else 'остановлен'}\n"
                f"LaunchAgent агента: {'зарегистрирован' if worker_launchd_loaded else 'не зарегистрирован'}\n"
                f"LaunchAgent Guardian: {'зарегистрирован' if guardian_launchd_loaded else 'не зарегистрирован'}\n"
                f"Цепочка: Siri → Safari ({layer_state('siri')}) → Guardian ({layer_state('safari')}) → агент ({'онлайн' if pid else 'остановлен'})\n"
                f"Дубликаты LaunchAgent: {duplicate_summary}\n"
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

    def _worker_command(self) -> list[str]:
        bundled = SCRIPT_DIR / "XIDER.app/Contents/MacOS/XGENT-MCS"
        if bundled.is_file():
            self.state["worker_runtime"] = "bundle"
            _save_state(self.state, "worker_runtime")
            return [str(bundled)]
        if self.state.get("worker_runtime") == "bundle":
            raise RuntimeError("Owner removed XIDER.app; local reinstall is required; no source fallback.")
        return [self._worker_python(), str(SCRIPT_DIR / "xgent_mcs.py")]

    def start_agent(self) -> int | None:
        self._require_running_intent()
        pid = self.agent_pid()
        if pid:
            return pid
        worker_command = self._worker_command()
        restored = ([] if self.state.get("worker_runtime") == "bundle"
                    else self._restore_missing_agent_files())
        if restored:
            log.info("Restored signed release files: %s", ", ".join(restored))
        self._require_running_intent()
        env = os.environ.copy()
        env["XIDER_NO_AUTOSTART"] = "1"
        log_path = SCRIPT_DIR / "agent.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                worker_command,
                cwd=str(SCRIPT_DIR), env=env,
                stdout=log_handle, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return proc.pid

    def _reset_recovery_backoff(self) -> None:
        self._restart_backoff_seconds = 5.0
        self._next_restart_at = 0.0
        self._last_recovery_error = None

    def _observe_agent_process(self, pid: int | None, now: float | None = None) -> None:
        """Back off after short-lived worker crashes; reset only after stability.

        A successful ``Popen`` only proves that macOS created a process. It
        does not prove that the worker loaded its config or stayed alive. Keep
        the failed process duration so a broken install cannot trigger a tight
        restart loop or a stream of misleading "recovered" notifications.
        """
        if now is None:
            now = time.monotonic()
        previous_pid = getattr(self, "_observed_agent_pid", None)
        started_at = getattr(self, "_observed_agent_since", 0.0)
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
            return
        if now - started_at >= AGENT_STABLE_SECONDS:
            self._reset_recovery_backoff()

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

    def _stop_agent_for_bundle_update(self, was_launchd_loaded: bool) -> None:
        """Stop the worker without changing the owner's persisted run intent."""
        self._require_running_intent()
        plist = Path.home() / "Library" / "LaunchAgents" / "com.xgent.agent.plist"
        if was_launchd_loaded:
            result = subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
                capture_output=True, text=True, check=False, timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Не удалось временно остановить LaunchAgent агента.")
        pid = self.agent_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and self.agent_pid() == pid:
                time.sleep(0.2)
            if self.agent_pid() == pid:
                os.kill(pid, signal.SIGKILL)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and self.agent_pid() == pid:
                    time.sleep(0.2)
            if self.agent_pid() == pid:
                raise RuntimeError("Старый процесс агента не завершился; бандл не заменён.")

    def _start_agent_after_bundle_update(self, was_launchd_loaded: bool) -> int | None:
        self._require_running_intent()
        plist = Path.home() / "Library" / "LaunchAgents" / "com.xgent.agent.plist"
        if was_launchd_loaded and plist.is_file() and not plist.is_symlink():
            result = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                capture_output=True, text=True, check=False, timeout=15,
            )
            if result.returncode != 0 and not self.launchd_loaded():
                raise RuntimeError(result.stderr.strip() or "LaunchAgent агента не запустился после обновления.")
            return self.agent_pid()
        return self.start_agent()

    def _wait_for_bundle_agent_health(self, expected_version: str, after: float) -> bool:
        deadline = time.monotonic() + 90
        health_path = CONFIG_DIR / "agent-health.json"
        while time.monotonic() < deadline:
            try:
                if health_path.is_symlink() or not health_path.is_file() or health_path.stat().st_size > 4096:
                    time.sleep(1)
                    continue
                payload = json.loads(health_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                time.sleep(1)
                continue
            pid = payload.get("pid") if isinstance(payload, dict) else None
            stamp = payload.get("updated_at") if isinstance(payload, dict) else None
            if (
                payload.get("component") == "agent" and payload.get("device_id") == DEVICE_ID
                and payload.get("version") == expected_version and type(pid) is int
                and type(stamp) in (int, float) and stamp >= after and self.agent_pid() == pid
            ):
                return True
            time.sleep(1)
        return False

    def _publish_bundle_update_progress(
        self, operation_id: str, release_tag: str, manifest_digest: str, state: str,
    ) -> None:
        payload = {
            "type": "agent_update_progress", "device_id": DEVICE_ID,
            "component": "mac_agent", "operation_id": operation_id,
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

    def _update_mac_bundle(self, payload: dict) -> None:
        operation_id = release_tag = manifest_digest = None
        backup_root = None
        was_launchd_loaded = False
        stopped = False
        try:
            operation_id, release_tag, manifest_digest = validate_mac_bundle_update_request(payload)
            if not (SCRIPT_DIR / ".xider-signed-release").is_file():
                raise RuntimeError("Установка не отмечена как подписанный frozen-релиз.")
            was_launchd_loaded = self.launchd_loaded()
            if not self._bundle_update_lock.acquire(blocking=False):
                raise RuntimeError("Обновление Mac-агента уже выполняется.")
            try:
                with self.lock:
                    self._refresh_worker_intent()
                    if not self.state.get("desired_running"):
                        raise RuntimeError("Запуск агента остановлен владельцем; обновление отменено.")
                self._publish_bundle_update_progress(operation_id, release_tag, manifest_digest, "downloading")
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                archive = CONFIG_DIR / f"mac-agent-{operation_id}.zip"
                manifest = CONFIG_DIR / f"mac-agent-{operation_id}.manifest.json"
                if any(path.exists() or path.is_symlink() for path in (archive, manifest)):
                    raise FileExistsError("Для этой операции уже существуют временные файлы.")
                verified_tag = download_verified_release_asset(
                    manifest, archive, "XGENT-MCS-macos-bundle.zip",
                    release_tag=release_tag,
                    expected_manifest_digest=manifest_digest,
                )
                if verified_tag != release_tag:
                    raise ValueError("GitHub вернул другой тег релиза.")
                self._publish_bundle_update_progress(operation_id, release_tag, manifest_digest, "verifying")
                self._stop_agent_for_bundle_update(was_launchd_loaded)
                stopped = True
                self._publish_bundle_update_progress(operation_id, release_tag, manifest_digest, "installing")
                backup_root = install_signed_bundle(archive, SCRIPT_DIR, operation_id)
                self._publish_bundle_update_progress(operation_id, release_tag, manifest_digest, "reconnecting")
                started_at = time.time()
                pid = self._start_agent_after_bundle_update(was_launchd_loaded)
                stopped = False
                self._publish_bundle_update_progress(operation_id, release_tag, manifest_digest, "health_check")
                if not pid or not self._wait_for_bundle_agent_health(release_tag[1:], started_at):
                    raise RuntimeError("Новый Mac-агент не подтвердил свежий heartbeat за 90 секунд.")
                self._publish({
                    "ok": True, "text": f"✅ Mac-агент обновлён до {release_tag}; свежий heartbeat подтверждён.",
                    "component": "mac_agent", "operation_id": operation_id,
                    "release_tag": release_tag, "manifest_digest": manifest_digest,
                    "update_state": "succeeded", "updated_at": time.time(),
                    "agent_update_protocol": MAC_BUNDLE_UPDATE_PROTOCOL,
                })
                # The running Guardian image is still the old signed app. Let
                # launchd restart it from the atomically replaced bundle.
                threading.Timer(
                    2.0,
                    lambda: subprocess.run(
                        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.xider.guardian"],
                        capture_output=True, check=False,
                    ),
                ).start()
            finally:
                self._bundle_update_lock.release()
        except Exception as exc:
            log.exception("macOS frozen agent update failed")
            outcome = "failed"
            if backup_root is not None:
                try:
                    self._publish_bundle_update_progress(
                        operation_id, release_tag, manifest_digest, "rolling_back",
                    )
                    if self.agent_pid():
                        self._stop_agent_for_bundle_update(was_launchd_loaded)
                        stopped = True
                    failed_root = restore_backup_set(backup_root, SCRIPT_DIR, operation_id)
                    if self.state.get("desired_running"):
                        started_at = time.time()
                        pid = self._start_agent_after_bundle_update(was_launchd_loaded)
                        stopped = False
                        if pid and self._wait_for_bundle_agent_health(str(VERSION), started_at):
                            outcome = "rolled_back"
                    else:
                        outcome = "rolled_back"
                except Exception:
                    log.exception("macOS app bundle rollback failed")
            elif stopped and self.state.get("desired_running"):
                try:
                    self._start_agent_after_bundle_update(was_launchd_loaded)
                except Exception:
                    log.exception("Could not restart unchanged macOS Agent after update preparation failed")
            self._publish({
                "ok": outcome == "rolled_back",
                "text": f"❌ Обновление Mac-агента не завершено: {exc}" + (
                    " Предыдущий бандл восстановлен." if outcome == "rolled_back" else ""
                ),
                "component": "mac_agent", "operation_id": operation_id,
                "release_tag": release_tag, "manifest_digest": manifest_digest,
                "update_state": outcome, "updated_at": time.time(),
                "agent_update_protocol": MAC_BUNDLE_UPDATE_PROTOCOL,
            })
        finally:
            if operation_id and getattr(self, "_active_bundle_update_id", None) == operation_id:
                self._active_bundle_update_id = None
            if operation_id:
                for suffix in ("zip", "manifest.json"):
                    try:
                        path = CONFIG_DIR / f"mac-agent-{operation_id}.{suffix}"
                        if path.is_file() and not path.is_symlink():
                            path.unlink()
                    except OSError:
                        log.warning("Could not remove a temporary Mac update file")

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
            elif command == "logs":
                result = _read_component_logs()
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
            elif command == "update_agent":
                operation_id, release_tag, manifest_digest = validate_mac_bundle_update_request(payload)
                if not (SCRIPT_DIR / ".xider-signed-release").is_file():
                    raise RuntimeError("Эта команда обновляет только подписанную frozen-сборку Mac.")
                active_id = getattr(self, "_active_bundle_update_id", None)
                if active_id == operation_id:
                    result = self.status_payload()
                    result.update({
                        "operation_id": operation_id, "release_tag": release_tag,
                        "manifest_digest": manifest_digest,
                        "text": f"⏳ Обновление Mac {release_tag} уже выполняется.",
                    })
                elif active_id is not None:
                    raise RuntimeError("Другое обновление Mac-агента уже выполняется.")
                else:
                    self._active_bundle_update_id = operation_id
                    worker = threading.Thread(
                        target=self._update_mac_bundle, args=(dict(payload),),
                        daemon=True, name=f"xider-mac-update-{operation_id[:8]}",
                    )
                    try:
                        worker.start()
                    except Exception:
                        self._active_bundle_update_id = None
                        raise
                    result = self.status_payload()
                    result.update({
                        "operation_id": operation_id, "release_tag": release_tag,
                        "manifest_digest": manifest_digest,
                        "text": f"📦 Guardian принял подписанный Mac-бандл {release_tag}.",
                    })
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
            self._write_health()
            with self.lock:
                self._refresh_worker_intent()
                update_lock = getattr(self, "_bundle_update_lock", None)
                if (getattr(self, "_active_bundle_update_id", None) is not None
                        or (update_lock is not None and update_lock.locked())):
                    continue
                if self.state.get("auto_restart") and self.state.get("desired_running"):
                    now = time.monotonic()
                    pid = self.agent_pid()
                    if pid and self._agent_health_is_stale(pid):
                        log.warning("Agent PID %s heartbeat is stale; restarting the unresponsive worker", pid)
                        try:
                            self._terminate_unresponsive_agent(pid)
                            pid = self.agent_pid()
                            if pid:
                                raise RuntimeError("зависший процесс агента не завершился")
                        except Exception as exc:
                            self._record_recovery_failure(exc)
                            continue
                    self._observe_agent_process(pid, now)
                    if pid or now < self._next_restart_at:
                        continue
                    try:
                        pid = self.start_agent()
                        if not pid:
                            raise RuntimeError("процесс агента не появился после команды запуска")
                        self._observe_agent_process(pid, time.monotonic())
                        self._publish({
                            "ok": True,
                            "text": f"🛡 Guardian запустил агент (PID {pid}); проверяю, что он остаётся активен.",
                        })
                    except Exception as exc:
                        self._record_recovery_failure(exc)

    def run(self) -> None:
        self._write_health(state="waiting")
        self.client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        threading.Thread(target=self.monitor, daemon=True).start()
        try:
            while not self.stop_event.wait(1):
                pass
        finally:
            self._write_health(state="stopped")
            self.client.loop_stop()
            self.client.disconnect()


def main() -> int:
    if sys.argv[1:] == ["--owner-stop-agent"]:
        set_guardian_desired_running(False)
        print("[OK] XIDER Guardian saved the owner's stop choice.")
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "--service":
        parser = argparse.ArgumentParser(description="XIDER macOS Guard Keeper LaunchAgent control")
        parser.add_argument("--service", choices=("start", "stop", "status"), required=True)
        args = parser.parse_args()
        label = MAC_SERVICE_LABELS["guardian"]
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        try:
            if args.service == "status":
                running = launch_agent_running(label)
                print(f"[{'OK' if running else 'STOPPED'}] XIDER Guard Keeper LaunchAgent")
                print(f"[LOG] {SCRIPT_DIR / 'guardian.log'}")
                return 0 if running else 1
            if args.service == "stop":
                set_guardian_desired_running(False)
                set_guardian_startup_enabled(False)
                stop_launch_agent(label)
                print("[OK] XIDER Guard Keeper: stop")
                return 0
            executable = Path(sys.executable).resolve()
            if not getattr(sys, "frozen", False) and globals().get("__compiled__") is None:
                executable = Path(__file__).resolve()
                arguments = [sys.executable, str(executable)]
                working_directory = executable.parent
            else:
                arguments = [str(executable)]
                working_directory = SCRIPT_DIR
            if not (working_directory / ".env").is_file():
                raise FileNotFoundError("Файл .env не найден; Guard Keeper не регистрирую.")
            set_guardian_desired_running(True)
            set_guardian_startup_enabled(True)
            plist_path = write_mac_launch_agent(
                label=label, program_arguments=arguments,
                working_directory=working_directory,
                stdout_path=CONFIG_DIR / "guardian.log",
            )
            start_launch_agent(label, plist_path)
            print("[OK] XIDER Guard Keeper: start")
            return 0
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"XIDER Guard Keeper {args.service}: {exc}", file=sys.stderr)
            return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    Guardian().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
