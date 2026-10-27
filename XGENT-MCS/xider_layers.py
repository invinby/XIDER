"""Visible, owner-managed macOS recovery layers for XIDER.

Siri supervises Safari, Safari supervises Guard Keeper, and Guard Keeper
supervises the Agent. Safari also checks the Agent heartbeat; if it is stale,
Safari restarts Guard Keeper so recovery still flows through the next layer.
Every component records the state and freshness of the other three in its local
health record. Repair authority stays on the directed Siri -> Safari -> Guard
Keeper -> Agent path, and every start checks the owner's persisted intent.
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import plistlib
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config import CONFIG_DIR, observed_component_health

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only by Windows-hosted tests
    fcntl = None


VERSION = "0.1.0"
TICK_SECONDS = 10
HEALTH_STALE_SECONDS = 35
LAYER_STATE = CONFIG_DIR / "layer-state.json"
_LAYER_STATE_THREAD_LOCK = threading.RLock()
LAYERS = {
    "safari": {
        "label": "com.xider.safari",
        "child_label": "com.xider.guardian",
        "child_start": "start_guardian.sh",
        "child_state": CONFIG_DIR / "guardian.json",
    },
    "siri": {
        "label": "com.xider.siri",
        "child_label": "com.xider.safari",
        "child_start": "start_safari.sh",
        "child_state": None,
    },
}


def set_layer_enabled(role: str, enabled: bool) -> None:
    """Persist an owner-controlled layer switch without losing sibling updates."""
    if role not in LAYERS or type(enabled) is not bool:
        raise ValueError("invalid XIDER layer state")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if LAYER_STATE.is_symlink():
        raise RuntimeError("Refusing symlink layer state.")
    lock_path = LAYER_STATE.with_suffix(".lock")
    if lock_path.is_symlink():
        raise RuntimeError("Refusing symlink layer-state lock.")
    with _LAYER_STATE_THREAD_LOCK, lock_path.open("a+b") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_json(LAYER_STATE)
            state[role] = enabled
            fd, temporary = tempfile.mkstemp(prefix=".layer-state.", suffix=".tmp", dir=CONFIG_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, LAYER_STATE)
            finally:
                Path(temporary).unlink(missing_ok=True)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def owner_stop(role: str) -> None:
    """Persist the owner's stop choice and publish a matching local health record."""
    set_layer_enabled(role, False)
    _write_health(role, "stopped", "disabled by owner")


def write_launch_agent(
    role: str, root: Path, python: str, app_bin: Path,
    release_marker: Path, plist_path: Path,
) -> None:
    """Create a visible LaunchAgent using the compiled release runtime when present."""
    if role not in LAYERS:
        raise ValueError("invalid XIDER layer")
    root = root.resolve()
    app_bin = app_bin.resolve()
    source = root / "xider_layers.py"
    if app_bin.is_file():
        program = [str(app_bin), "--role", role]
    elif release_marker.exists():
        raise FileNotFoundError("Signed XIDER-Layers.app is missing; refusing source fallback.")
    elif source.is_file() and python:
        program = [python, str(source), "--role", role]
    else:
        raise FileNotFoundError("XIDER-Layers runtime is unavailable.")

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.is_symlink():
        raise RuntimeError("Refusing symlink LaunchAgent plist.")
    payload = {
        "Label": LAYERS[role]["label"],
        "ProgramArguments": program,
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{plist_path.name}.", suffix=".tmp", dir=plist_path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(payload, stream, fmt=plistlib.FMT_XML, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, plist_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_health(role: str, state: str, detail: str = "") -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    destination = CONFIG_DIR / f"{role}-health.json"
    if destination.is_symlink():
        raise RuntimeError(f"Refusing symlink health file: {destination.name}")
    payload = {
        "schema": 1,
        "component": role,
        "version": VERSION,
        "pid": os.getpid(),
        "updated_at": time.time(),
        "state": state if state in {"running", "waiting", "error", "stopped"} else "unknown",
        "detail": re.sub(r"[\x00-\x1f\x7f]", " ", str(detail))[:240],
        "observed_components": observed_component_health(role),
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{role}-health.", suffix=".tmp", dir=CONFIG_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _role_enabled(role: str) -> bool:
    state = _read_json(LAYER_STATE)
    return state.get(role) is True


def _child_allowed(role: str) -> bool:
    child_state = LAYERS[role]["child_state"]
    if child_state is None:
        return _role_enabled("safari")
    state = _read_json(child_state)
    # No persisted intent means no recovery action. The installer or owner
    # must explicitly register/enable Guard Keeper first.
    return state.get("desired_running") is True and state.get("startup_enabled", True) is True


def _service_running(label: str) -> bool:
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, check=False, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(re.search(r"(?m)^\s*(?:pid = [1-9][0-9]*|state = running)\s*$", result.stdout))


def _health_is_fresh(role: str) -> bool:
    record = _read_json(CONFIG_DIR / f"{role}-health.json")
    stamp = record.get("updated_at")
    return (
        record.get("schema") == 1 and record.get("component") == role
        and isinstance(record.get("state"), str)
        and record.get("state") in {"running", "waiting"}
        and type(stamp) in (int, float) and 0 <= time.time() - stamp < HEALTH_STALE_SECONDS
    )


def _start_child(role: str) -> str:
    child = LAYERS[role]
    if not _child_allowed(role):
        return "owner_disabled"
    if role == "siri":
        if _service_running(child["child_label"]) and _health_is_fresh("safari"):
            return "healthy"
    else:
        guardian_running = _service_running("com.xider.guardian")
        guardian_healthy = guardian_running and _health_is_fresh("guardian")
        agent_running = _service_running("com.xgent.agent")
        agent_healthy = agent_running and _health_is_fresh("agent")
        if guardian_healthy and agent_healthy:
            return "healthy"
        if guardian_running:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.xider.guardian"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            if result.returncode == 0:
                return "guardian_restarted_for_agent" if guardian_healthy else "stale_guardian_restarted"
    script = Path.cwd() / child["child_start"]
    if not script.is_file():
        raise FileNotFoundError(f"Missing visible XIDER service script: {script.name}")
    result = subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True,
        check=False, timeout=35,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "launchd rejected the start request").strip()
        raise RuntimeError(" ".join(detail.split())[:220])
    return "started"


def run(role: str) -> int:
    if role not in LAYERS:
        raise ValueError("unknown XIDER recovery layer")
    os.umask(0o077)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = CONFIG_DIR / f"{role}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")],
    )
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    logger = logging.getLogger(f"xider.{role}")
    logger.info("XIDER %s %s started (PID %s)", role.title(), VERSION, os.getpid())
    if not _role_enabled(role):
        _write_health(role, "stopped", "disabled by owner")
        logger.info("Layer disabled by owner; exiting")
        return 0
    try:
        last_action_at = 0.0
        while _role_enabled(role):
            try:
                now = time.monotonic()
                if now - last_action_at < 30:
                    _write_health(role, "running", "recovery cooldown")
                    time.sleep(TICK_SECONDS)
                    continue
                outcome = _start_child(role)
                if outcome == "owner_disabled":
                    _write_health(role, "waiting", "child recovery disabled by owner")
                else:
                    _write_health(role, "running", f"child={outcome}")
                    if outcome != "healthy":
                        last_action_at = now
            except Exception as exc:
                message = " ".join(str(exc).split())[:220]
                logger.warning("Child supervision failed: %s", message)
                _write_health(role, "error", message)
                last_action_at = time.monotonic()
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopped by local service control")
    finally:
        _write_health(role, "stopped", "service exited")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--role", choices=tuple(LAYERS))
    mode.add_argument("--set-enabled", nargs=2, metavar=("ROLE", "START|STOP"))
    mode.add_argument("--owner-stop", choices=tuple(LAYERS))
    mode.add_argument(
        "--write-launch-agent", nargs=6,
        metavar=("ROLE", "ROOT", "PYTHON", "APP_BIN", "RELEASE_MARKER", "PLIST"),
    )
    args = parser.parse_args()
    if args.owner_stop is not None:
        try:
            owner_stop(args.owner_stop)
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    if args.write_launch_agent is not None:
        role, root, python, app_bin, release_marker, plist_path = args.write_launch_agent
        try:
            write_launch_agent(
                role, Path(root), python, Path(app_bin), Path(release_marker), Path(plist_path),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    if args.set_enabled is not None:
        role, action = args.set_enabled
        if action not in {"start", "stop"}:
            parser.error("--set-enabled action must be start or stop")
        try:
            set_layer_enabled(role, action == "start")
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    return run(args.role)


if __name__ == "__main__":
    raise SystemExit(main())
