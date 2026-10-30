import server_ops
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server_ops, "HISTORY_FILE", tmp_path / "metrics.json")
    monkeypatch.setattr(server_ops, "_OBSERVATIONS", {}, raising=False)
    monkeypatch.setattr(server_ops, "_CONFIRMATIONS", {}, raising=False)


def fake_process(monkeypatch, stdout="", code=0):
    seen = []
    def run(args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
    monkeypatch.setattr(server_ops.subprocess, "run", run)
    return seen


def _wrapper_test_command():
    if os.name != "nt":
        return ["bash", "-s", "--"]
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return None
    try:
        probe = subprocess.run(
            [wsl, "-d", "Ubuntu", "--exec", "bash", "-lc", "true"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0:
        return None
    return [wsl, "-d", "Ubuntu", "--", "bash", "-s", "--"]


def test_windows_wrapper_test_skips_without_ubuntu_wsl(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(shutil, "which", lambda _name: "wsl.exe")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0xFFFFFFFF))

    assert _wrapper_test_command() is None


def test_status_keeps_stack_states_independent(monkeypatch):
    fake_process(monkeypatch, "ActiveState=active\nSubState=running\nMainPID=42")
    result = server_ops.status()
    assert result.checked_at > 0
    assert result.states["process"]["state"] == "running"
    assert result.states["vps"]["state"] == "reachable"
    for name in ("telegram_polling", "telegram_webhook", "broker", "cpu", "ram", "disk"):
        assert result.states[name]["state"] == "unknown"


def test_unavailable_host_is_not_success(monkeypatch):
    fake_process(monkeypatch, "host unavailable", 1)
    result = server_ops.status()
    assert not result.ok
    assert result.states["vps"]["state"] == "unavailable"
    assert result.states["process"]["state"] == "unknown"


def test_inactive_service_is_failure_even_when_host_responds(monkeypatch):
    fake_process(monkeypatch, "ActiveState=inactive\nMainPID=0")
    result = server_ops.status()
    assert not result.ok and result.code != 0
    assert result.states["process"]["state"] == "stopped"
    assert result.states["vps"]["state"] == "reachable"


@pytest.mark.parametrize("args", [("status", "extra"), ("rollback", "/tmp/arbitrary.zip"), ("restart", "other.service"), ("bogus",)])
def test_privileged_wrapper_rejects_extra_arguments_before_any_action(args):
    command = _wrapper_test_command()
    if command is None:
        pytest.skip("Ubuntu WSL is not installed; the same wrapper contract runs in the Ubuntu CI job")
    script = (Path(__file__).resolve().parents[2] / "deploy/xider-server-ops.sh").read_text(encoding="utf-8")
    # Replace executable boundaries so even a broken guard cannot run a service
    # or updater. The real case/argument validation still executes in bash.
    for executable in ("/bin/systemctl", "/usr/bin/journalctl", "/bin/bash"):
        script = script.replace(executable, "/usr/bin/printf")
    result = subprocess.run(command + list(args), input=script.encode(), capture_output=True, timeout=15)
    assert result.returncode == 2
    assert b"Usage:" in result.stderr


def test_stale_observations_are_not_refreshed_by_status(monkeypatch):
    monkeypatch.setattr(server_ops.time, "time", lambda: 200)
    server_ops.observe("broker", "connected", checked_at=10)
    server_ops.observe("cpu", "available", checked_at=10, value=50)
    fake_process(monkeypatch, "ActiveState=active\nMainPID=42")
    result = server_ops.status()
    assert result.states["broker"]["state"] == "stale"
    assert result.states["cpu"]["checked_at"] == 10
    assert result.states["cpu"]["state"] == "stale"


def test_metrics_failure_does_not_report_zero_healthy(monkeypatch):
    monkeypatch.setattr(server_ops.os, "getloadavg", lambda: (_ for _ in ()).throw(OSError("missing")), raising=False)
    monkeypatch.setattr(server_ops.shutil, "disk_usage", lambda _: (_ for _ in ()).throw(OSError("missing")))
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/meminfo":
            raise OSError("missing")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    snapshot = server_ops.metrics()
    assert snapshot["checked_at"] > 0
    assert snapshot["load"] is None and snapshot["memory"] is None and snapshot["disk"] is None
    assert all(snapshot["states"][key]["state"] == "unavailable" for key in ("cpu", "ram", "disk"))


def test_partial_metric_failure_preserves_other_fresh_components(monkeypatch):
    monkeypatch.setattr(server_ops.os, "getloadavg", lambda: (1, 2, 3), raising=False)
    monkeypatch.setattr(server_ops.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(server_ops.shutil, "disk_usage", lambda _: SimpleNamespace(total=100, used=25))
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/meminfo":
            return "MemTotal: 1000 kB\n"  # MemAvailable is missing, not zero.
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    snapshot = server_ops.metrics()
    assert snapshot["load"] == 50 and snapshot["disk"] == 25
    assert snapshot["memory"] is None
    assert snapshot["states"]["cpu"]["state"] == "available"
    assert snapshot["states"]["disk"]["state"] == "available"
    assert snapshot["states"]["ram"]["state"] == "unavailable"


def test_new_confirmation_invalidates_old_intent():
    old = server_ops.request_confirmation("restart", 100, is_owner=True)
    new = server_ops.request_confirmation("update", 100, is_owner=True)
    assert server_ops.consume_confirmation(old, 100, is_owner=True) is None
    assert server_ops.consume_confirmation(new, 100, is_owner=True) == "update"


def test_subprocess_timeout_returns_bounded_redacted_failure(monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output="BOT_TOKEN=secret-value")
    monkeypatch.setattr(server_ops.subprocess, "run", timeout)
    result = server_ops.restart()
    assert not result.ok and result.code != 0
    assert "secret-value" not in result.text and len(result.text) <= 3500


def test_logs_redact_before_truncation_and_honor_line_bound(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:secret_token_fixture")
    output = "\n".join(["safe"] * 90 + ["BOT_TOKEN=123456:secret_token_fixture", "Authorization: Bearer private-value", "mqtt://user:password@broker", "SHARED_KEY=private-key"])
    fake_process(monkeypatch, output)
    result = server_ops.logs(3)
    assert len(result.text.splitlines()) <= 3
    for secret in ("secret_token_fixture", "private-value", "password", "private-key"):
        assert secret not in result.text
    assert "REDACTED" in result.text
    assert len(result.text) <= 3500


@pytest.mark.parametrize("command", ["rm -rf /", "uptime; whoami", "service --help", "$(id)", "logs | cat"])
def test_terminal_rejects_unlisted_argv(monkeypatch, command):
    seen = fake_process(monkeypatch)
    assert not server_ops.run_terminal(command).ok
    assert seen == []


def test_terminal_uses_fixed_argv_and_propagates_failure(monkeypatch):
    seen = fake_process(monkeypatch, "no memory", 9)
    result = server_ops.run_terminal("ram")
    assert not result.ok and result.code == 9
    assert seen[0][0] == ["free", "-h"]
    assert not seen[0][1].get("shell", False)


def test_specs_does_not_execute_discovered_binary_and_reports_failure(monkeypatch):
    monkeypatch.setattr(server_ops.shutil, "which", lambda _: "/tmp/untrusted-fastfetch")
    seen = fake_process(monkeypatch, "unavailable", 4)
    result = server_ops.run_terminal("specs")
    assert not result.ok
    assert [args for args, _ in seen] == [["hostname"], ["uname", "-srmo"], ["nproc"], ["uptime", "-p"], ["free", "-h"], ["df", "-h", "/"]]


def test_redacts_json_secrets_and_multiline_private_keys(monkeypatch):
    output = '{"api_key": "unknown-private-value", "MQTT_PASSWORD": "unknown-password"}\n-----BEGIN PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----'
    fake_process(monkeypatch, output)
    result = server_ops.logs()
    assert all(value not in result.text for value in ("unknown-private-value", "unknown-password", "secret-body"))


def test_stale_metrics_card_does_not_display_healthy_percent(monkeypatch):
    import bot
    monkeypatch.setattr(bot.time, "time", lambda: 200)
    text = bot.server_metrics_text({"load": 12.3, "memory": 45.6, "disk": 78.9, "checked_at": 10})
    assert "12.3%" not in text and "45.6%" not in text and "78.9%" not in text
    assert "stale" in text


def test_real_mqtt_callbacks_record_connect_disconnect_without_stack_inference():
    import bot
    client = SimpleNamespace(subscribe=lambda *args, **kwargs: None)
    bot.transport._client.on_connect(client, None, None, 0)
    states = server_ops.health_states()
    assert states["broker"]["state"] == "connected"
    assert states["broker"]["checked_at"] is not None
    assert states["telegram_polling"]["state"] == "unknown"
    bot.transport._client.on_disconnect(client, None, None, 0)
    assert server_ops.health_states()["broker"]["state"] == "disconnected"


def test_metric_chart_accepts_unavailable_components():
    result = server_ops.render_metrics_chart({"load": None, "memory": None, "disk": None})
    assert result.startswith(b"\x89PNG")


def test_confirmation_is_owner_bound_expiring_and_single_use(monkeypatch):
    clock = [100]
    monkeypatch.setattr(server_ops.time, "monotonic", lambda: clock[0])
    token = server_ops.request_confirmation("restart", 100, is_owner=True)
    assert token
    assert server_ops.consume_confirmation(token, 200, is_owner=True) is None
    assert server_ops.consume_confirmation(token, 100, is_owner=False) is None
    assert server_ops.consume_confirmation(token, 100, is_owner=True) == "restart"
    assert server_ops.consume_confirmation(token, 100, is_owner=True) is None
    expired = server_ops.request_confirmation("update", 100, is_owner=True)
    clock[0] += 121
    assert server_ops.consume_confirmation(expired, 100, is_owner=True) is None
    assert server_ops.request_confirmation("restart", 200, is_owner=False) is None


def test_cancel_invalidates_confirmation():
    token = server_ops.request_confirmation("rollback", 100, is_owner=True)
    assert server_ops.consume_confirmation(token, 100, is_owner=True, cancel=True) == "rollback"
    assert server_ops.consume_confirmation(token, 100, is_owner=True) is None


@pytest.fixture
def bot_callbacks(monkeypatch):
    import bot
    calls, cards, answers = [], [], []
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER if user == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *a, **kw: None)
    async def render(cq, text, **kwargs):
        cards.append((text, kwargs.get("reply_markup")))
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(server_ops, "restart", lambda: calls.append("restart") or server_ops.Result(True, "ok"))
    async def answer(*a, **kw):
        answers.append((a, kw))
    def callback(data, user=100):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=user), answer=answer)
    return bot, callback, calls, cards


def test_mutation_callback_checks_owner_even_without_router(bot_callbacks):
    bot, callback, calls, cards = bot_callbacks
    asyncio.run(bot.on_server_confirm(callback("server_confirm:restart", 200)))
    assert calls == []
    asyncio.run(bot.on_server_dangerous_request(callback("server:restart", 200)))
    assert cards == []


@pytest.mark.parametrize("role_name", ["GUEST", "USER", "COOWNER"])
def test_readonly_server_handler_hides_seeded_owner_details(bot_callbacks, monkeypatch, role_name):
    bot, callback, calls, cards = bot_callbacks
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER if user == 100 else getattr(bot.Role, role_name))
    monkeypatch.setattr(bot, "XIDER_BUILD_CODE", "PRIVATE_SERVER_BUILD_FIXTURE")
    monkeypatch.setattr(server_ops, "status", lambda: pytest.fail("read-only actor probed privileged server status"))
    for component in server_ops.STATE_NAMES:
        server_ops.observe(component, f"PRIVATE_STATE_{component}", checked_at=1234567890.25, value=73.125)
    asyncio.run(bot.on_menu_server(callback("menu:server", 200)))
    text, markup = cards[-1]
    assert "только для чтения" in text.lower()
    assert "PRIVATE_SERVER_BUILD_FIXTURE" not in text
    assert "PRIVATE_STATE_" not in text and "1234567890.25" not in text and "checked_at" not in text
    assert all(component not in text for component in server_ops.STATE_NAMES)
    assert bot._lex("server_approval_line", state="включено") not in text
    assert bot._lex("server_approval_line", state="выключено") not in text
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == ["menu:main"]
    assert calls == []


def test_server_overview_without_explicit_identity_fails_closed(bot_callbacks):
    bot, _, _, _ = bot_callbacks
    text = bot.server_overview_text(approval=True, connected=True)
    assert "только для чтения" in text.lower()
    assert "checked_at" not in text and "telegram_polling" not in text


def test_owner_server_handler_retains_explicit_private_overview(bot_callbacks, monkeypatch):
    bot, callback, _, cards = bot_callbacks
    monkeypatch.setattr(bot, "XIDER_BUILD_CODE", "OWNER_BUILD_FIXTURE")
    monkeypatch.setattr(server_ops, "status", lambda: server_ops.Result(True, "fixture"))
    for component in server_ops.STATE_NAMES:
        server_ops.observe(component, "private", checked_at=1234567890.25)
    asyncio.run(bot.on_menu_server(callback("menu:server", 100)))
    text, markup = cards[-1]
    assert "OWNER_BUILD_FIXTURE" in text and "1234567890.25" in text
    assert all(component in text for component in server_ops.STATE_NAMES)
    assert bot._lex("server_approval_line", state="включено") in text
    assert "server:restart" in [button.callback_data for row in markup.inline_keyboard for button in row]


def test_explicit_confirmation_callback_is_replay_resistant(bot_callbacks):
    bot, callback, calls, cards = bot_callbacks
    asyncio.run(bot.on_server_dangerous_request(callback("server:restart")))
    buttons = [button.callback_data for row in cards[-1][1].inline_keyboard for button in row]
    confirm = next(data for data in buttons if data.startswith("server_confirm:"))
    cancel = next(data for data in buttons if data.startswith("server_cancel:"))
    assert confirm != "server_confirm:restart"
    asyncio.run(bot.on_server_confirm(callback(confirm)))
    asyncio.run(bot.on_server_confirm(callback(confirm)))
    assert calls == ["restart"]
    asyncio.run(bot.on_server_dangerous_request(callback("server:restart")))
    cancel = next(button.callback_data for row in cards[-1][1].inline_keyboard for button in row if button.callback_data.startswith("server_cancel:"))
    asyncio.run(bot.on_server_cancel(callback(cancel)))
    asyncio.run(bot.on_server_confirm(callback(cancel.replace("server_cancel:", "server_confirm:"))))
    assert calls == ["restart"]


def test_device_approval_policy_toggle_requires_confirmation_and_persists(bot_callbacks, monkeypatch, tmp_path):
    bot, callback, _, cards = bot_callbacks
    monkeypatch.setattr(bot.bot_settings, "FILE", tmp_path / "bot_settings.json")

    assert bot.bot_settings.get("require_device_approval", True) is True
    asyncio.run(bot.on_server_approval(callback("server:approval")))
    prompt, markup = cards[-1]
    assert "политики допуска устройств" in prompt
    confirm = next(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("server_confirm:")
    )
    asyncio.run(bot.on_server_confirm(callback(confirm)))

    assert bot.bot_settings.get("require_device_approval") is False
    assert '"require_device_approval": false' in (tmp_path / "bot_settings.json").read_text(encoding="utf-8")
    assert bot._lex("server_approval_line", state="выключено") in cards[-1][0]


def test_confirmation_callback_checks_role_revocation_and_expiry(bot_callbacks, monkeypatch):
    bot, callback, calls, cards = bot_callbacks
    clock = [100]
    monkeypatch.setattr(server_ops.time, "monotonic", lambda: clock[0])
    asyncio.run(bot.on_server_dangerous_request(callback("server:restart")))
    confirm = next(button.callback_data for row in cards[-1][1].inline_keyboard for button in row if button.callback_data.startswith("server_confirm:"))
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.USER)
    asyncio.run(bot.on_server_confirm(callback(confirm)))
    assert calls == []
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER)
    clock[0] += 121
    asyncio.run(bot.on_server_confirm(callback(confirm)))
    assert calls == []


def test_role_revoked_during_pending_card_cannot_execute(bot_callbacks, monkeypatch):
    bot, callback, calls, cards = bot_callbacks
    token = server_ops.request_confirmation("restart", 100, is_owner=True)
    async def revoke(*args, **kwargs):
        monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.USER)
    monkeypatch.setattr(bot, "_replace_callback_message", revoke)
    asyncio.run(bot.on_server_confirm(callback(f"server_confirm:{token}")))
    assert calls == []


def test_run_uses_allowlisted_environment(monkeypatch):
    seen = {}

    class Proc:
        returncode = 0
        stdout = "ActiveState=active\nMainPID=42"
        stderr = ""

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return Proc()

    monkeypatch.setattr(server_ops.subprocess, "run", fake_run)
    result = server_ops.status()
    assert result.ok
    assert seen["args"][:2] == ["sudo", "-n"]
    assert seen["args"][-1] == "status"
    assert "BOT_TOKEN" not in seen["env"]
