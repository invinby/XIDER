import server_ops


def test_run_uses_allowlisted_environment(monkeypatch):
    seen = {}

    class Proc:
        returncode = 0
        stdout = "ActiveState=active"
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
