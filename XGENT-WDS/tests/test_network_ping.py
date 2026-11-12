from types import SimpleNamespace

from xg_commands import network


def test_net_ping_sanitizes_host_and_preserves_legacy_response():
    published = []
    calls = []
    network.net_ping(
        {"host": "example.com; whoami"},
        device_id="device-4",
        publish_response=lambda topic, response: published.append((topic, response)),
        run_without_console=lambda runner, args, **kwargs: calls.append((runner, args, kwargs)) or SimpleNamespace(
            stdout="4 packets received\n", stderr="", returncode=0,
        ),
        subprocess_module=SimpleNamespace(run="run-command"),
        re_module=__import__("re"),
    )

    assert calls == [("run-command", ["ping", "-n", "4", "example.comwhoami"], {
        "capture_output": True, "text": True, "timeout": 10,
    })]
    assert published == [("output", {
        "type": "net_ping", "device_id": "device-4", "ok": True,
        "text": "🏓 Результаты Ping к example.comwhoami:\n```\n4 packets received\n```",
    })]


def test_net_ping_reports_runner_failures_in_legacy_response():
    published = []
    network.net_ping(
        {}, device_id="device-4",
        publish_response=lambda topic, response: published.append((topic, response)),
        run_without_console=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
        subprocess_module=SimpleNamespace(run="run-command"), re_module=__import__("re"),
    )

    assert published == [("output", {
        "type": "net_ping", "device_id": "device-4", "ok": False,
        "text": "⚠️ Ошибка ping: timed out",
    })]
