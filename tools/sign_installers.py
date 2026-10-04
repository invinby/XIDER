"""Sign standalone installer hashes for the short pinned-key launchers."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from build_install_launchers import INSTALLERS, INSTALLER_NAMESPACE
from sign_bootstrap import SIGNER_IDENTITY, _openssh_public_key_line, _write_exclusive
from release_signature import TRUSTED_RELEASE_KEYS, release_key_id


def signed_installer_message(platform: str, digest: str) -> bytes:
    if platform not in INSTALLERS:
        raise ValueError("unsupported installer platform")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("installer SHA-256 must be lowercase hexadecimal")
    return f"XIDER-INSTALLER-SHA256\n{platform}\n{digest}\n".encode("ascii")


def sign_installers(
    directory: Path,
    signing_key_pem: bytes,
    *,
    trusted_keys: dict[str, bytes] | None = None,
    ssh_keygen: str | None = None,
) -> list[Path]:
    private = serialization.load_pem_private_key(signing_key_pem, password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise ValueError("Release signing key must be Ed25519.")
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    keys = TRUSTED_RELEASE_KEYS if trusted_keys is None else trusted_keys
    if keys.get(release_key_id(public)) != public:
        raise ValueError("Signing key does not match a pinned publisher key.")
    ssh_keygen = ssh_keygen or shutil.which("ssh-keygen") or shutil.which("ssh-keygen.exe")
    if not ssh_keygen:
        raise RuntimeError("OpenSSH ssh-keygen is required to sign installers.")
    directory = Path(directory)
    # Validate every input/output before making any signature files.
    for filename in INSTALLERS.values():
        if not (directory / filename).is_file():
            raise FileNotFoundError(directory / filename)
        if (directory / f"{filename}.sig").exists():
            raise FileExistsError(directory / f"{filename}.sig")
    outputs = []
    with tempfile.TemporaryDirectory(prefix="xider-installer-sign-") as temp:
        root = Path(temp)
        key_path = root / "key"
        allowed = root / "allowed_signers"
        _write_exclusive(key_path, private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        ), private=True)
        _write_exclusive(allowed, f"{SIGNER_IDENTITY} {_openssh_public_key_line(public)}\n".encode("ascii"))
        for platform, filename in INSTALLERS.items():
            digest = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
            message = signed_installer_message(platform, digest)
            path = root / platform
            _write_exclusive(path, message)
            signed = subprocess.run(
                [ssh_keygen, "-Y", "sign", "-f", str(key_path), "-n", INSTALLER_NAMESPACE, str(path)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            signature = Path(f"{path}.sig")
            if signed.returncode != 0 or not signature.is_file():
                detail = " ".join((signed.stderr or signed.stdout or "").split())[:500]
                raise RuntimeError(f"OpenSSH installer signing failed (exit {signed.returncode}): {detail}")
            verified = subprocess.run(
                [ssh_keygen, "-Y", "verify", "-f", str(allowed), "-I", SIGNER_IDENTITY,
                 "-n", INSTALLER_NAMESPACE, "-s", str(signature)],
                input=message, capture_output=True, timeout=30, check=False,
            )
            if verified.returncode != 0:
                raise RuntimeError("OpenSSH installer signature self-verification failed.")
            destination = directory / f"{filename}.sig"
            _write_exclusive(destination, signature.read_bytes())
            outputs.append(destination)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    encoded_key = os.environ.get("XIDER_RELEASE_PRIVATE_KEY_B64", "")
    if not encoded_key:
        parser.error("XIDER_RELEASE_PRIVATE_KEY_B64 is required for signed installers.")
    try:
        outputs = sign_installers(args.directory, base64.b64decode(encoded_key, validate=True))
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
