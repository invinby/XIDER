"""Safe local operations for the XIDER server panel.

The Telegram bot runs on the VPS, so these operations intentionally use a
small allow-list of systemd/journalctl commands.  They never accept a shell
string from Telegram and never return environment variables or credentials.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SERVICE = os.environ.get("XIDER_SERVICE", "xider-bot.service")
APP_DIR = Path(os.environ.get("XIDER_APP_DIR", "/opt/xider"))
UPDATE_SCRIPT = APP_DIR / "deploy" / "update-server.sh"
CONTROL = Path(os.environ.get("XIDER_SERVER_OPS", "/usr/local/sbin/xider-server-ops"))
HISTORY_FILE = Path(os.environ.get(
    "XIDER_METRICS_FILE",
    str(Path(__file__).resolve().parent / ".server_metrics.json"),
))

# Команды, которые можно запросить из Telegram. Никаких пайпов, перенаправлений,
# подстановок или произвольных аргументов: бот не превращается в открытый shell.
SAFE_COMMANDS: dict[str, list[str]] = {
    "uptime": ["uptime"],
    "memory": ["free", "-h"],
    "disk": ["df", "-h", "/"],
    "processes": ["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"],
    "service": ["systemctl", "status", SERVICE, "--no-pager", "-n", "30"],
    "logs": ["journalctl", "-u", SERVICE, "-n", "80", "--no-pager"],
}
SAFE_ALIASES = {
    "uptime": "uptime", "нагрузка": "uptime", "cpu": "uptime",
    "память": "memory", "memory": "memory", "ram": "memory",
    "диск": "disk", "disk": "disk", "место": "disk",
    "процессы": "processes", "processes": "processes", "ps": "processes",
    "статус": "service", "service": "service", "логи": "logs", "logs": "logs",
    "характеристики": "specs", "характеристика": "specs", "specs": "specs",
    "fastfetch": "specs", "neofetch": "specs", "инфо": "specs",
}


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
    return _run(["sudo", "-n", str(CONTROL), "status"])


def logs(lines: int = 40) -> Result:
    # Количество строк ограничивается wrapper-ом на сервере, чтобы Telegram
    # не мог подменить аргументы journalctl.
    return _run(["sudo", "-n", str(CONTROL), "logs"], 25)


def restart() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "restart"], 30)


def update() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "update"], 180)


def rollback() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "rollback"], 180)


def metrics() -> dict:
    """Снять безопасный снимок нагрузки VPS и сохранить короткую историю."""
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    cpu_count = max(1, os.cpu_count() or 1)
    mem_total = mem_available = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            number = int(value.strip().split()[0]) * 1024
            if key == "MemTotal":
                mem_total = number
            elif key == "MemAvailable":
                mem_available = number
    except (OSError, ValueError):
        pass
    if not mem_total:
        # На Windows-тестах и локальном запуске оставляем корректный нулевой снимок.
        mem_total = 0
        mem_available = 0
    disk = shutil.disk_usage("/")
    item = {
        "at": int(time.time()),
        "load": round(float(load[0]) / cpu_count * 100, 1),
        "memory": round((1 - mem_available / mem_total) * 100, 1) if mem_total else 0.0,
        "disk": round(disk.used / disk.total * 100, 1) if disk.total else 0.0,
    }
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        history = history if isinstance(history, list) else []
    except (OSError, json.JSONDecodeError):
        history = []
    history.append(item)
    history = history[-60:]
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history), encoding="utf-8")
    except OSError:
        pass
    item["history"] = history
    return item


def specs() -> Result:
    """Return a bounded, credential-free VPS hardware/OS summary.

    Prefer fastfetch/neofetch when installed, otherwise use a small built-in
    summary. No shell string or environment variable is exposed.
    """
    for tool, args in (("fastfetch", ["--pipe"]), ("neofetch", ["--stdout"])):
        executable = shutil.which(tool)
        if executable:
            return _run([executable, *args], 15)
    commands = [
        ("Хост", ["hostname"]),
        ("Ядро", ["uname", "-srmo"]),
        ("CPU", ["nproc"]),
        ("Аптайм", ["uptime", "-p"]),
        ("RAM", ["free", "-h"]),
        ("Диск", ["df", "-h", "/"]),
    ]
    lines = []
    for label, args in commands:
        result = _run(args, 10)
        lines.append(f"{label}:\n{result.text[-900:]}")
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
        pretty_name = next(
            (line.split("=", 1)[1].strip().strip('"')
             for line in os_release.splitlines()
             if line.startswith("PRETTY_NAME=") and "=" in line),
            "неизвестно",
        )
    except OSError:
        pretty_name = "неизвестно"
    lines.insert(1, f"ОС:\n{pretty_name}")
    return Result(True, "\n\n".join(lines)[-3400:])


def render_metrics_chart(snapshot: dict) -> bytes:
    """Нарисовать PNG-график без внешнего сервиса и без передачи секретов."""
    from PIL import Image, ImageDraw

    history = snapshot.get("history") or [snapshot]
    width, height = 1000, 500
    image = Image.new("RGB", (width, height), "#101827")
    draw = ImageDraw.Draw(image)
    draw.text((28, 20), "XIDER VPS — нагрузка", fill="#f8fafc")
    left, top, right, bottom = 60, 70, width - 30, height - 55
    for pct in (0, 25, 50, 75, 100):
        y = bottom - (bottom - top) * pct / 100
        draw.line((left, y, right, y), fill="#273449", width=1)
        draw.text((12, y - 7), f"{pct}%", fill="#94a3b8")
    colors = {"load": "#60a5fa", "memory": "#34d399", "disk": "#fbbf24"}
    labels = {"load": "CPU", "memory": "RAM", "disk": "Диск"}
    for key, color in colors.items():
        points = []
        for index, row in enumerate(history):
            x = left + (right - left) * index / max(1, len(history) - 1)
            value = max(0, min(100, float(row.get(key, 0))))
            y = bottom - (bottom - top) * value / 100
            points.append((x, y))
        if len(points) > 1:
            draw.line(points, fill=color, width=3)
        if points:
            draw.ellipse((points[-1][0] - 4, points[-1][1] - 4, points[-1][0] + 4, points[-1][1] + 4), fill=color)
        draw.text((right - 220 + list(colors).index(key) * 70, 24), labels[key], fill=color)
    buf = __import__("io").BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def run_terminal(command: str) -> Result:
    """Выполнить только одну из заранее разрешённых диагностических команд."""
    normalized = re.sub(r"\s+", " ", str(command or "").strip().lower())
    if any(token in normalized for token in (";", "&&", "||", "|", ">", "<", "`", "$", "\\")):
        return Result(False, "Команда отклонена: shell-операторы запрещены.", 2)
    alias = SAFE_ALIASES.get(normalized)
    if not alias:
        choices = ", ".join(sorted({*SAFE_COMMANDS, "specs", "fastfetch"}))
        return Result(False, f"Разрешены только: {choices}", 2)
    if alias == "specs":
        return specs()
    return _run(SAFE_COMMANDS[alias], 30)
