"""Generate copy-ready installers pinned to one full Git commit ID."""

from __future__ import annotations

import argparse
import base64
import re
import struct
import subprocess
import sys
from pathlib import Path


COMMIT_ID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
REPOSITORY = "invinby/XIDER"
ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SIGNER_IDENTITY = "xider-release"
SIGNATURE_NAMESPACE = "xider-bootstrap@xider.link"

RELEASE_MODULE_DIR = ROOT / "XGENT-MCS"
if str(RELEASE_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_MODULE_DIR))
from release_signature import TRUSTED_RELEASE_KEYS


def _openssh_allowed_signers() -> str:
    lines = []
    key_type = b"ssh-ed25519"
    for public_key in TRUSTED_RELEASE_KEYS.values():
        wire_key = (
            struct.pack(">I", len(key_type))
            + key_type
            + struct.pack(">I", len(public_key))
            + public_key
        )
        encoded = base64.b64encode(wire_key).decode("ascii")
        lines.append(f"{SIGNER_IDENTITY} ssh-ed25519 {encoded}")
    if not lines:
        raise ValueError("No pinned bootstrap signing keys are configured.")
    return "\n".join(lines) + "\n"


def _committed_file(commit_id: str, relative_path: str) -> bytes:
    """Read the exact bytes GitHub serves for a file at the pinned commit.

    A Windows worktree can normalize line endings through core.autocrlf, so
    checking the Git blob avoids treating local checkout bytes as release data.
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


def build_commands(commit_id: str, release_tag: str) -> str:
    if not isinstance(commit_id, str) or not COMMIT_ID.fullmatch(commit_id):
        raise ValueError("A full 40- or 64-character Git commit ID is required.")
    if not isinstance(release_tag, str) or not RELEASE_TAG.fullmatch(release_tag):
        raise ValueError("A semantic version release tag such as v4.1.0 is required.")

    ref = commit_id.lower()
    raw_base = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}"
    release_base = f"https://github.com/{REPOSITORY}/releases/download/{release_tag}"
    # Require a locally available commit that contains both pinned bootstraps;
    # the signed release artifact authenticates the bytes fetched from its URL.
    for path in ("deploy/bootstrap.ps1", "deploy/bootstrap.sh"):
        if not _committed_file(ref, path):
            raise ValueError(f"Pinned bootstrap source {path} is empty.")
    allowed_signers_b64 = base64.b64encode(
        _openssh_allowed_signers().encode("ascii")
    ).decode("ascii")
    return (
        "XIDER quick install (signed bootstrap; source pinned to this release commit)\n"
        f"Commit: {ref}\n\n"
        "Windows PowerShell:\n"
        "$ErrorActionPreference='Stop'; $d=Join-Path $env:TEMP ('xider-'+[guid]::NewGuid().ToString('N')); "
        "New-Item -ItemType Directory -Path $d|Out-Null; try { "
        "$p=Join-Path $d 'bootstrap.ps1'; $s=Join-Path $d 'bootstrap.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'; "
        f"iwr -UseBasicParsing -TimeoutSec 90 -Uri '{raw_base}/deploy/bootstrap.ps1' -OutFile $p; "
        f"iwr -UseBasicParsing -TimeoutSec 90 -Uri '{release_base}/XIDER-bootstrap-windows.ps1.sig' -OutFile $s; "
        f"[IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('{allowed_signers_b64}')); "
        "$f=[IO.File]::OpenRead($p); $sha=[Security.Cryptography.SHA256]::Create(); "
        "try{$hash=[BitConverter]::ToString($sha.ComputeHash($f)).Replace('-','').ToLowerInvariant()} "
        "finally{$f.Dispose();$sha.Dispose()}; "
        "$ssh=(Get-Command ssh-keygen.exe -ErrorAction Stop).Source; "
        f"$mb=[Text.Encoding]::ASCII.GetBytes(\"XIDER-BOOTSTRAP-SHA256`nwindows`n{release_tag}`n{ref}`n\"+$hash+\"`n\"); "
        "[IO.File]::WriteAllBytes($m,$mb); "
        "$c=Join-Path $d 'verify.cmd'; "
        f"$verify='@echo off'+[Environment]::NewLine+'pushd \"%~dp0\"'+[Environment]::NewLine+'\"'+$ssh+'\" -Y verify -f \"allowed_signers\" -I {SIGNER_IDENTITY} -n {SIGNATURE_NAMESPACE} -s \"bootstrap.sig\" < \"message\"'+[Environment]::NewLine+'exit /b %errorlevel%'; "
        "[IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII); "
        "$psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; "
        "$psi.Arguments='/d /s /c \"\"'+$c+'\"\"'; $psi.UseShellExecute=$false; "
        "$v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); "
        "$v.WaitForExit(); if($v.ExitCode -ne 0){throw 'XIDER bootstrap signature verification failed'}; "
        f"& $p -Ref '{ref}' }} finally {{ Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }}\n\n"
        "macOS Terminal:\n"
        "set -eu; d=\"$(mktemp -d)\"; trap 'rm -rf \"$d\"' EXIT; "
        f"p=\"$d/bootstrap.sh\"; s=\"$d/bootstrap.sig\"; a=\"$d/allowed_signers\"; "
        f"curl -fsSL --max-time 90 '{raw_base}/deploy/bootstrap.sh' -o \"$p\"; "
        f"curl -fsSL --max-time 90 '{release_base}/XIDER-bootstrap-macos.sh.sig' -o \"$s\"; "
        f"printf '%s' '{allowed_signers_b64}' | base64 -D > \"$a\"; "
        f"h=\"$(shasum -a 256 \"$p\" | awk '{{print $1}}')\"; "
        f"printf 'XIDER-BOOTSTRAP-SHA256\\nmacos\\n{release_tag}\\n{ref}\\n%s\\n' \"$h\" | "
        f"ssh-keygen -Y verify -f \"$a\" -I {SIGNER_IDENTITY} -n {SIGNATURE_NAMESPACE} -s \"$s\"; "
        f"XIDER_REF='{ref}' bash \"$p\"\n\n"
        "Both commands verify an OpenSSH Ed25519 signature over the downloaded bootstrap SHA-256, platform, release tag, and commit before execution.\n"
        "The source archive is pinned to the same full commit. OpenSSH ssh-keygen is required on the client.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit_id")
    parser.add_argument("release_tag")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        contents = build_commands(args.commit_id, args.release_tag)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(contents, encoding="utf-8", newline="\n")
    print(args.output)


if __name__ == "__main__":
    main()
