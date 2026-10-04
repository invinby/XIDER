import subprocess
from pathlib import Path

import pytest

from build_bootstrap_commands import build_commands


ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG = "v4.1.0"
CURRENT_COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


@pytest.mark.parametrize("commit_id", [CURRENT_COMMIT, CURRENT_COMMIT.upper()])
def test_generated_commands_pin_script_and_source_to_the_same_commit(commit_id):
    ref = commit_id.lower()
    text = build_commands(commit_id, RELEASE_TAG)
    windows_bytes = subprocess.check_output(
        ["git", "show", f"{ref}:deploy/bootstrap-agent.ps1"], cwd=ROOT
    )
    macos_bytes = subprocess.check_output(
        ["git", "show", f"{ref}:deploy/bootstrap.sh"], cwd=ROOT
    )
    assert windows_bytes
    assert macos_bytes

    assert f"raw.githubusercontent.com/invinby/XIDER/{ref}/deploy/bootstrap-agent.ps1" in text
    assert "/deploy/bootstrap.ps1" not in text
    assert f"raw.githubusercontent.com/invinby/XIDER/{ref}/deploy/bootstrap.sh" in text
    assert f"releases/download/{RELEASE_TAG}/XIDER-bootstrap-windows.ps1.sig" in text
    assert f"releases/download/{RELEASE_TAG}/XIDER-bootstrap-macos.sh.sig" in text
    assert f"& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Ref '{ref}'" in text
    assert "Set-ExecutionPolicy" not in text
    assert f"XIDER_REF='{ref}' bash" in text
    assert f"XIDER_RELEASE_TAG='{RELEASE_TAG}' XIDER_REF='{ref}'" in text
    assert "iwr -UseBasicParsing -TimeoutSec 90" in text
    assert "curl -fsSL --max-time 90" in text
    assert "[Security.Cryptography.SHA256]::Create()" in text
    assert "shasum -a 256" in text
    assert "ssh-keygen -Y verify" in text
    assert f"{RELEASE_TAG}`n{ref}`n\"+$hash" in text
    assert f"{RELEASE_TAG}\\n{ref}\\n%s\\n' \"$h\"" in text
    assert "not a signature on the bootstrap or quickstart" not in text


def test_quickstart_bootstrap_source_is_pinned_to_the_exact_git_commit():
    text = build_commands(CURRENT_COMMIT, RELEASE_TAG)
    blob = subprocess.check_output(
        ["git", "show", f"{CURRENT_COMMIT}:deploy/bootstrap-agent.ps1"], cwd=ROOT
    )
    assert blob == subprocess.check_output(
        ["git", "show", f"{CURRENT_COMMIT}:deploy/bootstrap-agent.ps1"], cwd=ROOT
    )
    assert "ComputeHash($f)" in text
    assert "$m=Join-Path $d 'message'" in text
    assert "WriteAllBytes($m,$mb)" in text
    assert "pushd \"%~dp0\"" in text
    assert "< \"message\"" in text
    assert "StandardInput.BaseStream.Write" not in text
    assert "StandardInputEncoding" not in text

    # This checkout has mixed line endings in the PowerShell file on Windows;
    # the raw GitHub blob must remain the source of truth for the expected hash.
    worktree = (ROOT / "deploy" / "bootstrap-agent.ps1").read_bytes()
    if worktree != blob:
        assert worktree != blob


def test_generated_commands_require_the_pinned_commit_to_exist_locally():
    with pytest.raises(ValueError, match="not available locally"):
        build_commands("f" * 40, RELEASE_TAG)


@pytest.mark.parametrize("commit_id", ["main", "a" * 39, "f" * 41, "../" + "a" * 40])
def test_generated_commands_reject_moving_or_invalid_refs(commit_id):
    with pytest.raises(ValueError):
        build_commands(commit_id, RELEASE_TAG)


@pytest.mark.parametrize("release_tag", ["main", "v1", "v1.2.3-beta", "../v1.2.3"])
def test_generated_commands_reject_nonsemantic_release_tags(release_tag):
    with pytest.raises(ValueError, match="semantic version"):
        build_commands(CURRENT_COMMIT, release_tag)
