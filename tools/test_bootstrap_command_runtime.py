import re
import shutil
import subprocess
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import build_bootstrap_commands as builder
import sign_bootstrap
from release_signature import release_key_id


ROOT = Path(__file__).resolve().parents[1]
COMMIT = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
TAG = "v4.1.0"
POWERSHELLS = tuple(
    shell for shell in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if shell
)
SSH_KEYGEN = shutil.which("ssh-keygen.exe") or shutil.which("ssh-keygen")


@pytest.mark.skipif(not (POWERSHELLS and SSH_KEYGEN), reason="Windows PowerShell/OpenSSH is unavailable")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_generated_windows_quickstart_verifies_before_execution(tmp_path, monkeypatch, powershell):
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    trusted = {release_key_id(public): public}
    monkeypatch.setattr(builder, "TRUSTED_RELEASE_KEYS", trusted)

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for platform, filename in sign_bootstrap.BOOTSTRAPS.items():
        source = subprocess.check_output(
            ["git", "show", f"{COMMIT}:deploy/bootstrap.{ 'ps1' if platform == 'windows' else 'sh' }"],
            cwd=ROOT,
        )
        (artifacts / filename).write_bytes(source)
    sign_bootstrap.sign_bootstrap_assets(
        artifacts,
        TAG,
        COMMIT,
        private_pem,
        trusted_keys=trusted,
        ssh_keygen=SSH_KEYGEN,
    )

    quickstart = builder.build_commands(COMMIT, TAG)
    windows = quickstart.split("Windows PowerShell:\n", 1)[1].split("\n\nmacOS Terminal:", 1)[0]
    windows = re.sub(
        r"iwr -UseBasicParsing -TimeoutSec 90 -Uri '[^']+/deploy/bootstrap\.ps1' -OutFile \$p;",
        lambda _match: f"Copy-Item -LiteralPath '{artifacts / sign_bootstrap.BOOTSTRAPS['windows']}' -Destination $p;",
        windows,
        count=1,
    )
    windows = re.sub(
        r"iwr -UseBasicParsing -TimeoutSec 90 -Uri '[^']+\.ps1\.sig' -OutFile \$s;",
        lambda _match: f"Copy-Item -LiteralPath '{artifacts / (sign_bootstrap.BOOTSTRAPS['windows'] + '.sig')}' -Destination $s;",
        windows,
        count=1,
    )
    windows = re.sub(r"& \$p -Ref '[0-9a-f]+'", "Write-Output 'VERIFIER_ACCEPTED'", windows, count=1)
    assert "Copy-Item" in windows and "VERIFIER_ACCEPTED" in windows

    script = tmp_path / "verify-quickstart.ps1"
    script.write_text(windows + "\n", encoding="utf-8")

    def execute_verifier():
        return subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    valid = execute_verifier()
    assert valid.returncode == 0, valid.stderr
    assert "VERIFIER_ACCEPTED" in valid.stdout

    bootstrap = artifacts / sign_bootstrap.BOOTSTRAPS["windows"]
    bootstrap.write_bytes(bootstrap.read_bytes() + b"# tampered\n")
    tampered = execute_verifier()
    assert tampered.returncode != 0
    assert "VERIFIER_ACCEPTED" not in tampered.stdout


def test_generated_macos_quickstart_verifies_before_execution(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    trusted = {release_key_id(public): public}
    monkeypatch.setattr(builder, "TRUSTED_RELEASE_KEYS", trusted)

    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        if not wsl:
            pytest.skip("WSL is unavailable for Bash/OpenSSH verification")

        # GitHub's Windows image includes wsl.exe, but may not have an
        # installed WSL distribution. Probe the actual tools before treating
        # the launcher stub as a runnable Linux environment.
        wsl_probe = subprocess.run(
            [wsl, "-e", "sh", "-lc", "command -v wslpath && command -v bash"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if wsl_probe.returncode != 0:
            pytest.skip("No usable WSL distribution with Bash/OpenSSH tools is installed")

        def to_shell_path(path):
            return subprocess.check_output(
                [wsl, "-e", "wslpath", "-a", str(path)], text=True, timeout=15
            ).strip()

    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("Bash is unavailable for macOS-command syntax verification")

        def to_shell_path(path):
            return str(path)

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / sign_bootstrap.BOOTSTRAPS["windows"]).write_bytes(b"windows test bootstrap\n")
    mac_bootstrap = artifacts / sign_bootstrap.BOOTSTRAPS["macos"]
    mac_bootstrap.write_bytes(b"#!/bin/sh\nprintf 'VERIFIER_ACCEPTED\\n'\n")
    sign_bootstrap.sign_bootstrap_assets(
        artifacts,
        TAG,
        COMMIT,
        private_pem,
        trusted_keys=trusted,
        ssh_keygen=SSH_KEYGEN,
    )

    quickstart = builder.build_commands(COMMIT, TAG)
    mac = quickstart.split("macOS Terminal:\n", 1)[1].split("\n\nBoth commands", 1)[0]
    mac = re.sub(
        r"curl -fsSL --max-time 90 '[^']+/deploy/bootstrap\.sh' -o \"\$p\";",
        lambda _match: f"cp '{to_shell_path(mac_bootstrap)}' \"$p\";",
        mac,
        count=1,
    )
    mac = re.sub(
        r"curl -fsSL --max-time 90 '[^']+\.sh\.sig' -o \"\$s\";",
        lambda _match: f"cp '{to_shell_path(artifacts / (sign_bootstrap.BOOTSTRAPS['macos'] + '.sig'))}' \"$s\";",
        mac,
        count=1,
    )
    mac = re.sub(r"XIDER_REF='[0-9a-f]+' bash \"\$p\"", "bash \"$p\"", mac, count=1)
    mac = (
        "base64(){ if [ \"$1\" = '-D' ]; then shift; command base64 -d \"$@\"; "
        "else command base64 \"$@\"; fi; }; "
        + mac
    )
    assert "curl -fsSL" not in mac and "XIDER_REF=" not in mac

    script = tmp_path / "verify-quickstart.sh"
    script.write_text(mac + "\n", encoding="utf-8", newline="\n")
    shell_script_path = to_shell_path(script)
    if os.name == "nt":
        command = [wsl, "bash", shell_script_path]
    else:
        command = [bash, shell_script_path]

    valid = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert valid.returncode == 0, valid.stderr
    assert "VERIFIER_ACCEPTED" in valid.stdout

    mac_bootstrap.write_bytes(mac_bootstrap.read_bytes() + b"# tampered\n")
    tampered = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert tampered.returncode != 0
    assert "VERIFIER_ACCEPTED" not in tampered.stdout
