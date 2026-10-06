"""Catch shell syntax/line-ending regressions in the macOS entry scripts."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SHELL_SCRIPTS = (
    "build_standalone.sh",
    "make_app.sh",
    "start_agent.sh",
    "start_guardian.sh",
    "stop_agent.sh",
)


@pytest.mark.parametrize("script_name", SHELL_SCRIPTS)
def test_mac_shell_script_parses_with_bash(script_name):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is unavailable; shell syntax validation needs Bash.")

    result = subprocess.run(
        [bash, "-n", script_name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_mac_agent_status_falls_back_when_launchd_job_is_waiting(tmp_path):
    if os.name == "nt":
        pytest.skip("The process/launchd status fixture requires a Unix process table.")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is unavailable; shell behavior validation needs Bash.")

    script_dir = tmp_path / "agent"
    fake_bin = tmp_path / "bin"
    script_dir.mkdir()
    fake_bin.mkdir()
    (script_dir / "start_agent.sh").write_text(
        (ROOT / "start_agent.sh").read_text(encoding="utf-8"), encoding="utf-8"
    )
    commands = {
        "launchctl": "#!/bin/sh\nprintf 'state = waiting\\n'\n",
        "pgrep": "#!/bin/sh\nprintf '4242\\n'\n",
        "ps": "#!/bin/sh\nexit 0\n",
    }
    for name, body in commands.items():
        path = fake_bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
    result = subprocess.run(
        [bash, str(script_dir / "start_agent.sh"), "--status"],
        cwd=script_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "PID: 4242" in result.stdout
