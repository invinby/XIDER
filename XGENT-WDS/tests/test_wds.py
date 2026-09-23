"""Юнит-тесты новых Windows-функций XGENT-WDS.

Проверяют чистые функции форматирования и маршрутизацию/ack без
реального подключения к MQTT или оборудованию.
"""

import collections
import os

import psutil
import pytest

import xgent_wds as wds


# ---------------------------------------------------------------------------
#  Чистые функции форматирования
# ---------------------------------------------------------------------------

def test_humanize_seconds():
    assert wds._humanize_seconds(0) == "0мин"
    assert wds._humanize_seconds(60) == "1мин"
    assert wds._humanize_seconds(3600) == "1ч 0мин"
    assert wds._humanize_seconds(19920) == "5ч 32мин"
    assert wds._humanize_seconds(None) is None
    assert wds._humanize_seconds(-1) is None
    assert wds._humanize_seconds("bad") is None


def test_format_battery_none():
    assert wds._format_battery(None) == {"available": False}


def test_format_battery_present():
    Battery = collections.namedtuple("sbattery", ["percent", "secsleft", "power_plugged"])
    b = Battery(percent=87.4, secsleft=12345, power_plugged=False)
    out = wds._format_battery(b)
    assert out["available"] is True
    assert out["percent"] == 87.4
    assert out["power_plugged"] is False
    assert out["secsleft"] == 12345
    assert out["time_left"] == "3ч 25мин"
    assert out["state"] == "on_battery"


def test_format_battery_unlimited():
    Battery = collections.namedtuple("sbattery", ["percent", "secsleft", "power_plugged"])
    b = Battery(percent=100.0, secsleft=psutil.POWER_TIME_UNLIMITED, power_plugged=True)
    out = wds._format_battery(b)
    assert out["secsleft"] is None
    assert out["time_left"] is None
    assert out["state"] == "charging"


class _Addr:
    def __init__(self, family, address):
        self.family = family
        self.address = address


class _Stats:
    def __init__(self, isup, speed):
        self.isup = isup
        self.speed = speed


class _Io:
    def __init__(self, sent, recv):
        self.bytes_sent = sent
        self.bytes_recv = recv


def test_format_network():
    import socket

    addrs = {
        "Ethernet": [_Addr(socket.AF_INET, "10.0.0.5"), _Addr(psutil.AF_LINK, "AA-BB-CC")],
        "Loopback": [_Addr(socket.AF_INET6, "::1%lo")],
    }
    stats = {"Ethernet": _Stats(isup=True, speed=1000), "Loopback": _Stats(isup=True, speed=0)}
    io = {"Ethernet": _Io(100, 200), "Loopback": _Io(5, 5)}
    out = wds._format_network(addrs, stats, io)
    eth = next(i for i in out["interfaces"] if i["name"] == "Ethernet")
    assert eth["ipv4"] == "10.0.0.5"
    assert eth["mac"] == "AA-BB-CC"
    assert eth["up"] is True
    assert eth["speed_mbps"] == 1000
    lo = next(i for i in out["interfaces"] if i["name"] == "Loopback")
    assert lo["ipv6"] == "::1"
    assert out["totals"]["bytes_sent"] == 105
    assert out["totals"]["bytes_recv"] == 205


class _Service:
    def __init__(self, name, display_name, status, start_type):
        self._name = name
        self._display = display_name
        self._status = status
        self._start = start_type

    def name(self):
        return self._name

    def display_name(self):
        return self._display

    def status(self):
        return self._status

    def start_type(self):
        return self._start


def test_format_services():
    svcs = [
        _Service("WinDefend", "Windows Defender", "running", "auto"),
        _Service("Spooler", "Print Spooler", "running", "auto"),
        _Service("BrokenSvc", "Broken", "stopped", "auto"),
        _Service("ManualSvc", "Manual", "stopped", "manual"),
    ]
    out = wds._format_services(svcs)
    assert out["total"] == 4
    assert out["running"] == 2
    assert out["stopped"] == 2
    assert out["failed_auto_start"] == [
        {"name": "BrokenSvc", "display_name": "Broken", "status": "stopped"}
    ]


class _Proc:
    def __init__(self, info):
        self.info = info


def test_format_processes():
    procs = [
        _Proc({"pid": 1, "name": "svchost.exe", "memory_percent": 12.3}),
        _Proc({"pid": 2, "name": "chrome.exe", "memory_percent": 30.1}),
        _Proc({"pid": 3, "name": "explorer.exe", "memory_percent": 5.0}),
    ]
    out = wds._format_processes(procs, top_n=2)
    assert out["lines"][0] == "chrome.exe: 30.1%"
    assert out["lines"][1] == "svchost.exe: 12.3%"
    assert len(out["top"]) == 2
    assert out["top"][0]["name"] == "chrome.exe"


# ---------------------------------------------------------------------------
#  Маршрутизация и acknowledgement
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    c = wds.XgentClient()
    mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock.publish.return_value.rc = 0
    monkeypatch.setattr(c, "_client", mock)
    return c


def test_dispatch_unknown_command(client):
    client._dispatch({"type": "no_such_command", "id": "abc"})
    # Должен уйти ack с ошибкой unknown_command.
    topics = [call.args[0] for call in client._client.publish.call_args_list]
    assert any(t.endswith("/ack") for t in topics)
    payloads = [
        __import__("json").loads(call.args[1])["payload"]
        for call in client._client.publish.call_args_list
    ]
    ack = next(p for p in payloads if p.get("type") == "ack")
    assert ack["status"] == "error"
    assert ack["detail"] == "unknown_command"
    assert ack["id"] == "abc"


def test_dispatch_known_command_acks_received_and_ok(client, monkeypatch):
    # open_url не требует оборудования, кроме webbrowser.open — замокаем.
    monkeypatch.setattr(wds.webbrowser, "open", lambda u: None)
    monkeypatch.setattr(wds, "ctypes_windll_user32_message_box", lambda t: None)

    client._dispatch({"type": "open_url", "id": "x1", "url": "https://example.com"})
    import json
    import time

    def _acks():
        return [
            json.loads(call.args[1])["payload"]
            for call in client._client.publish.call_args_list
            if call.args[0].endswith("/ack")
        ]

    # Первый ack — received (сразу).
    acks = _acks()
    assert acks and acks[0]["status"] == "received"
    # Итоговый ok придёт из потока — дождёмся.
    for _ in range(50):
        if any(p["status"] == "ok" for p in _acks()):
            break
        time.sleep(0.05)
    assert any(p["status"] == "ok" for p in _acks())


def test_handler_map_covers_supported_commands(client):
    for name in wds.SUPPORTED_COMMANDS:
        assert name in client._handlers
        assert callable(client._handlers[name])


# ---------------------------------------------------------------------------
#  DEEPSEEK: чистые функции глубоких команд (экран/сеть/реестр/Night Light)
# ---------------------------------------------------------------------------

def test_clamp_brightness():
    assert wds._clamp("50", 0, 100, 30) == 50
    assert wds._clamp("150", 0, 100, 30) == 100
    assert wds._clamp("-5", 0, 100, 30) == 0
    assert wds._clamp("abc", 0, 100, 30) == 30
    assert wds._clamp(None, 0, 100, 30) == 30


def test_orientation_value():
    assert wds._orientation_value(0) == 0
    assert wds._orientation_value(90) == 1
    assert wds._orientation_value(180) == 2
    assert wds._orientation_value(270) == 3
    assert wds._orientation_value(450) == 1  # 450 % 360 = 90
    assert wds._orientation_value(45) is None
    assert wds._orientation_value("abc") is None


def test_parse_wifi_profile_names():
    text = (
        "Profiles on interface Wi-Fi:\n\n"
        "Group policy profiles (read only)\n"
        "---------------------------------\n"
        "    <None>\n\n"
        "User profiles\n"
        "------------\n"
        "    All User Profile     : HomeNet\n"
        "    All User Profile     : Office_5G\n"
    )
    assert wds._parse_wifi_profile_names(text) == ["HomeNet", "Office_5G"]


def test_parse_wifi_profile_names_russian():
    text = "    Профиль всех пользователей : Дом\n    Профиль всех пользователей : Работа\n"
    assert wds._parse_wifi_profile_names(text) == ["Дом", "Работа"]


def test_parse_key_content():
    text = "    Key Content            : mysecret123\n    Cost                    : 1\n"
    assert wds._parse_key_content(text) == "mysecret123"


def test_night_light_helpers():
    # Проверяем переключение blob'а в обе стороны без реального реестра.
    enabled = bytearray(43)
    enabled[18] = 0x15
    assert wds._night_light_enabled(bytes(enabled)) is True

    off = wds._night_light_toggle_bytes(bytes(enabled))
    assert wds._night_light_enabled(off) is False
    assert len(off) in (41, 43)

    back = wds._night_light_toggle_bytes(off)
    assert wds._night_light_enabled(back) is True


# ---------------------------------------------------------------------------
#  Безопасность: тесты на критические сценарии (функция warn удалена —
#  fail-closed теперь делает сама config.py, см. тесты ниже)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
#  Чувствительные команды: routing + безопасный shell (без реального микрофона/сети)
# ---------------------------------------------------------------------------

def _publish_calls(client):
    import json
    return [
        (call.args[0], json.loads(call.args[1])["payload"])
        for call in client._client.publish.call_args_list
    ]


def test_clipboard_routing(client, monkeypatch):
    import pyperclip
    monkeypatch.setattr(pyperclip, "paste", lambda: "hello world")
    client._dispatch({"type": "clipboard", "id": "c1"})
    import time
    for _ in range(50):
        topics = _publish_calls(client)
        if any(t.endswith("/clipboard") for t, _ in topics):
            break
        time.sleep(0.05)
    topics = _publish_calls(client)
    assert any(t.endswith("/clipboard") for t, _ in topics)
    payload = next(p for t, p in topics if t.endswith("/clipboard"))
    assert payload["text"] == "hello world"
    # id живёт в ack, а не в ответе клиента
    ack = next(p for t, p in topics if t.endswith("/ack") and p.get("id") == "c1")
    assert ack["action"] == "clipboard"


def test_shell_routing_uses_capture(client, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        r = type("R", (), {})()
        r.stdout = "ok\n"
        r.stderr = ""
        r.returncode = 0
        return r

    monkeypatch.setattr(wds.subprocess, "run", fake_run)
    client._dispatch({"type": "shell", "command": "whoami", "id": "s1"})
    import time
    for _ in range(50):
        topics = _publish_calls(client)
        if any(t.endswith("/shell") for t, _ in topics):
            break
        time.sleep(0.05)
    assert captured["cmd"] == "whoami"
    assert captured["kwargs"]["shell"] is True
    assert captured["kwargs"]["timeout"] >= 1
    topics = _publish_calls(client)
    payload = next(p for t, p in topics if t.endswith("/shell"))
    assert "ok" in payload["output"]
    assert payload["returncode"] == 0


def test_shell_timeout_reports_error(client, monkeypatch):
    def fake_run(*a, **kw):
        raise wds.subprocess.TimeoutExpired(cmd="x", timeout=20)
    monkeypatch.setattr(wds.subprocess, "run", fake_run)
    client._dispatch({"type": "shell", "command": "sleep 999"})
    import time
    for _ in range(50):
        topics = _publish_calls(client)
        if any(t.endswith("/shell") for t, _ in topics):
            break
        time.sleep(0.05)
    payload = next(p for t, p in topics if t.endswith("/shell"))
    assert "TIMEOUT" in payload["output"]
    assert payload["returncode"] == -1


def test_open_app_uses_startfile_no_shell(client, monkeypatch):
    calls = {"startfile": None}

    def fake_startfile(p):
        calls["startfile"] = p

    monkeypatch.setattr(wds.os, "startfile", fake_startfile, raising=False)
    client._dispatch({"type": "open_app", "app": "calc"})
    assert calls["startfile"] == "calc"


def test_capabilities_does_not_leak_secret(client):
    client._dispatch({"type": "capabilities", "id": "cap1"})
    import time
    for _ in range(50):
        topics = _publish_calls(client)
        if any(t.endswith("/capabilities") for t, _ in topics):
            break
        time.sleep(0.05)
    topics = _publish_calls(client)
    payload = next(p for t, p in topics if t.endswith("/capabilities"))
    assert "SHARED_KEY" not in payload
    assert "shared_key" not in payload
    assert "clipboard" in payload["commands"]
    assert "mic" in payload["commands"]
    assert "shell" in payload["commands"]
    assert "open_app" in payload["commands"]


def test_config_fails_closed_without_key(tmp_path):
    """Без SHARED_KEY в env и в .env config.py должен вызвать SystemExit(2)."""
    import os as _os
    import subprocess as sp
    env = {k: v for k, v in _os.environ.items() if k not in {"SHARED_KEY"}}
    # Не передаём SHARED_KEY, но load_dotenv прочитает существующий .env,
    # если он есть. Поэтому запускаем в копии каталога без .env.
    import shutil
    fake_root = tmp_path / "wds_fake"
    fake_root.mkdir()
    shutil.copy(
        "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\config.py",
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\venv\\Scripts\\python.exe",
            "-c",
            "import runpy; runpy.run_path(r'C:\\TEMP\\cfg.py', run_name='__main__')"
            .replace(r"C:\TEMP\cfg.py", str(fake_root / "config.py")),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "FATAL" in (result.stderr + result.stdout)


def test_config_fails_closed_with_default_key(tmp_path):
    """Дефолтный (публичный) SHARED_KEY должен тоже приводить к SystemExit(2)."""
    import os as _os
    import subprocess as sp
    import shutil
    env = {k: v for k, v in _os.environ.items() if k != "SHARED_KEY"}
    env["SHARED_KEY"] = wds.DEFAULT_SHARED_KEY
    fake_root = tmp_path / "wds_fake2"
    fake_root.mkdir()
    shutil.copy(
        "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\config.py",
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\venv\\Scripts\\python.exe",
            "-c",
            "import runpy; runpy.run_path(r'X', run_name='__main__')"
            .replace("r'X'", "r'" + str(fake_root / "config.py") + "'"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "FATAL" in (result.stderr + result.stdout)


def test_config_ok_with_strong_key(tmp_path):
    """Нормальный SHARED_KEY — config.py загружается без ошибок."""
    import os as _os
    import subprocess as sp
    import shutil
    env = {k: v for k, v in _os.environ.items() if k != "SHARED_KEY"}
    env["SHARED_KEY"] = "a-strong-secret-0123456789abcdef"
    fake_root = tmp_path / "wds_fake3"
    fake_root.mkdir()
    shutil.copy(
        "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\config.py",
        str(fake_root / "config.py"),
    )
    result = sp.run(
        [
            "C:\\Users\\rog\\Desktop\\XIDER\\XGENT-WDS\\venv\\Scripts\\python.exe",
            "-c",
            "import runpy; runpy.run_path(r'X', run_name='__main__')"
            .replace("r'X'", "r'" + str(fake_root / "config.py") + "'"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
