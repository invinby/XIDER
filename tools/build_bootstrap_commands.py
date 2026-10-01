"""Generate copy-ready installers pinned to one full Git commit ID."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path


COMMIT_ID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
REPOSITORY = "invinby/XIDER"
ROOT = Path(__file__).resolve().parents[1]


def _committed_file(commit_id: str, relative_path: str) -> bytes:
    """Read the exact bytes GitHub serves for a file at the pinned commit.

    Hashing a Windows worktree file is not reliable with core.autocrlf or a
    mixed-line-ending checkout: its bytes can differ from the immutable Git
    blob even though the source looks identical in an editor.
    """
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit_id}:{relative_path}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"Commit {commit_id} or required file {relative_path} is not available locally."
        ) from exc


def build_commands(commit_id: str) -> str:
    if not isinstance(commit_id, str) or not COMMIT_ID.fullmatch(commit_id):
        raise ValueError("A full 40- or 64-character Git commit ID is required.")

    ref = commit_id.lower()
    raw_base = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}"
    windows_hash = hashlib.sha256(_committed_file(ref, "deploy/bootstrap.ps1")).hexdigest()
    macos_hash = hashlib.sha256(_committed_file(ref, "deploy/bootstrap.sh")).hexdigest()
    return (
        "XIDER quick install (pinned to this release commit)\n"
        f"Commit: {ref}\n\n"
        "Windows PowerShell:\n"
        f"$ErrorActionPreference='Stop'; $p=Join-Path $env:TEMP ([guid]::NewGuid().ToString('N')+'.ps1'); "
        f"try {{ iwr -UseBasicParsing -TimeoutSec 90 -Uri '{raw_base}/deploy/bootstrap.ps1' -OutFile $p; "
        f"if ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash -ne '{windows_hash}') "
        "{ throw 'XIDER bootstrap SHA-256 mismatch' }; & $p -Ref '"
        f"{ref}' }} finally {{ Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }}\n\n"
        "macOS Terminal:\n"
        f"t=\"$(mktemp)\" && curl -fsSL --max-time 90 '{raw_base}/deploy/bootstrap.sh' -o \"$t\" && "
        f"printf '%s  %s\\n' '{macos_hash}' \"$t\" | shasum -a 256 -c - && "
        f"XIDER_REF={ref} bash \"$t\"; rc=$?; rm -f \"$t\"; exit \"$rc\"\n\n"
        "Both commands check the downloaded bootstrap SHA-256 before execution and pin the source archive to this commit.\n"
        "The hash detects byte mismatches; transport trust is HTTPS/GitHub, not a signature on the bootstrap or quickstart.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit_id")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        contents = build_commands(args.commit_id)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(contents, encoding="utf-8", newline="\n")
    print(args.output)


if __name__ == "__main__":
    main()
