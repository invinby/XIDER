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


def _usable_bash():
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.extend((
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ))
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not Path(candidate).is_file():
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, check=False, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        if result.returncode == 0 and "GNU bash" in output:
            return candidate
    return None


@pytest.mark.parametrize("script_name", SHELL_SCRIPTS)
def test_mac_shell_script_parses_with_bash(script_name):
    bash = _usable_bash()
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


@pytest.mark.parametrize("version,accepted", [("4.2.1", True), ("4.2", False)])
def test_make_app_executes_real_version_preflight(tmp_path, version, accepted):
    bash = _usable_bash()
    if bash is None:
        pytest.skip("Bash is unavailable; executable version preflight needs Bash.")
    (tmp_path / "make_app.sh").write_text(
        (ROOT / "make_app.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    (tmp_path / "config.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8", newline="\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # Only the external build/signing boundary is doubled. The actual sed,
    # Bash version validation, and non-ad-hoc identity check run unchanged.
    python = fake_bin / "python3"
    python.write_text('#!/usr/bin/env bash\nprintf "BUILD_BOUNDARY %s\\n" "$*"\n',
                      encoding="utf-8", newline="\n")
    python.chmod(0o755)
    env = os.environ.copy()
    env["XIDER_MACOS_SIGNING_IDENTITY"] = "Apple Development: Fixture (TEAM)"
    result = subprocess.run(
        [bash, "-c", 'export PATH="$PWD/bin:$PATH"; '
         'export XIDER_MACOS_SIGNING_IDENTITY="Apple Development: Fixture (TEAM)"; '
         'exec bash ./make_app.sh'],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    if accepted:
        assert result.returncode == 0, result.stderr or result.stdout
        assert f"Built signed XIDER.app {version};" in result.stdout
        assert "BUILD_BOUNDARY -m PyInstaller" in result.stdout
        assert "BUILD_BOUNDARY ../tools/verify_macos_bundle.py" in result.stdout
    else:
        assert result.returncode == 1
        assert "Could not read the agent version" in result.stderr
        assert "BUILD_BOUNDARY" not in result.stdout


def test_mac_agent_status_falls_back_when_launchd_job_is_waiting(tmp_path):
    if os.name == "nt":
        pytest.skip("The process/launchd status fixture requires a Unix process table.")
    bash = _usable_bash()
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
