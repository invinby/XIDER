"""Exercise the workflow's actual server archive input paths."""
import shlex
import subprocess
import zipfile
from pathlib import Path


def test_server_archive_includes_canonical_release_verifier(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/build-agents.yml").read_text(encoding="utf-8")
    command = next(line.strip() for line in workflow.splitlines() if "git archive" in line and "X-STAB-server.zip" in line)
    args = shlex.split(command)
    destination = tmp_path / "server.zip"
    args = [f"--output={destination}" if arg.startswith("--output=") else arg for arg in args]
    subprocess.run(args, cwd=root, check=True, capture_output=True)
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("XGENT-MCS/release_signature.py") == (root / "XGENT-MCS/release_signature.py").read_bytes()
