#!/usr/bin/env python3
"""X-VAULT: authenticated encrypted backups with safe staging restores."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAGIC = b"XVAULT1\0"
MAGIC_RECIPIENT = b"XVAULT2\0"
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
EPHEMERAL_PUBLIC_KEY_BYTES = 32
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
PASSPHRASE_ENV = "XIDER_VAULT_PASSPHRASE"
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "incoming"}
ROOT = Path(__file__).resolve().parents[1]


class VaultError(Exception):
    """An expected, safe-to-display backup/restore error."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise VaultError(f"Set a non-empty {PASSPHRASE_ENV} environment variable.")
    if len(passphrase) < 16:
        raise VaultError("Vault passphrase must be at least 16 characters.")
    return Scrypt(salt=salt, length=KEY_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def _passphrase() -> str:
    value = os.environ.get(PASSPHRASE_ENV, "")
    if not value:
        raise VaultError(f"Set {PASSPHRASE_ENV} in the environment; do not pass it as a command-line argument.")
    return value


def _passphrase_from_stdin() -> str:
    value = sys.stdin.readline()
    if value == "":
        raise VaultError("No passphrase was provided on standard input.")
    return value.rstrip("\r\n")


def _load_key_file(path: Path) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise VaultError("X-VAULT key must be an existing regular file, not a symlink.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise VaultError("Cannot read the X-VAULT key file.") from exc


def _load_recipient_public_key(path: Path) -> X25519PublicKey:
    try:
        key = serialization.load_pem_public_key(_load_key_file(path))
    except (ValueError, TypeError) as exc:
        raise VaultError("Invalid X-VAULT recipient public key.") from exc
    if not isinstance(key, X25519PublicKey):
        raise VaultError("X-VAULT recipient key must use X25519.")
    return key


def _load_recipient_private_key(path: Path) -> X25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(_load_key_file(path), password=None)
    except (ValueError, TypeError) as exc:
        raise VaultError("Invalid or encrypted X-VAULT recipient private key.") from exc
    if not isinstance(key, X25519PrivateKey):
        raise VaultError("X-VAULT recovery key must use X25519.")
    return key


def _recipient_aead_key(shared_secret: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=SHA256(),
        length=KEY_BYTES,
        salt=salt,
        info=b"X-VAULT/2 recipient encryption",
    ).derive(shared_secret)


def _encrypt_for_recipient(plaintext: bytes, recipient: X25519PublicKey) -> bytes:
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC_RECIPIENT + ephemeral_public + salt + nonce
    key = _recipient_aead_key(ephemeral.exchange(recipient), salt)
    return header + AESGCM(key).encrypt(nonce, plaintext, header)


def _decrypt_for_recipient(blob: bytes, recipient: X25519PrivateKey) -> bytes:
    header_size = len(MAGIC_RECIPIENT) + EPHEMERAL_PUBLIC_KEY_BYTES + SALT_BYTES + NONCE_BYTES
    if len(blob) <= header_size or not blob.startswith(MAGIC_RECIPIENT):
        raise VaultError("Not a supported X-VAULT recipient archive or archive is truncated.")
    public_start = len(MAGIC_RECIPIENT)
    salt_start = public_start + EPHEMERAL_PUBLIC_KEY_BYTES
    nonce_start = salt_start + SALT_BYTES
    try:
        ephemeral = X25519PublicKey.from_public_bytes(blob[public_start:salt_start])
        salt = blob[salt_start:nonce_start]
        nonce = blob[nonce_start:header_size]
        header = blob[:header_size]
        key = _recipient_aead_key(recipient.exchange(ephemeral), salt)
        return AESGCM(key).decrypt(nonce, blob[header_size:], header)
    except (ValueError, InvalidTag) as exc:
        raise VaultError("Authentication failed: wrong recovery key or damaged archive.") from exc


def _write_new_file(path: Path, content: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def create_recipient_keypair(directory: Path) -> dict[str, str]:
    """Create a new offline recovery key pair in a protected, new directory."""
    directory = Path(directory).expanduser()
    resolved = directory.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise VaultError("Keep X-VAULT private recovery keys outside the repository checkout.")
    if directory.exists() or directory.is_symlink():
        raise FileExistsError("X-VAULT key directory must not already exist.")
    if not directory.parent.is_dir():
        raise FileNotFoundError("Create the parent directory before generating X-VAULT keys.")

    directory.mkdir(mode=0o700)
    try:
        if os.name == "nt":
            domain = os.environ.get("USERDOMAIN", "").strip()
            username = os.environ.get("USERNAME", "").strip()
            if not username:
                raise RuntimeError("Cannot identify the current Windows account for X-VAULT key ACLs.")
            identity = f"{domain}\\{username}" if domain else username
            result = subprocess.run(
                ["icacls.exe", str(directory), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F", "/Q"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError("Could not restrict the new X-VAULT key directory ACL.")
        else:
            directory.chmod(0o700)

        private_key = X25519PrivateKey.generate()
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        private_path = directory / "vault-recipient-private.pem"
        public_path = directory / "vault-recipient-public.pem"
        _write_new_file(private_path, private_pem, 0o600)
        try:
            _write_new_file(public_path, public_pem, 0o644)
        except Exception:
            private_path.unlink(missing_ok=True)
            raise
        fingerprint = hashlib.sha256(public_raw).hexdigest()[:24]
        return {
            "directory": str(directory.resolve()),
            "private_key_path": str(private_path.resolve()),
            "public_key_path": str(public_path.resolve()),
            "fingerprint": fingerprint,
        }
    except Exception:
        for name in ("vault-recipient-private.pem", "vault-recipient-public.pem"):
            (directory / name).unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    normalized_name = name[:-1] if name.endswith("/") else name
    if (
        path.is_absolute()
        or not path.parts
        or str(path) != normalized_name
        or any(part in ("", ".", "..") or ":" in part or "\x00" in part for part in path.parts)
    ):
        raise VaultError(f"Unsafe backup member: {name!r}")
    if "\\" in name:
        raise VaultError(f"Unsafe backup member: {name!r}")
    return path


def _read_stable(path: Path) -> bytes:
    for _attempt in range(3):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if before.st_size == after.st_size == len(data) and before.st_mtime_ns == after.st_mtime_ns:
            return data
    raise VaultError(f"File changed during backup; retry when it is idle: {path.name}")


def _build_archive(
    source: Path,
    secret_files: tuple[Path, ...] = (),
    *,
    recipient_encrypted: bool = False,
) -> bytes:
    if source.is_symlink() or not source.is_dir():
        raise VaultError("Backup source must be an existing directory.")
    output = io.BytesIO()
    manifest = {
        "format": "X-VAULT/2" if recipient_encrypted else "X-VAULT/1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_name": source.name,
        "encryption": (
            "X25519 + HKDF-SHA256 + AES-256-GCM"
            if recipient_encrypted
            else "AES-256-GCM with Scrypt key derivation"
        ),
        "excluded_directories": sorted(SKIP_DIRS),
        "external_files": [],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        names = {"manifest.json"}
        portable_names = {"manifest.json".casefold()}
        for current, dirs, files in os.walk(source, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(
                name for name in dirs
                if name not in SKIP_DIRS and not (current_path / name).is_symlink()
            )
            for filename in sorted(files):
                path = current_path / filename
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                _safe_member(relative)
                if relative in names:
                    raise VaultError(f"Archive path collision: {relative}")
                if relative.casefold() in portable_names:
                    raise VaultError(f"Case-insensitive archive path collision: {relative}")
                names.add(relative)
                portable_names.add(relative.casefold())
                archive.writestr(relative, _read_stable(path))
        for extra_path in secret_files:
            if extra_path.is_symlink() or not extra_path.is_file():
                raise VaultError(f"Included file must be a regular file: {extra_path}")
            member = f"external/{extra_path.name}"
            _safe_member(member)
            if member in names:
                raise VaultError(f"Two included files have the same filename: {extra_path.name}")
            if member.casefold() in portable_names:
                raise VaultError(f"Case-insensitive archive path collision: {member}")
            names.add(member)
            portable_names.add(member.casefold())
            archive.writestr(member, _read_stable(extra_path))
            manifest["external_files"].append(member)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return output.getvalue()


def _encrypt(plaintext: bytes, passphrase: str) -> bytes:
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC + salt + nonce
    encrypted = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, plaintext, header)
    return header + encrypted


def _decrypt(
    blob: bytes,
    passphrase: str | None = None,
    recipient_private_key: X25519PrivateKey | None = None,
) -> tuple[bytes, int]:
    if blob.startswith(MAGIC_RECIPIENT):
        if recipient_private_key is None:
            raise VaultError("This archive needs its offline recovery private key; provide --private-key.")
        return _decrypt_for_recipient(blob, recipient_private_key), 2
    header_size = len(MAGIC) + SALT_BYTES + NONCE_BYTES
    if len(blob) <= header_size or not blob.startswith(MAGIC):
        raise VaultError("Not a supported X-VAULT archive or archive is truncated.")
    if passphrase is None:
        raise VaultError("This archive needs its original passphrase.")
    salt_start = len(MAGIC)
    salt = blob[salt_start : salt_start + SALT_BYTES]
    nonce_start = salt_start + SALT_BYTES
    nonce = blob[nonce_start : nonce_start + NONCE_BYTES]
    header = blob[:header_size]
    try:
        return AESGCM(_derive_key(passphrase, salt)).decrypt(nonce, blob[header_size:], header), 1
    except InvalidTag as exc:
        raise VaultError("Authentication failed: wrong passphrase or damaged archive.") from exc


def _read_archive(
    path: Path,
    passphrase: str | None = None,
    recipient_private_key_path: Path | None = None,
) -> tuple[zipfile.ZipFile, io.BytesIO]:
    try:
        recipient_private_key = (
            _load_recipient_private_key(recipient_private_key_path)
            if recipient_private_key_path is not None
            else None
        )
        payload, encryption_version = _decrypt(
            path.read_bytes(), passphrase, recipient_private_key,
        )
        buffer = io.BytesIO(payload)
        archive = zipfile.ZipFile(buffer, "r")
        names = archive.namelist()
        seen: dict[str, bool] = {}
        files: set[str] = set()
        for info in archive.infolist():
            _safe_member(info.filename)
            key = info.filename.rstrip("/").casefold()
            if key in seen:
                raise VaultError("Archive contains duplicate or case-colliding paths.")
            seen[key] = info.is_dir()
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise VaultError("Symbolic links are not permitted in a restore archive.")
            if not info.is_dir():
                files.add(key)
        for key, is_dir in seen.items():
            if is_dir:
                continue
            parts = key.split("/")
            if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
                raise VaultError("Archive file conflicts with a parent directory path.")
        if "manifest.json" not in names:
            raise VaultError("Archive manifest is missing.")
        manifest = json.loads(archive.read("manifest.json"))
        expected_format = f"X-VAULT/{encryption_version}"
        if not isinstance(manifest, dict) or manifest.get("format") != expected_format:
            raise VaultError("Unsupported X-VAULT manifest.")
        return archive, buffer
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise VaultError(f"Cannot read X-VAULT archive: {exc}") from exc


def create_backup(
    source: Path,
    vault_dir: Path,
    passphrase: str | None = None,
    secret_files: tuple[Path, ...] = (),
    *,
    recipient_public_key_path: Path | None = None,
) -> Path:
    if recipient_public_key_path is not None and passphrase is not None:
        raise VaultError("Choose recipient public-key encryption or a passphrase, not both.")
    if recipient_public_key_path is None and passphrase is None:
        raise VaultError("Provide a passphrase or a recipient public key.")
    archive_bytes = _build_archive(
        source,
        secret_files,
        recipient_encrypted=recipient_public_key_path is not None,
    )
    if recipient_public_key_path is not None:
        encrypted = _encrypt_for_recipient(
            archive_bytes,
            _load_recipient_public_key(recipient_public_key_path),
        )
    else:
        encrypted = _encrypt(archive_bytes, passphrase or "")
    vault_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = vault_dir / f"xvault-{stamp}-{secrets.token_hex(3)}.xvlt"
    fd, temp_name = tempfile.mkstemp(prefix=".xvault-", suffix=".tmp", dir=vault_dir)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes atomically without replacing a pre-existing archive.
        os.link(temp_name, destination)
        os.unlink(temp_name)
        return destination
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def verify_backup(
    path: Path,
    passphrase: str | None = None,
    recipient_private_key_path: Path | None = None,
) -> dict:
    archive, _buffer = _read_archive(path, passphrase, recipient_private_key_path)
    manifest = json.loads(archive.read("manifest.json"))
    payload_files = 0
    for item in archive.infolist():
        if item.is_dir():
            continue
        archive.read(item)  # trigger ZIP CRC validation for every payload member
        if item.filename != "manifest.json":
            payload_files += 1
    return {"manifest": manifest, "files": payload_files}


def restore_to_stage(
    path: Path,
    stage: Path,
    passphrase: str | None = None,
    recipient_private_key_path: Path | None = None,
) -> dict:
    if stage.is_symlink():
        raise VaultError("Refusing to restore through a staging-directory symlink.")
    if stage.exists() and (not stage.is_dir() or any(stage.iterdir())):
        raise VaultError("Refusing to restore unless the staging directory is absent or empty.")
    archive, _buffer = _read_archive(path, passphrase, recipient_private_key_path)
    stage.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = stage.resolve()
    restored = 0
    try:
        for info in archive.infolist():
            if info.is_dir() or info.filename == "manifest.json":
                continue
            member = _safe_member(info.filename)
            destination = root.joinpath(*member.parts)
            if not destination.resolve().is_relative_to(root):
                raise VaultError(f"Restore path escapes staging directory: {info.filename!r}")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(destination, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(archive.read(info))
            restored += 1
        return {"restored_files": restored, "manifest": json.loads(archive.read("manifest.json"))}
    except Exception:
        # Keep any partial output for inspection; never recursively remove a caller's stage.
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, verify, or stage-restore encrypted X-VAULT backups")
    subparsers = parser.add_subparsers(dest="action", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--source", type=Path, required=True)
    backup.add_argument("--vault", type=Path, required=True)
    backup.add_argument(
        "--recipient-public-key",
        type=Path,
        help="encrypt to an offline X25519 recovery key; no passphrase is stored on this host",
    )
    backup.add_argument(
        "--include-file", type=Path, action="append", default=[],
        help="include an extra file encrypted in external/<filename> inside the archive",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--private-key", type=Path, help="offline X-VAULT recipient private key")
    restore = subparsers.add_parser("restore-stage")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--stage", type=Path, required=True)
    restore.add_argument("--private-key", type=Path, help="offline X-VAULT recipient private key")
    keygen = subparsers.add_parser("keygen", help="create an offline X25519 recipient key pair")
    keygen.add_argument(
        "--directory", type=Path, required=True,
        help="new directory outside the repository; protect and store the private key offline",
    )
    for action_parser in (backup, verify, restore):
        action_parser.add_argument(
            "--passphrase-stdin",
            action="store_true",
            help="read the passphrase from one line of standard input instead of the environment",
        )
    args = parser.parse_args(argv)
    try:
        if args.action == "keygen":
            info = create_recipient_keypair(args.directory)
            print("X-VAULT recipient key pair created in a protected directory.")
            print(f"fingerprint={info['fingerprint']}")
            print(f"public_key_file={info['public_key_path']}")
            print(f"private_key_file={info['private_key_path']}")
            print("Keep the private key offline; only copy the public key to the backup host.")
            return 0

        private_key_path = getattr(args, "private_key", None)
        recipient_public_key_path = getattr(args, "recipient_public_key", None)
        if private_key_path is not None and args.passphrase_stdin:
            raise VaultError("Choose --private-key or --passphrase-stdin, not both.")
        if recipient_public_key_path is not None and args.passphrase_stdin:
            raise VaultError("Choose --recipient-public-key or --passphrase-stdin, not both.")
        secret = (
            None
            if private_key_path is not None or recipient_public_key_path is not None
            else _passphrase_from_stdin() if args.passphrase_stdin else _passphrase()
        )
        if args.action == "backup":
            result = create_backup(
                args.source,
                args.vault,
                secret,
                tuple(args.include_file),
                recipient_public_key_path=recipient_public_key_path,
            )
            print(f"X-VAULT backup created: {result}")
        elif args.action == "verify":
            result = verify_backup(args.archive, secret, private_key_path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            result = restore_to_stage(args.archive, args.stage, secret, private_key_path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (VaultError, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"X-VAULT error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
