import hashlib
import subprocess
from pathlib import Path

import pytest

from build_bootstrap_commands import build_commands


ROOT = Path(__file__).resolve().parents[1]
CURRENT_COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


@pytest.mark.parametrize("commit_id", [CURRENT_COMMIT, CURRENT_COMMIT.upper()])
def test_generated_commands_pin_script_and_source_to_the_same_commit(commit_id):
    ref = commit_id.lower()
    text = build_commands(commit_id)
    windows_bytes = subprocess.check_output(
        ["git", "show", f"{ref}:deploy/bootstrap.ps1"], cwd=ROOT
    )
    macos_bytes = subprocess.check_output(
        ["git", "show", f"{ref}:deploy/bootstrap.sh"], cwd=ROOT
    )
    windows_hash = hashlib.sha256(windows_bytes).hexdigest()
    macos_hash = hashlib.sha256(macos_bytes).hexdigest()

    assert f"raw.githubusercontent.com/invinby/XIDER/{ref}/deploy/bootstrap.ps1" in text
    assert f"raw.githubusercontent.com/invinby/XIDER/{ref}/deploy/bootstrap.sh" in text
    assert f"-ne '{windows_hash}'" in text
    assert f"printf '%s  %s\\n' '{macos_hash}'" in text
    assert f"& $p -Ref '{ref}'" in text
    assert f"XIDER_REF={ref} bash" in text
    assert "iwr -UseBasicParsing -TimeoutSec 90" in text
    assert "curl -fsSL --max-time 90" in text
    assert "Get-FileHash -LiteralPath $p -Algorithm SHA256" in text
    assert "shasum -a 256 -c -" in text
    assert "not a signature on the bootstrap or quickstart" in text


def test_quickstart_hash_comes_from_the_pinned_git_blob_not_worktree_bytes():
    text = build_commands(CURRENT_COMMIT)
    blob = subprocess.check_output(
        ["git", "show", f"{CURRENT_COMMIT}:deploy/bootstrap.ps1"], cwd=ROOT
    )
    blob_hash = hashlib.sha256(blob).hexdigest()
    assert f"-ne '{blob_hash}'" in text

    # This checkout has mixed line endings in the PowerShell file on Windows;
    # the raw GitHub blob must remain the source of truth for the expected hash.
    worktree = (ROOT / "deploy" / "bootstrap.ps1").read_bytes()
    if worktree != blob:
        assert hashlib.sha256(worktree).hexdigest() != blob_hash


def test_generated_commands_require_the_pinned_commit_to_exist_locally():
    with pytest.raises(ValueError, match="not available locally"):
        build_commands("f" * 40)


@pytest.mark.parametrize("commit_id", ["main", "a" * 39, "f" * 41, "../" + "a" * 40])
def test_generated_commands_reject_moving_or_invalid_refs(commit_id):
    with pytest.raises(ValueError):
        build_commands(commit_id)
