"""Catch shell syntax/line-ending regressions in the macOS entry scripts."""

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
