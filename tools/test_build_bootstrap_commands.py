import hashlib
from pathlib import Path

import pytest

from build_bootstrap_commands import build_commands


@pytest.mark.parametrize("commit_id", ["a" * 40, "B" * 64])
def test_generated_commands_pin_script_and_source_to_the_same_commit(commit_id):
    ref = commit_id.lower()
    text = build_commands(commit_id)
    root = Path(__file__).resolve().parents[1]
    windows_hash = hashlib.sha256((root / "deploy" / "bootstrap.ps1").read_bytes()).hexdigest()
    macos_hash = hashlib.sha256((root / "deploy" / "bootstrap.sh").read_bytes()).hexdigest()

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


@pytest.mark.parametrize("commit_id", ["main", "a" * 39, "f" * 41, "../" + "a" * 40])
def test_generated_commands_reject_moving_or_invalid_refs(commit_id):
    with pytest.raises(ValueError):
        build_commands(commit_id)
