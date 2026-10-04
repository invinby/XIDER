"""Build signed-release installer bodies and short GitHub Pages entry points.

The static entry points trust the pinned publisher key, not a mutable latest
download: they verify a detached OpenSSH signature before executing an installer.
The installer then independently verifies its commit-pinned bootstrap.
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from build_bootstrap_commands import (
    REPOSITORY,
    SIGNER_IDENTITY,
    _openssh_allowed_signers,
    build_platform_commands,
)


INSTALLERS = {
    "windows": "XIDER-install-windows.ps1",
    "macos": "XIDER-install-macos.sh",
}
INSTALLER_NAMESPACE = "xider-installer@xider.link"


def build_installers(commit_id: str, release_tag: str) -> dict[str, str]:
    commands = build_platform_commands(commit_id, release_tag)
    return {
        INSTALLERS["windows"]: (
            f"# XIDER {release_tag}: agent-only installer; commit {commit_id.lower()}\n"
            "Write-Host 'XIDER: verifying the signed Windows bootstrap...'\n"
            + commands["windows"] + "\n"
        ),
        INSTALLERS["macos"]: (
            "#!/bin/bash\n"
            f"# XIDER {release_tag}: agent-only installer; commit {commit_id.lower()}\n"
            "printf '%s\\n' 'XIDER: verifying the signed macOS bootstrap...'\n"
            + commands["macos"] + "\n"
        ),
    }


def build_pages_launchers() -> dict[str, str]:
    """Static, pinned-key entry points; fail closed if latest assets are absent."""
    allowed_b64 = base64.b64encode(_openssh_allowed_signers().encode("ascii")).decode("ascii")
    base = f"https://github.com/{REPOSITORY}/releases/latest/download"
    mac = (
        "#!/bin/bash\nset -eu\n"
        "for tool in curl base64 shasum ssh-keygen; do\n"
        "  command -v \"$tool\" >/dev/null 2>&1 || { printf 'XIDER: required tool missing: %s\\n' \"$tool\" >&2; exit 1; }\n"
        "done\n"
        "printf '%s\\n' 'XIDER: downloading the latest signed macOS installer...'\n"
        "d=\"$(mktemp -d)\"\ntrap 'rm -rf \"$d\"' EXIT\n"
        "p=\"$d/install.sh\"; s=\"$d/install.sig\"; a=\"$d/allowed_signers\"\n"
        f"curl -fsSL --max-time 90 '{base}/{INSTALLERS['macos']}' -o \"$p\"\n"
        f"curl -fsSL --max-time 90 '{base}/{INSTALLERS['macos']}.sig' -o \"$s\"\n"
        f"printf '%s' '{allowed_b64}' | base64 -D > \"$a\"\n"
        "h=\"$(shasum -a 256 \"$p\" | awk '{print $1}')\"\n"
        "printf 'XIDER-INSTALLER-SHA256\\nmacos\\n%s\\n' \"$h\" | "
        f"ssh-keygen -Y verify -f \"$a\" -I {SIGNER_IDENTITY} -n {INSTALLER_NAMESPACE} -s \"$s\"\n"
        "bash \"$p\"\n"
    )
    windows = (
        "$ErrorActionPreference='Stop'\n"
        "$ProgressPreference='SilentlyContinue'\n"
        "Write-Host 'XIDER: downloading the latest signed Windows installer...'\n"
        "$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))\n"
        "New-Item -ItemType Directory -Path $d|Out-Null\ntry {\n"
        "  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'\n"
        f"  iwr -UseBasicParsing -TimeoutSec 90 -Uri '{base}/{INSTALLERS['windows']}' -OutFile $p\n"
        f"  iwr -UseBasicParsing -TimeoutSec 90 -Uri '{base}/{INSTALLERS['windows']}.sig' -OutFile $s\n"
        f"  [IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('{allowed_b64}'))\n"
        "  $f=[IO.File]::OpenRead($p); $sha=[Security.Cryptography.SHA256]::Create()\n"
        "  try{$hash=[BitConverter]::ToString($sha.ComputeHash($f)).Replace('-','').ToLowerInvariant()} finally{$f.Dispose();$sha.Dispose()}\n"
        "  $ssh=(Get-Command ssh-keygen.exe -ErrorAction Stop).Source\n"
        "  [IO.File]::WriteAllBytes($m,[Text.Encoding]::ASCII.GetBytes(\"XIDER-INSTALLER-SHA256`nwindows`n\"+$hash+\"`n\"))\n"
        "  $c=Join-Path $d 'verify.cmd'\n"
        f"  $verify='@echo off'+[Environment]::NewLine+'pushd \"%~dp0\"'+[Environment]::NewLine+'\"'+$ssh+'\" -Y verify -f \"allowed_signers\" -I {SIGNER_IDENTITY} -n {INSTALLER_NAMESPACE} -s \"install.sig\" < \"message\"'+[Environment]::NewLine+'exit /b %errorlevel%'\n"
        "  [IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII)\n"
        "  $psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; $psi.Arguments='/d /s /c \"\"'+$c+'\"\"'; $psi.UseShellExecute=$false\n"
        "  $v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); $v.WaitForExit()\n"
        "  if($v.ExitCode -ne 0){throw 'XIDER installer signature verification failed'}\n"
        "  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p\n"
        "  if($LASTEXITCODE -ne 0){throw 'XIDER Windows installer failed'}\n"
        "} finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }\n"
    )
    return {"mac": mac, "win.ps1": windows, ".nojekyll": ""}


def write_files(directory: Path, files: dict[str, str]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name, content in files.items():
        path = directory / name
        path.write_text(content, encoding="utf-8", newline="\n")
        outputs.append(path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit_id")
    parser.add_argument("release_tag")
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages-dir", type=Path)
    args = parser.parse_args()
    try:
        installers = build_installers(args.commit_id, args.release_tag)
        pages = build_pages_launchers() if args.pages_dir else None
    except ValueError as exc:
        parser.error(str(exc))
    for path in write_files(args.output, installers):
        print(path)
    if pages is not None:
        for path in write_files(args.pages_dir, pages):
            print(path)


if __name__ == "__main__":
    main()
