import json
from types import SimpleNamespace

from xg_commands import startup


def test_autorun_status_combines_persisted_preference_with_task_presence(tmp_path):
    (tmp_path / "guardian.json").write_text(json.dumps({"startup_enabled": True}), encoding="utf-8")
    sent = []
    startup.autorun_status(
        {}, device_id="d", publish_response=lambda *args: sent.append(args), config_dir=tmp_path,
        task_exists=lambda: True, task_name="XIDER Agent",
    )
    assert sent[0][1] == {
        "type": "autorun_status", "device_id": "d", "ok": True,
        "text": "🚀 Автозапуск Windows: ✅ ВКЛЮЧЕН\nИсточник: Scheduled Task «XIDER Agent»",
    }


def test_autorun_enable_keeps_enable_then_persist_then_run_order():
    calls, sent = [], []

    def run_without_console(run, command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    startup.autorun_enable(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        task_exists=lambda: True, task_name="XIDER Agent", run_without_console=run_without_console,
        run="subprocess.run", set_startup_enabled=lambda enabled: calls.append(("state", enabled)),
    )
    assert calls == [
        (["schtasks.exe", "/Change", "/TN", "XIDER Agent", "/ENABLE"],
         {"capture_output": True, "text": True, "timeout": 20, "check": False}),
        ("state", True),
        (["schtasks.exe", "/Run", "/TN", "XIDER Agent"],
         {"capture_output": True, "text": True, "timeout": 20, "check": False}),
    ]
    assert sent[0][1]["ok"] is True and sent[0][1]["type"] == "autorun_enable"


def test_autorun_disable_removes_legacy_run_values_after_task_disable():
    calls, deleted, sent = [], [], []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    winreg_module = SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_ALL_ACCESS=2,
        OpenKey=lambda *args: (calls.append(("OpenKey", args)) or Key()),
        DeleteValue=lambda _key, name: (deleted.append(name) or None),
    )

    def run_without_console(run, command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    startup.autorun_disable(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        task_exists=lambda: True, task_name="XIDER Agent", run_without_console=run_without_console,
        run="subprocess.run", winreg_module=winreg_module,
        set_startup_enabled=lambda enabled: calls.append(("state", enabled)),
        logger=SimpleNamespace(exception=lambda *_args: None),
    )
    assert calls[0] == ("state", False)
    assert calls[1][0] == ["schtasks.exe", "/Change", "/TN", "XIDER Agent", "/DISABLE"]
    assert deleted == ["XGENT", "XGentAgent"]
    assert sent[0][1]["ok"] is True and sent[0][1]["type"] == "autorun_disable"


def test_autorun_disable_restores_persisted_state_when_task_disable_fails():
    state, sent = [], []

    startup.autorun_disable(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        task_exists=lambda: True, task_name="XIDER Agent",
        run_without_console=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stderr="denied", stdout="",
        ),
        run="subprocess.run", winreg_module=None,
        set_startup_enabled=state.append, logger=SimpleNamespace(exception=lambda *_args: None),
    )
    assert state == [False, True]
    assert sent[0][1]["ok"] is False
    assert "denied" in sent[0][1]["text"]
