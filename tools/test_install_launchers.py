import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import build_bootstrap_commands as bootstrap
from build_install_launchers import INSTALLERS, INSTALLER_NAMESPACE, build_installers, build_pages_launchers, write_files
from release_signature import release_key_id
from sign_installers import sign_installers, signed_installer_message


ROOT = Path(__file__).resolve().parents[1]
COMMIT = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
TAG = "v4.1.0"
SSH_KEYGEN = shutil.which("ssh-keygen.exe") or shutil.which("ssh-keygen")
POWERSHELLS = tuple(shell for shell in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if shell)


def test_release_installers_reuse_the_exact_commit_pinned_bootstrap_verifiers():
    bodies = bootstrap.build_platform_commands(COMMIT, TAG)
    installers = build_installers(COMMIT, TAG)
    for platform, name in INSTALLERS.items():
        assert bodies[platform] in installers[name]
        assert COMMIT in installers[name]
        assert TAG in installers[name]
    assert "/deploy/bootstrap-agent.ps1" in installers[INSTALLERS["windows"]]
    assert "/deploy/bootstrap.ps1" not in installers[INSTALLERS["windows"]]
    assert f"XIDER_RELEASE_TAG='{TAG}'" in installers[INSTALLERS["macos"]]


def test_all_client_bootstraps_default_to_the_current_xider_vps():
    expected_host = "16.16.200.207"
    powershell_defaults = []
    for filename, parameter in (("bootstrap.ps1", "ServerIp"), ("setup-all.ps1", "ServerIp"),
                                ("upload-and-install.ps1", "ServerIp"), ("bootstrap-agent.ps1", "ServerHost")):
        script = (ROOT / "deploy" / filename).read_text(encoding="utf-8-sig")
        match = re.search(rf"\[string\]\${parameter}\s*=\s*'([^']+)'", script)
        assert match is not None, f"Missing explicit {parameter} default in {filename}"
        powershell_defaults.append(match.group(1))
    shell_script = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")
    shell_default = re.search(r'XIDER_SERVER_HOST:-([^}\"]+)', shell_script)
    assert shell_default is not None
    assert powershell_defaults == [expected_host] * len(powershell_defaults)
    assert shell_default.group(1) == expected_host


def test_pages_launchers_authenticate_latest_installer_before_running_it():
    pages = build_pages_launchers()
    assert set(pages) == {"mac", "win.ps1", "win.txt", ".nojekyll"}
    assert pages["win.txt"] == pages["win.ps1"]
    for platform, filename in (("macos", "mac"), ("windows", "win.ps1")):
        script = pages[filename]
        assert f"releases/latest/download/{INSTALLERS[platform]}" in script
        assert f"releases/latest/download/{INSTALLERS[platform]}.sig" in script
        assert "XIDER-INSTALLER-SHA256" in script
        assert INSTALLER_NAMESPACE in script
    assert pages["mac"].index("ssh-keygen -Y verify") < pages["mac"].index('bash "$p"')
    assert pages["win.ps1"].index("$v.ExitCode -ne 0") < pages["win.ps1"].index("$installer=Start-Process")
    assert "-Verb RunAs" in pages["win.ps1"]
    assert "-Wait -PassThru" in pages["win.ps1"]
    assert "XIDER Windows installer failed" in pages["win.ps1"]
    assert "requesting administrator approval (UAC)" in pages["win.ps1"]
    assert "OpenSSH.Client~~~~0.0.1.0" in pages["win.ps1"]
    assert "OpenSSH Client is still unavailable" in pages["win.ps1"]
    assert "Read-Host" not in pages["win.ps1"]
    assert "Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru" in pages["win.ps1"]
    assert "System32\\OpenSSH\\ssh-keygen.exe" in pages["win.ps1"]
    assert pages["win.ps1"].index("Add-WindowsCapability") < pages["win.ps1"].index("releases/latest/download")
    assert "XIDER launcher failed:" in pages["win.ps1"]
    assert "Set-ExecutionPolicy" not in pages["win.ps1"]


def test_installer_message_is_platform_bound_and_exact_ascii():
    digest = "a" * 64
    assert signed_installer_message("macos", digest) == f"XIDER-INSTALLER-SHA256\nmacos\n{digest}\n".encode("ascii")
    assert signed_installer_message("windows", digest) != signed_installer_message("macos", digest)


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "../" + "a" * 61, "g" * 64])
def test_installer_message_rejects_invalid_digest(digest):
    with pytest.raises(ValueError, match="SHA-256"):
        signed_installer_message("macos", digest)


def test_installer_message_rejects_unknown_platform():
    with pytest.raises(ValueError, match="platform"):
        signed_installer_message("ios", "a" * 64)


def make_signing_fixture(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    trusted = {release_key_id(public): public}
    monkeypatch.setattr(bootstrap, "TRUSTED_RELEASE_KEYS", trusted)
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / INSTALLERS["macos"]).write_text("#!/bin/bash\nprintf 'INSTALLER_ACCEPTED\\n'\n", encoding="utf-8", newline="\n")
    (root / INSTALLERS["windows"]).write_text("Write-Output 'INSTALLER_ACCEPTED'\n", encoding="utf-8", newline="\n")
    return root, private_pem, trusted


def test_signer_requires_a_pinned_key_before_writing_any_signatures(tmp_path, monkeypatch):
    root, private, _trusted = make_signing_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="pinned"):
        sign_installers(root, private, trusted_keys={})
    assert not list(root.glob("*.sig"))


@pytest.mark.skipif(not SSH_KEYGEN, reason="OpenSSH is unavailable")
def test_signer_rejects_missing_input_and_existing_signature(tmp_path, monkeypatch):
    root, private, trusted = make_signing_fixture(tmp_path, monkeypatch)
    mac = root / INSTALLERS["macos"]
    mac.unlink()
    with pytest.raises(FileNotFoundError):
        sign_installers(root, private, trusted_keys=trusted)
    assert not list(root.glob("*.sig"))
    mac.write_bytes(b"test\n")
    (root / f"{INSTALLERS['macos']}.sig").write_bytes(b"keep\n")
    with pytest.raises(FileExistsError):
        sign_installers(root, private, trusted_keys=trusted)
    assert (root / f"{INSTALLERS['macos']}.sig").read_bytes() == b"keep\n"


def test_written_pages_entrypoints_have_exact_names_and_lf(tmp_path):
    outputs = write_files(tmp_path, build_pages_launchers())
    assert {path.name for path in outputs} == {"mac", "win.ps1", "win.txt", ".nojekyll"}
    assert all(b"\r\n" not in path.read_bytes() for path in outputs)


def test_pages_only_cli_needs_no_commit_or_tag_and_preserves_existing_docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    existing = docs / "README.md"
    existing.write_bytes(b"Existing project documentation\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_pages_entrypoints.py"), str(docs)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert existing.read_bytes() == b"Existing project documentation\n"
    for name, contents in build_pages_launchers().items():
        assert (docs / name).read_text(encoding="utf-8") == contents


@pytest.mark.skipif(not (POWERSHELLS and SSH_KEYGEN), reason="Windows PowerShell/OpenSSH is unavailable")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_short_windows_launcher_accepts_signed_installer_and_rejects_tampering(tmp_path, monkeypatch, powershell):
    root, private, trusted = make_signing_fixture(tmp_path, monkeypatch)
    sign_installers(root, private, trusted_keys=trusted, ssh_keygen=SSH_KEYGEN)
    script = build_pages_launchers()["win.ps1"]
    for suffix, variable in (("", "$p"), (".sig", "$s")):
        source = root / f"{INSTALLERS['windows']}{suffix}"
        pattern = rf"iwr -UseBasicParsing -TimeoutSec 90 -Uri '[^']+/{re.escape(INSTALLERS['windows'] + suffix)}' -OutFile {re.escape(variable)}"
        script, count = re.subn(pattern, lambda _match: f"Copy-Item -LiteralPath '{source}' -Destination {variable}", script, count=1)
        assert count == 1
    # Keep this isolated signature-verification fixture noninteractive. The
    # production launcher uses RunAs; the test executes only the signed fixture.
    script, count = re.subn(
        r"\s*\$installer=Start-Process -FilePath 'powershell\.exe' -Verb RunAs -Wait -PassThru -ArgumentList \$args",
        "\n  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File ($args[4].Trim('\"')); $installer=[pscustomobject]@{ExitCode=$LASTEXITCODE}",
        script,
        count=1,
    )
    assert count == 1
    path = tmp_path / "short.ps1"
    path.write_text(script, encoding="utf-8", newline="\n")
    command = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Restricted", "-Command", f"Get-Content -LiteralPath '{path}' -Raw | Invoke-Expression"]
    valid = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert valid.returncode == 0, valid.stderr
    assert "INSTALLER_ACCEPTED" in valid.stdout
    installer = root / INSTALLERS["windows"]
    installer.write_bytes(installer.read_bytes() + b"# tampered\n")
    invalid = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert invalid.returncode != 0
    assert "INSTALLER_ACCEPTED" not in invalid.stdout


@pytest.mark.skipif(not SSH_KEYGEN, reason="OpenSSH is unavailable")
def test_short_macos_launcher_accepts_signed_installer_and_rejects_tampering(tmp_path, monkeypatch):
    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        if not wsl:
            pytest.skip("WSL is unavailable")
        probe = subprocess.run([wsl, "-e", "sh", "-lc", "command -v wslpath && command -v bash"], capture_output=True, text=True, timeout=15)
        if probe.returncode:
            pytest.skip("No usable WSL distribution is installed")
        def shell_path(path):
            return subprocess.check_output([wsl, "-e", "wslpath", "-a", str(path)], text=True, timeout=15).strip()
        shell = [wsl, "bash"]
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("Bash is unavailable")
        shell = [bash]
        def shell_path(path):
            return str(path)
    root, private, trusted = make_signing_fixture(tmp_path, monkeypatch)
    sign_installers(root, private, trusted_keys=trusted, ssh_keygen=SSH_KEYGEN)
    script = build_pages_launchers()["mac"]
    for suffix, variable in (("", "$p"), (".sig", "$s")):
        source = root / f"{INSTALLERS['macos']}{suffix}"
        pattern = rf"curl -fsSL --max-time 90 '[^']+/{re.escape(INSTALLERS['macos'] + suffix)}' -o \"{re.escape(variable)}\""
        script, count = re.subn(pattern, lambda _match: f"cp '{shell_path(source)}' \"{variable}\"", script, count=1)
        assert count == 1
    # Linux uses -d; macOS's launcher correctly uses -D.
    script = "base64(){ if [ \"$1\" = '-D' ]; then shift; command base64 -d \"$@\"; else command base64 \"$@\"; fi; };\n" + script
    path = tmp_path / "short.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    command = shell + [shell_path(path)]
    valid = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert valid.returncode == 0, valid.stderr
    assert "INSTALLER_ACCEPTED" in valid.stdout
    installer = root / INSTALLERS["macos"]
    installer.write_bytes(installer.read_bytes() + b"# tampered\n")
    invalid = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert invalid.returncode != 0
    assert "INSTALLER_ACCEPTED" not in invalid.stdout
