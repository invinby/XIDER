#!/usr/bin/env python3
"""Create a dedicated XIDER Ed25519 release key outside the source checkout.

The private key is never printed. The command emits only the public key ID and
raw public key, which can be reviewed and pinned in release_signature.py.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
RELEASE_MODULE_DIR = ROOT / "XGENT-MCS"
if str(RELEASE_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_MODULE_DIR))


def _secure_new_directory(path: Path) -> Path:
    path = Path(path).expanduser()
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("Keep release signing material outside the repository checkout.")
    if path.exists() or path.is_symlink():
        raise FileExistsError("Output directory already exists; refusing to reuse or overwrite it.")
    if not path.parent.is_dir():
        raise FileNotFoundError("Create the parent directory first; the final key directory must be new.")

    path.mkdir(mode=0o700)
    try:
        if os.name == "nt":
            domain = os.environ.get("USERDOMAIN", "").strip()
            username = os.environ.get("USERNAME", "").strip()
            if not username:
                raise RuntimeError("Cannot identify the current Windows account for key ACLs.")
            identity = f"{domain}\\{username}" if domain else username
            result = subprocess.run(
                [
                    "icacls.exe", str(path), "/inheritance:r", "/grant:r",
                    f"{identity}:(OI)(CI)F", "/Q",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError("Could not restrict the new release-key directory ACL.")
        else:
            path.chmod(0o700)
        return path.resolve()
    except Exception:
        try:
            path.rmdir()
        except OSError:
            pass
        raise


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def create_release_key(directory: Path) -> dict[str, str]:
    """Create a protected, one-time key directory and return public metadata."""
    output_dir = _secure_new_directory(Path(directory))
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    from release_signature import release_key_id

    files = {
        output_dir / "release-signing-key.pem": private_pem,
        output_dir / "release-signing-key.b64": base64.b64encode(private_pem),
    }
    created: list[Path] = []
    try:
        for path, content in files.items():
            _write_private_file(path, content)
            created.append(path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise

    return {
        "directory": str(output_dir),
        "private_pem_path": str(output_dir / "release-signing-key.pem"),
        "private_base64_path": str(output_dir / "release-signing-key.b64"),
        "key_id": release_key_id(public_raw),
        "public_key_hex": public_raw.hex(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory", required=True, type=Path,
        help="A new directory outside the checkout, with an existing parent.",
    )
    args = parser.parse_args(argv)
    try:
        info = create_release_key(args.directory)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print("Dedicated XIDER release-signing key created in a protected directory.")
    print(f"key_id={info['key_id']}")
    print(f"public_key_hex={info['public_key_hex']}")
    print(f"private_key_file={info['private_pem_path']}")
    print(f"GitHub_secret_input_file={info['private_base64_path']}")
    print("Private key material was not printed. Keep both private files outside this repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
