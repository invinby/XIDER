"""Small, visible LaunchAgent controls shared by the compiled macOS apps."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path


LABELS = {
    "agent": "com.xgent.agent",
    "guardian": "com.xider.guardian",
    "safari": "com.xider.safari",
    "siri": "com.xider.siri",
}


def app_install_root(executable: str | os.PathLike[str]) -> Path:
    """Return the directory containing the installed XIDER app bundles."""
    path = Path(executable).resolve()
    app = next((parent for parent in (path, *path.parents) if parent.suffix == ".app"), None)
    if app is None:
        raise RuntimeError("Не удалось найти .app для управления LaunchAgent.")
    return app.parent


def _run_launchctl(arguments: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *arguments], capture_output=True, text=True,
        check=False, timeout=timeout,
    )


def launch_agent_running(label: str) -> bool:
    if label not in LABELS.values():
        return False
    try:
        result = _run_launchctl(["print", f"gui/{os.getuid()}/{label}"], timeout=8)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(
        re.search(r"(?m)^\s*(?:pid = [1-9][0-9]*|state = running)\s*$", result.stdout)
    )


def launch_agent_loaded(label: str) -> bool:
    if label not in LABELS.values():
        return False
    try:
        return _run_launchctl(["print", f"gui/{os.getuid()}/{label}"], timeout=8).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def write_launch_agent(
    *, label: str, program_arguments: list[str], working_directory: Path,
    stdout_path: Path, stderr_path: Path | None = None, keep_alive: bool = True,
) -> Path:
    """Atomically write one owner-visible per-user LaunchAgent plist."""
    if label not in LABELS.values() or not program_arguments:
        raise ValueError("Некорректная LaunchAgent-конфигурация XIDER.")
    root = working_directory.resolve()
    executable = Path(program_arguments[0])
    if not executable.is_absolute() or not executable.is_file():
        raise FileNotFoundError("Скомпилированное приложение XIDER не найдено.")
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.parent.is_symlink():
        raise RuntimeError("Отказ: каталог LaunchAgents является symlink.")
    os.chmod(plist_path.parent, 0o700)
    if plist_path.is_symlink():
        raise RuntimeError(f"Отказ: LaunchAgent является symlink: {plist_path.name}")
    payload = {
        "Label": label,
        "ProgramArguments": [str(part) for part in program_arguments],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": bool(keep_alive),
        "ProcessType": "Background",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path or stdout_path),
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{label}.", suffix=".plist.tmp", dir=plist_path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(payload, stream, fmt=plistlib.FMT_XML, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, plist_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return plist_path


def start_launch_agent(label: str, plist_path: Path) -> None:
    if label not in LABELS.values():
        raise ValueError("Неизвестный LaunchAgent XIDER.")
    plist_path = plist_path.resolve()
    if not plist_path.is_file() or plist_path.is_symlink():
        raise FileNotFoundError("LaunchAgent XIDER не создан.")
    service = f"gui/{os.getuid()}/{label}"
    if launch_agent_loaded(label):
        result = _run_launchctl(["bootout", service], timeout=10)
        if result.returncode != 0 and launch_agent_loaded(label):
            detail = (result.stderr or result.stdout or "старый LaunchAgent не выгрузился").strip()
            raise RuntimeError(" ".join(detail.split())[:240])
    result = _run_launchctl(["bootstrap", f"gui/{os.getuid()}", str(plist_path)], timeout=15)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "launchctl bootstrap завершился ошибкой").strip()
        raise RuntimeError(" ".join(detail.split())[:240])
    result = _run_launchctl(["kickstart", service], timeout=15)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "launchctl kickstart завершился ошибкой").strip()
        raise RuntimeError(" ".join(detail.split())[:240])
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if launch_agent_running(label):
            return
        time.sleep(0.25)
    raise RuntimeError(f"LaunchAgent {label} зарегистрирован, но процесс не перешёл в running за 12 секунд.")


def stop_launch_agent(label: str, *, remove_plist: bool = True) -> None:
    if label not in LABELS.values():
        raise ValueError("Неизвестный LaunchAgent XIDER.")
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if plist_path.is_symlink():
        raise RuntimeError(f"Отказ: LaunchAgent является symlink: {plist_path.name}")
    service = f"gui/{os.getuid()}/{label}"
    if launch_agent_loaded(label):
        result = _run_launchctl(["bootout", service], timeout=15)
        if result.returncode != 0 and launch_agent_loaded(label):
            detail = (result.stderr or result.stdout or "LaunchAgent не выгрузился").strip()
            raise RuntimeError(" ".join(detail.split())[:240])
    if remove_plist:
        plist_path.unlink(missing_ok=True)
