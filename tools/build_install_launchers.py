"""Build signed-release installer bodies and short GitHub Pages entry points.

The static entry points trust the pinned publisher key, not a mutable latest
download: they verify a detached OpenSSH signature before executing an installer.
The installer then independently verifies its commit-pinned bootstrap.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
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
DEFAULT_MACOS_INSTALLER_TAG = ""


def build_installers(
    commit_id: str,
    release_tag: str,
    platforms: set[str] | None = None,
    *,
    macos_source_preview: bool = False,
) -> dict[str, str]:
    commands = build_platform_commands(
        commit_id, release_tag,
        macos_source_preview=macos_source_preview,
    )
    installers = {
        INSTALLERS["windows"]: (
            "param([string]$LogPath)\n"
            "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'\n"
            "if(-not $LogPath){$LogPath=Join-Path $env:TEMP ('xider-install-'+[guid]::NewGuid().ToString('N')+'.log')}\n"
            "New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null\n"
            "Start-Transcript -Path $LogPath -Force | Out-Null\n"
            "try {\n"
            f"# XIDER {release_tag}: agent-only installer; commit {commit_id.lower()}\n"
            "Write-Host 'XIDER: verifying the signed Windows bootstrap...'\n"
            + commands["windows"] + "\n"
            "} catch { Write-Host ('XIDER installer error: ' + $_.Exception.Message) -ForegroundColor Red; throw }\n"
            "finally { Stop-Transcript | Out-Null }\n"
        ),
        INSTALLERS["macos"]: (
            "#!/bin/bash\n"
            f"# XIDER {release_tag}: "
            f"{'macOS source preview; runtime is not Apple-signed or notarized' if macos_source_preview else 'agent-only installer'}; "
            f"commit {commit_id.lower()}\n"
            "printf '%s\\n' 'XIDER: verifying the signed macOS bootstrap...'\n"
            + commands["macos"] + "\n"
        ),
    }
    if platforms is None:
        return installers
    if not platforms or not platforms <= set(INSTALLERS):
        raise ValueError("platforms must be a non-empty subset of windows, macos")
    return {filename: body for platform, filename in INSTALLERS.items() if platform in platforms for body in (installers[filename],)}


def build_pages_launchers() -> dict[str, str]:
    """Build static, pinned-key entry points; release tags can pin OS-specific assets."""
    allowed_b64 = base64.b64encode(_openssh_allowed_signers().encode("ascii")).decode("ascii")
    base = f"https://github.com/{REPOSITORY}/releases/latest/download"
    windows_tag = os.environ.get("XIDER_WINDOWS_INSTALLER_TAG", "").strip()
    if windows_tag and not re.fullmatch(r"v\d+\.\d+\.\d+", windows_tag):
        raise ValueError("XIDER_WINDOWS_INSTALLER_TAG must be a semantic release tag.")
    macos_tag = os.environ.get("XIDER_MACOS_INSTALLER_TAG", DEFAULT_MACOS_INSTALLER_TAG).strip()
    if macos_tag and not re.fullmatch(r"v\d+\.\d+\.\d+", macos_tag):
        raise ValueError("XIDER_MACOS_INSTALLER_TAG must be a semantic release tag.")
    windows_base = (
        f"https://github.com/{REPOSITORY}/releases/download/{windows_tag}"
        if windows_tag else base
    )
    macos_base = (
        f"https://github.com/{REPOSITORY}/releases/download/{macos_tag}"
        if macos_tag else base
    )
    macos_source_preview = os.environ.get("XIDER_MACOS_SOURCE_PREVIEW", "").strip().lower() in {"1", "true", "yes"}
    macos_preview_notice = (
        "printf '%s\\n' 'XIDER: selecting the signed macOS source-preview installer (runtime is not Apple-signed or notarized)...'\n"
        if macos_source_preview
        else "printf '%s\\n' 'XIDER: selecting the signed macOS installer...'\n"
    )
    mac = (
        "#!/bin/bash\nset -eu\n"
        "for tool in curl base64 shasum ssh-keygen; do\n"
        "  command -v \"$tool\" >/dev/null 2>&1 || { printf 'XIDER: required tool missing: %s\\n' \"$tool\" >&2; exit 1; }\n"
        "done\n"
        + macos_preview_notice
        + f"base='{macos_base}'\n"
        "tag=\"${XIDER_INSTALL_TAG:-}\"\n"
        "if [ -n \"$tag\" ]; then\n"
        "  [[ \"$tag\" =~ ^v[0-9]{1,6}\\.[0-9]{1,6}\\.[0-9]{1,6}$ ]] || { printf '%s\\n' 'XIDER: invalid release tag.' >&2; exit 1; }\n"
        f"  base=\"https://github.com/{REPOSITORY}/releases/download/${{tag}}\"\n"
        "fi\n"
        "d=\"$(mktemp -d)\"\ntrap 'rm -rf \"$d\"' EXIT\n"
        "p=\"$d/install.sh\"; s=\"$d/install.sig\"; a=\"$d/allowed_signers\"\n"
        f"curl -fsSL --max-time 90 \"$base/{INSTALLERS['macos']}\" -o \"$p\"\n"
        f"curl -fsSL --max-time 90 \"$base/{INSTALLERS['macos']}.sig\" -o \"$s\"\n"
        f"printf '%s' '{allowed_b64}' | base64 -D > \"$a\"\n"
        "h=\"$(shasum -a 256 \"$p\" | awk '{print $1}')\"\n"
        "printf 'XIDER-INSTALLER-SHA256\\nmacos\\n%s\\n' \"$h\" | "
        f"ssh-keygen -Y verify -f \"$a\" -I {SIGNER_IDENTITY} -n {INSTALLER_NAMESPACE} -s \"$s\"\n"
        "bash \"$p\"\n"
    )
    windows = (
        "$ErrorActionPreference='Stop'\n"
        "$ProgressPreference='SilentlyContinue'\n"
        "function Get-XiderSha256Hex { param([Parameter(Mandatory)][string]$Path); $stream=[IO.File]::OpenRead($Path); $algorithm=[Security.Cryptography.SHA256]::Create(); try { [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() } finally { $stream.Dispose(); $algorithm.Dispose() } }\n"
        "$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))\n"
        "New-Item -ItemType Directory -Path $d|Out-Null\ntry {\n"
        "  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'; $manifestPath=Join-Path $d 'release-manifest.json'\n"
        "  Write-Host 'XIDER: checking the release and signature verifier...'\n"
        "  $sshCommand=Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue\n"
        "  if(-not $sshCommand){$sshPath=Join-Path $env:WINDIR 'System32\\OpenSSH\\ssh-keygen.exe'; if(Test-Path -LiteralPath $sshPath -PathType Leaf){$sshCommand=[pscustomobject]@{Source=$sshPath}}}\n"
        "  $tag=$env:XIDER_INSTALL_TAG\n"
        "  if($tag -and $tag -notmatch '^v\\d{1,6}\\.\\d{1,6}\\.\\d{1,6}$'){throw 'XIDER release tag is invalid.'}\n"
        f"  $installerBase='{windows_base}'\n"
        "  $repo='" + REPOSITORY + "'; $apiRoot='https://api.github.com/repos/'+$repo; $headers=@{Accept='application/vnd.github+json';'User-Agent'='XIDER-Windows-Installer'}\n"
        "  if(-not $tag -and $installerBase -match '/releases/download/(v\\d+\\.\\d+\\.\\d+)$'){$tag=$Matches[1]}\n"
        "  if(-not $tag){$release=Invoke-RestMethod -UseBasicParsing -TimeoutSec 30 -Headers $headers -Uri ($apiRoot+'/releases/latest'); $tag=[string]$release.tag_name}\n"
        "  if($tag -notmatch '^v\\d{1,6}\\.\\d{1,6}\\.\\d{1,6}$'){throw 'GitHub returned an invalid XIDER release tag.'}\n"
        "  $release=Invoke-RestMethod -UseBasicParsing -TimeoutSec 30 -Headers $headers -Uri ($apiRoot+'/releases/tags/'+$tag)\n"
        "  if($release.draft -or [string]$release.tag_name -cne $tag){throw 'GitHub release tag does not match the selected XIDER version.'}\n"
        "  $installerBase='https://github.com/'+$repo+'/releases/download/'+$tag\n"
        "  Write-Host ('XIDER: downloading XIDER '+$tag+' from '+$repo+'...')\n"
        f"  Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri \"$installerBase/{INSTALLERS['windows']}\" -OutFile $p\n"
        "  $hash=Get-XiderSha256Hex -Path $p\n"
        "  if($sshCommand){\n"
        f"    Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri \"$installerBase/{INSTALLERS['windows']}.sig\" -OutFile $s\n"
        f"    [IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('{allowed_b64}'))\n"
        "    [IO.File]::WriteAllBytes($m,[Text.Encoding]::ASCII.GetBytes(\"XIDER-INSTALLER-SHA256`nwindows`n\"+$hash+\"`n\"))\n"
        "    $ssh=$sshCommand.Source; $c=Join-Path $d 'verify.cmd'\n"
        f"    $verify='@echo off'+[Environment]::NewLine+'pushd \"%~dp0\"'+[Environment]::NewLine+'\"'+$ssh+'\" -Y verify -f \"allowed_signers\" -I {SIGNER_IDENTITY} -n {INSTALLER_NAMESPACE} -s \"install.sig\" < \"message\"'+[Environment]::NewLine+'exit /b %errorlevel%'\n"
        "    [IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII)\n"
        "    $psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; $psi.Arguments='/d /s /c \"\"'+$c+'\"\"'; $psi.UseShellExecute=$false\n"
        "    $v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); $v.WaitForExit(); if($v.ExitCode -ne 0){throw 'XIDER installer signature verification failed'}\n"
        "    Write-Host 'XIDER: release signature verified.'\n"
        "  } else {\n"
        "    Write-Host 'XIDER: OpenSSH is absent; using GitHub HTTPS metadata and release-manifest SHA-256 checks (detached signature is not verified).' -ForegroundColor Yellow\n"
        "    $manifestAsset=@($release.assets | Where-Object { $_.name -ceq 'release-manifest.json' }); $installerAsset=@($release.assets | Where-Object { $_.name -ceq 'XIDER-install-windows.ps1' })\n"
        "    if($manifestAsset.Count -ne 1 -or $installerAsset.Count -ne 1 -or $manifestAsset[0].digest -notmatch '^sha256:[0-9a-fA-F]{64}$' -or $installerAsset[0].digest -notmatch '^sha256:[0-9a-fA-F]{64}$'){throw 'GitHub release metadata has no unique SHA-256 digest for the installer and manifest.'}\n"
        "    Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri ($installerBase+'/release-manifest.json') -OutFile $manifestPath\n"
        "    $manifestHash=Get-XiderSha256Hex -Path $manifestPath; $expectedManifestHash=([string]$manifestAsset[0].digest).Substring(7).ToLowerInvariant(); if($manifestHash -cne $expectedManifestHash){throw ('Release manifest SHA-256 mismatch (actual '+$manifestHash+', expected '+$expectedManifestHash+').')}\n"
        "    $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json; if($manifest.schema -ne 1 -or [string]$manifest.release -cne $tag -or -not $manifest.assets -or $manifest.signature.algorithm -cne 'ed25519' -or -not $manifest.signature.key_id -or -not $manifest.signature.value){throw 'Release manifest schema, signature envelope, or release tag is invalid.'}\n"
        "    $entries=@($manifest.assets | Where-Object { $_.name -ceq 'XIDER-install-windows.ps1' -and $_.component -ceq 'installer' -and $_.platform -ceq 'windows' }); if($entries.Count -ne 1){throw 'Release manifest has no unique Windows installer entry.'}\n"
        "    $actualSize=(Get-Item -LiteralPath $p).Length; $manifestDigest=([string]$entries[0].sha256).ToLowerInvariant(); $apiDigest=([string]$installerAsset[0].digest).Substring(7).ToLowerInvariant()\n"
        "    if($manifestDigest -notmatch '^[0-9a-f]{64}$' -or $hash -cne $manifestDigest -or $hash -cne $apiDigest -or [long]$entries[0].size -ne $actualSize -or [long]$installerAsset[0].size -ne $actualSize){throw 'Installer failed release-manifest or SHA-256 validation.'}\n"
        "  }\n"
        "  $logRoot=Join-Path $env:LOCALAPPDATA 'XIDER\\logs'; New-Item -ItemType Directory -Path $logRoot -Force|Out-Null\n"
        "  Get-ChildItem -LiteralPath $logRoot -File -Filter 'install-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -Skip 9 | ForEach-Object { if($_.Name -match '^install-\\d{8}-\\d{6}\\.log$' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)){Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue} }\n"
        "  $log=Join-Path $logRoot ('install-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')\n"
        "  $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()); $isAdmin=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\n"
        "  $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$p,'-LogPath',$log)\n"
        "  if(-not $isAdmin){Write-Host 'XIDER: requesting administrator approval (UAC)...'}\n"
        "  $powershellPath=Join-Path $env:WINDIR 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
        "  if($isAdmin){& $powershellPath @args; $installerExitCode=$LASTEXITCODE} else {\n"
        "    $argumentLine='-NoProfile -ExecutionPolicy Bypass -File \"{0}\" -LogPath \"{1}\"' -f $p.Replace('\"','\"\"'),$log.Replace('\"','\"\"')\n"
        "    $installerProcess=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\\WindowsPowerShell\\v1.0\\powershell.exe') -ArgumentList $argumentLine -Verb RunAs -Wait -PassThru; $installerExitCode=$installerProcess.ExitCode\n"
        "  }\n"
        "  if($installerExitCode -ne 0){ Write-Host ('XIDER install log: '+$log) -ForegroundColor Yellow; if(Test-Path -LiteralPath $log){Get-Content -LiteralPath $log -Tail 80}; throw ('XIDER Windows installer failed with exit code '+$installerExitCode) }\n"
        "} catch { Write-Host ('XIDER launcher failed: '+$_.Exception.Message) -ForegroundColor Red; throw } finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }\n"
    )
    # Keep the bot's one-line `irm .../win.txt | iex` route as well as the
    # downloadable .ps1. GitHub Pages serves .ps1 as application/octet-stream,
    # which PowerShell reads as byte[] instead of executable script text.
    return {"mac": mac, "win.ps1": windows, "win.txt": windows, ".nojekyll": ""}


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
    parser.add_argument("--macos-source-preview", action="store_true")
    args = parser.parse_args()
    try:
        installers = build_installers(
            args.commit_id, args.release_tag,
            macos_source_preview=args.macos_source_preview,
        )
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
