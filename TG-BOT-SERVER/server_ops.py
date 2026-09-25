"""Safe local operations for the XIDER server panel.

The Telegram bot runs on the VPS, so these operations intentionally use a
small allow-list of systemd/journalctl commands.  They never accept a shell
string from Telegram and never return environment variables or credentials.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


SERVICE = os.environ.get("XIDER_SERVICE", "xider-bot.service")
APP_DIR = Path(os.environ.get("XIDER_APP_DIR", "/opt/xider"))
UPDATE_SCRIPT = APP_DIR / "deploy" / "update-server.sh"


@dataclass(frozen=True)
class Result:
    ok: bool
    text: str
    code: int = 0


def _run(args: list[str], timeout: int = 20) -> Result:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(False, f"Операция не выполнена: {exc}", 1)
    output = (proc.stdout or proc.stderr or "").strip()
    return Result(proc.returncode == 0, output[-3500:] or "(пустой ответ)", proc.returncode)


def status() -> Result:
    """Return a redacted systemd status summary."""
    return _run(["systemctl", "show", SERVICE, "--no-page",
                 "--property=ActiveState,SubState,Unit,MainPID,ExecMainStatus"])


def logs(lines: int = 40) -> Result:
    lines = max(1, min(int(lines), 120))
    return _run(["journalctl", "-u", SERVICE, "-n", str(lines), "--no-pager", "-o", "short"], 25)


def restart() -> Result:
    return _run(["systemctl", "restart", SERVICE], 30)


def update() -> Result:
    if not UPDATE_SCRIPT.exists():
        return Result(False, f"Скрипт обновления не найден: {UPDATE_SCRIPT}", 2)
    return _run(["bash", str(UPDATE_SCRIPT), "update"], 180)


def rollback() -> Result:
    if not UPDATE_SCRIPT.exists():
        return Result(False, f"Скрипт отката не найден: {UPDATE_SCRIPT}", 2)
    return _run(["bash", str(UPDATE_SCRIPT), "rollback"], 180)
