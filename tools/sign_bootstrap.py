"""Create OpenSSH signatures for the release's pinned bootstrap hashes.

The signatures use the same Ed25519 publisher key as the signed release
manifest. The private key is loaded from XIDER_RELEASE_PRIVATE_KEY_B64 and is
kept only in a restricted temporary file for the duration of ssh-keygen.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


TAG = re.compile(r"^v\d+\.\d+\.\d+$")
COMMIT_ID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
NAMESPACE = "xider-bootstrap@xider.link"
SIGNER_IDENTITY = "xider-release"
BOOTSTRAPS = {
    "windows": "XIDER-bootstrap-windows.ps1",
    "macos": "XIDER-bootstrap-macos.sh",
}
ROOT = Path(__file__).resolve().parents[1]
RELEASE_MODULE_DIR = ROOT / "XGENT-MCS"
if str(RELEASE_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_MODULE_DIR))

from release_signature import TRUSTED_RELEASE_KEYS, release_key_id  # noqa: E402


def signed_hash_message(platform: str, tag: str, commit_id: str, digest: str) -> bytes:
    """Return the exact platform-specific bytes verified by the quickstart."""
    if platform not in BOOTSTRAPS:
        raise ValueError("unsupported bootstrap platform")
    if not TAG.fullmatch(tag):
        raise ValueError("tag must be vMAJOR.MINOR.PATCH")
    if not COMMIT_ID.fullmatch(commit_id):
        raise ValueError("a full 40- or 64-character Git commit ID is required")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("bootstrap SHA-256 must be lowercase hexadecimal")

    body = (
        f"XIDER-BOOTSTRAP-SHA256\n{platform}\n{tag}\n"
        f"{commit_id.lower()}\n{digest}\n"
    ).encode("ascii")
    # Windows PowerShell 5.1's redirected StandardInput emits UTF-8's BOM.
    # The quickstart explicitly sets UTF-8-with-BOM in newer PowerShell too.
    return b"\xef\xbb\xbf" + body if platform == "windows" else body


def _openssh_public_key_line(public_key: bytes) -> str:
    key_type = b"ssh-ed25519"
    wire_key = (
        struct.pack(">I", len(key_type))
        + key_type
        + struct.pack(">I", len(public_key))
        + public_key
    )
    return f"ssh-ed25519 {base64.b64encode(wire_key).decode('ascii')}"


def _restrict_private_file(path: Path) -> None:
    if os.name == "nt":
        username = os.environ.get("USERNAME", "").strip()
        domain = os.environ.get("USERDOMAIN", "").strip()
        if not username:
            raise RuntimeError("Cannot identify the Windows account for the temporary signing-key ACL.")
        identity = f"{domain}\\{username}" if domain else username
        result = subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"{identity}:(R)", "/Q"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not restrict the temporary bootstrap-signing key ACL.")
    else:
        path.chmod(0o600)


def _write_exclusive(path: Path, content: bytes, *, private: bool = False) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
    try:
        # On Windows, restrict the new file before the private-key bytes hit
        # disk. POSIX gets 0600 atomically from os.open above.
        if private:
            _restrict_private_file(path)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def sign_bootstrap_assets(
    artifacts_dir: Path,
    tag: str,
    commit_id: str,
    signing_key_pem: bytes,
    *,
    trusted_keys: dict[str, bytes] | None = None,
    ssh_keygen: str | None = None,
) -> list[Path]:
    """Sign and self-verify both bootstrap hashes; refuse unpinned keys."""
    if not TAG.fullmatch(tag):
        raise ValueError("tag must be vMAJOR.MINOR.PATCH")
    if not isinstance(commit_id, str) or not COMMIT_ID.fullmatch(commit_id):
        raise ValueError("a full 40- or 64-character Git commit ID is required")
    commit_id = commit_id.lower()
    artifact_root = Path(artifacts_dir)
    if not artifact_root.is_dir():
        raise FileNotFoundError(artifact_root)

    try:
        private_key = serialization.load_pem_private_key(signing_key_pem, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("Release signing key is not an unencrypted PEM private key.") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Release signing key must be Ed25519.")
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_id = release_key_id(public_bytes)
    keyring = TRUSTED_RELEASE_KEYS if trusted_keys is None else trusted_keys
    if keyring.get(key_id) != public_bytes:
        raise ValueError("Signing key does not match a public key pinned in release_signature.py.")
    public_line = _openssh_public_key_line(public_bytes)

    ssh_keygen = ssh_keygen or shutil.which("ssh-keygen") or shutil.which("ssh-keygen.exe")
    if not ssh_keygen:
        raise RuntimeError("OpenSSH ssh-keygen is required to sign bootstrap hashes.")

    open_ssh_private = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    created: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="xider-bootstrap-sign-") as folder:
        temp_root = Path(folder)
        private_path = temp_root / "release-signing-key"
        allowed_signers_path = temp_root / "allowed_signers"
        _write_exclusive(private_path, open_ssh_private, private=True)
        _write_exclusive(
            allowed_signers_path,
            f"{SIGNER_IDENTITY} {public_line}\n".encode("ascii"),
        )

        for platform, filename in BOOTSTRAPS.items():
            bootstrap_path = artifact_root / filename
            if not bootstrap_path.is_file():
                raise FileNotFoundError(bootstrap_path)
            signature_path = artifact_root / f"{filename}.sig"
            if signature_path.exists():
                raise FileExistsError(signature_path)

            digest = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
            message = signed_hash_message(platform, tag, commit_id, digest)
            message_path = temp_root / f"{platform}.message"
            _write_exclusive(message_path, message)
            sign_result = subprocess.run(
                [
                    ssh_keygen,
                    "-Y", "sign",
                    "-f", str(private_path),
                    "-n", NAMESPACE,
                    str(message_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            temp_signature = Path(f"{message_path}.sig")
            if sign_result.returncode != 0 or not temp_signature.is_file():
                # Keep the actionable OpenSSH diagnostic. In particular,
                # Windows runners can reject the temporary key ACL, and
                # suppressing stderr turns that into an opaque CI failure.
                detail = (sign_result.stderr or sign_result.stdout or "").strip()
                detail = " ".join(detail.split())[:500]
                suffix = f" OpenSSH: {detail}" if detail else ""
                raise RuntimeError(
                    "OpenSSH could not sign the bootstrap hash message "
                    f"(exit {sign_result.returncode}).{suffix}"
                )

            verify_result = subprocess.run(
                [
                    ssh_keygen,
                    "-Y", "verify",
                    "-f", str(allowed_signers_path),
                    "-I", SIGNER_IDENTITY,
                    "-n", NAMESPACE,
                    "-s", str(temp_signature),
                ],
                input=message,
                capture_output=True,
                check=False,
                timeout=30,
            )
            if verify_result.returncode != 0:
                raise RuntimeError("OpenSSH self-verification of a bootstrap signature failed.")

            _write_exclusive(signature_path, temp_signature.read_bytes())
            created.append(signature_path)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("commit_id")
    parser.add_argument("artifacts_dir", type=Path)
    args = parser.parse_args(argv)
    encoded_key = os.environ.get("XIDER_RELEASE_PRIVATE_KEY_B64", "")
    if not encoded_key:
        parser.error("XIDER_RELEASE_PRIVATE_KEY_B64 is required for signed bootstraps.")
    try:
        key_pem = base64.b64decode(encoded_key, validate=True)
        outputs = sign_bootstrap_assets(args.artifacts_dir, args.tag, args.commit_id, key_pem)
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
