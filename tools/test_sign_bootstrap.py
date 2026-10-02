import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import sign_bootstrap
from release_signature import release_key_id


SSH_KEYGEN = shutil.which("ssh-keygen") or shutil.which("ssh-keygen.exe")


def _key_pair():
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
    return private_pem, {release_key_id(public): public}


@pytest.mark.skipif(not SSH_KEYGEN, reason="OpenSSH ssh-keygen is unavailable")
def test_bootstrap_signatures_cover_exact_digest_platform_tag_and_commit(tmp_path, monkeypatch):
    private_pem, trusted = _key_pair()
    payloads = {
        "windows": b"Write-Output 'signed bootstrap'\r\n",
        "macos": b"#!/bin/bash\nprintf 'signed bootstrap\\n'\n",
    }
    for platform, name in sign_bootstrap.BOOTSTRAPS.items():
        (tmp_path / name).write_bytes(payloads[platform])

    if os.name == "nt":
        # Reproduce hosted Windows profiles whose temp directory contributes
        # an OWNER RIGHTS ACE; OpenSSH refuses that ACL unless the signer
        # removes the extra ACE before writing key material.
        restrict_private_file = sign_bootstrap._restrict_private_file

        def restrict_after_owner_rights_ace(path):
            added = subprocess.run(
                ["icacls.exe", str(path), "/grant", "*S-1-3-4:(R)", "/Q"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            assert added.returncode == 0, added.stderr or added.stdout
            restrict_private_file(path)

        monkeypatch.setattr(
            sign_bootstrap, "_restrict_private_file", restrict_after_owner_rights_ace
        )

    signatures = sign_bootstrap.sign_bootstrap_assets(
        tmp_path,
        "v4.1.0",
        "a" * 40,
        private_pem,
        trusted_keys=trusted,
        ssh_keygen=SSH_KEYGEN,
    )

    assert {path.name for path in signatures} == {
        "XIDER-bootstrap-windows.ps1.sig",
        "XIDER-bootstrap-macos.sh.sig",
    }
    public = next(iter(trusted.values()))
    allowed_signers = tmp_path / "allowed_signers.test"
    allowed_signers.write_text(
        f"{sign_bootstrap.SIGNER_IDENTITY} {sign_bootstrap._openssh_public_key_line(public)}\n",
        encoding="ascii",
    )
    for platform, name in sign_bootstrap.BOOTSTRAPS.items():
        digest = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        message = sign_bootstrap.signed_hash_message(platform, "v4.1.0", "a" * 40, digest)
        result = subprocess.run(
            [
                SSH_KEYGEN,
                "-Y", "verify",
                "-f", str(allowed_signers),
                "-I", sign_bootstrap.SIGNER_IDENTITY,
                "-n", sign_bootstrap.NAMESPACE,
                "-s", str(tmp_path / f"{name}.sig"),
            ],
            input=message,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")

        tampered = sign_bootstrap.signed_hash_message(
            platform, "v4.1.0", "b" * 40, digest
        )
        rejected = subprocess.run(
            [
                SSH_KEYGEN,
                "-Y", "verify",
                "-f", str(allowed_signers),
                "-I", sign_bootstrap.SIGNER_IDENTITY,
                "-n", sign_bootstrap.NAMESPACE,
                "-s", str(tmp_path / f"{name}.sig"),
            ],
            input=tampered,
            capture_output=True,
            check=False,
        )
        assert rejected.returncode != 0


def test_bootstrap_signer_rejects_an_unpinned_key(tmp_path):
    private_pem, _trusted = _key_pair()
    for name in sign_bootstrap.BOOTSTRAPS.values():
        (tmp_path / name).write_bytes(b"bootstrap")

    with pytest.raises(ValueError, match="does not match"):
        sign_bootstrap.sign_bootstrap_assets(
            tmp_path,
            "v4.1.0",
            "a" * 40,
            private_pem,
            trusted_keys={},
            ssh_keygen=SSH_KEYGEN,
        )


@pytest.mark.parametrize("tag,commit", [("main", "a" * 40), ("v1.2.3", "a" * 39)])
def test_bootstrap_signature_message_rejects_unpinned_release_identity(tag, commit):
    with pytest.raises(ValueError):
        sign_bootstrap.signed_hash_message("macos", tag, commit, "0" * 64)


def test_windows_signed_hash_message_uses_encoding_independent_ascii_bytes():
    message = sign_bootstrap.signed_hash_message("windows", "v4.1.0", "A" * 40, "f" * 64)
    assert message.startswith(b"XIDER-BOOTSTRAP-SHA256\nwindows\n")
    assert message.endswith(b"\n" + b"f" * 64 + b"\n")


def test_macos_signed_hash_message_has_no_bom():
    message = sign_bootstrap.signed_hash_message("macos", "v4.1.0", "A" * 40, "f" * 64)
    assert message.startswith(b"XIDER-BOOTSTRAP-SHA256\nmacos\n")
