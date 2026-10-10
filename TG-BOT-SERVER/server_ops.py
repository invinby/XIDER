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
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path


SERVICE = "xider-bot.service"
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

STATE_NAMES = ("process", "telegram_polling", "telegram_webhook", "broker", "vps", "cpu", "ram", "disk")
FRESHNESS_SECONDS = 60
CONFIRM_SECONDS = 120
_OBSERVATIONS: dict[str, dict] = {}
_CONFIRMATIONS: dict[str, tuple[int, str, float]] = {}
_LOCK = threading.RLock()


def observe(name: str, state: str, *, checked_at: float | None = None, value=None) -> None:
    """Record only this component's observation; never infer other health."""
    if name not in STATE_NAMES:
        raise ValueError("Unknown server component")
    with _LOCK:
        _OBSERVATIONS[name] = {"state": state, "checked_at": time.time() if checked_at is None else checked_at, "value": value}


def health_states() -> dict:
    now = time.time()
    with _LOCK:
        states = {name: dict(_OBSERVATIONS.get(name, {"state": "unknown", "checked_at": None, "value": None})) for name in STATE_NAMES}
    for item in states.values():
        stamp = item["checked_at"]
        if stamp is not None and (now - stamp > FRESHNESS_SECONDS or stamp > now):
            item["state"] = "stale"
    return states


def request_confirmation(action: str, actor_id: int, *, is_owner: bool) -> str | None:
    if not is_owner or action not in {"restart", "update", "rollback", "approval"}:
        return None
    now = time.monotonic()
    with _LOCK:
        # One pending intent per owner; a new card invalidates older buttons.
        for token, (actor, _, expires) in list(_CONFIRMATIONS.items()):
            if actor == actor_id or expires <= now:
                _CONFIRMATIONS.pop(token)
        token = secrets.token_urlsafe(18)
        _CONFIRMATIONS[token] = (actor_id, action, now + CONFIRM_SECONDS)
        return token


def consume_confirmation(token: str, actor_id: int, *, is_owner: bool, cancel: bool = False) -> str | None:
    with _LOCK:
        pending = _CONFIRMATIONS.get(token)
        if pending is None or not is_owner or pending[0] != actor_id:
            return None
        _CONFIRMATIONS.pop(token)
        if pending[2] <= time.monotonic():
            return None
        # Consumption happens before any await or subprocess, including cancel.
        return pending[1]


def sanitize(text: str) -> str:
    text = str(text)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED]", text, flags=re.S)
    for name, value in os.environ.items():
        if value and any(word in name.upper() for word in ("TOKEN", "PASSWORD", "SECRET", "SHARED_KEY", "PRIVATE_KEY")):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\b\d{5,12}:[A-Za-z0-9_-]{15,}\b", "[REDACTED]", text)
    text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer\s+|basic\s+)?)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:[A-Z_]*(?:token|password|secret|shared_key|private_key)|api_key)\b[\"']?\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1[REDACTED]", text)
    text = re.sub(r"(\w+://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]", "", text)


@dataclass(frozen=True)
class Result:
    ok: bool
    text: str
    code: int = 0
    checked_at: float = field(default_factory=lambda: time.time())
    states: dict = field(default_factory=dict)


def _run(args: list[str], timeout: int = 20) -> Result:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(False, sanitize(f"Операция не выполнена: {exc}")[-3500:], 1)
    output = sanitize(proc.stdout or proc.stderr or "").strip()
    return Result(proc.returncode == 0, output[-3500:] or "(пустой ответ)", proc.returncode)


def status() -> Result:
    """Return a redacted systemd status summary."""
    result = _run(["sudo", "-n", str(CONTROL), "status"])
    props = dict(line.split("=", 1) for line in result.text.splitlines() if "=" in line)
    running = props.get("ActiveState") == "active" and props.get("MainPID", "0").isdigit() and int(props.get("MainPID", "0")) > 0
    observe("process", "running" if running else ("stopped" if result.ok else "unknown"), checked_at=result.checked_at)
    # This is local wrapper reachability, not a remote network ping or stack verdict.
    observe("vps", "reachable" if result.ok else "unavailable", checked_at=result.checked_at)
    states = health_states()
    summary = "\n".join(f"{name}: {item['state']} (checked_at={item['checked_at']})" for name, item in states.items())
    return Result(result.ok and running, f"{result.text}\n\nЛокальные наблюдения:\n{summary}"[-3500:], result.code if result.code else (0 if running else 1), result.checked_at, states)


def logs(lines: int = 40) -> Result:
    # Количество строк ограничивается wrapper-ом на сервере, чтобы Telegram
    # не мог подменить аргументы journalctl.
    result = _run(["sudo", "-n", str(CONTROL), "logs"], 25)
    count = max(1, min(80, int(lines)))
    return Result(result.ok, "\n".join(result.text.splitlines()[-count:])[-3500:], result.code, result.checked_at)


def restart() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "restart"], 30)


def update() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "update"], 180)


def rollback() -> Result:
    return _run(["sudo", "-n", str(CONTROL), "rollback"], 180)


def metrics() -> dict:
    """Снять безопасный снимок нагрузки VPS и сохранить короткую историю."""
    try:
        load_pct = round(float(os.getloadavg()[0]) / max(1, os.cpu_count() or 1) * 100, 1)
    except (OSError, AttributeError, ValueError):
        load_pct = None
    mem_total = 0
    mem_available = None
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
        # Unsupported platforms remain unavailable rather than reporting 0%.
        mem_total = 0
        mem_available = None
    try:
        disk = shutil.disk_usage("/")
        disk_pct = round(disk.used / disk.total * 100, 1) if disk.total else None
    except OSError:
        disk_pct = None
    item = {
        "at": int(time.time()),
        "checked_at": time.time(),
        "load": load_pct,
        "memory": round((1 - mem_available / mem_total) * 100, 1) if mem_total > 0 and mem_available is not None and 0 <= mem_available <= mem_total else None,
        "disk": disk_pct,
    }
    for name, key in (("cpu", "load"), ("ram", "memory"), ("disk", "disk")):
        observe(name, "available" if item[key] is not None else "unavailable", checked_at=item["checked_at"], value=item[key])
    item["states"] = health_states()
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

    Use fixed argv only, including when fastfetch/neofetch aliases are requested.
    """
    commands = [
        ("Хост", ["hostname"]),
        ("Ядро", ["uname", "-srmo"]),
        ("CPU", ["nproc"]),
        ("Аптайм", ["uptime", "-p"]),
        ("RAM", ["free", "-h"]),
        ("Диск", ["df", "-h", "/"]),
    ]
    lines = []
    ok = True
    code = 0
    for label, args in commands:
        result = _run(args, 10)
        if not result.ok:
            ok = False
            code = result.code or 1
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
    return Result(ok, sanitize("\n\n".join(lines))[-3400:], code)


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
            if row.get(key) is None or row.get("states", {}).get({"load": "cpu", "memory": "ram", "disk": "disk"}[key], {}).get("state") in {"unavailable", "stale", "unknown"}:
                if len(points) > 1:
                    draw.line(points, fill=color, width=3)
                points = []
                continue
            value = max(0, min(100, float(row[key])))
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
