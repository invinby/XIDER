#!/usr/bin/env python3
"""X-VAULT: authenticated encrypted backups with safe staging restores."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import stat
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAGIC = b"XVAULT1\0"
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
PASSPHRASE_ENV = "XIDER_VAULT_PASSPHRASE"
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "incoming"}


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


def _build_archive(source: Path, secret_files: tuple[Path, ...] = ()) -> bytes:
    if source.is_symlink() or not source.is_dir():
        raise VaultError("Backup source must be an existing directory.")
    output = io.BytesIO()
    manifest = {
        "format": "X-VAULT/1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_name": source.name,
        "encryption": "AES-256-GCM with Scrypt key derivation",
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


def _decrypt(blob: bytes, passphrase: str) -> bytes:
    header_size = len(MAGIC) + SALT_BYTES + NONCE_BYTES
    if len(blob) <= header_size or not blob.startswith(MAGIC):
        raise VaultError("Not a supported X-VAULT archive or archive is truncated.")
    salt_start = len(MAGIC)
    salt = blob[salt_start : salt_start + SALT_BYTES]
    nonce_start = salt_start + SALT_BYTES
    nonce = blob[nonce_start : nonce_start + NONCE_BYTES]
    header = blob[:header_size]
    try:
        return AESGCM(_derive_key(passphrase, salt)).decrypt(nonce, blob[header_size:], header)
    except InvalidTag as exc:
        raise VaultError("Authentication failed: wrong passphrase or damaged archive.") from exc


def _read_archive(path: Path, passphrase: str) -> tuple[zipfile.ZipFile, io.BytesIO]:
    try:
        payload = _decrypt(path.read_bytes(), passphrase)
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
        if not isinstance(manifest, dict) or manifest.get("format") != "X-VAULT/1":
            raise VaultError("Unsupported X-VAULT manifest.")
        return archive, buffer
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise VaultError(f"Cannot read X-VAULT archive: {exc}") from exc


def create_backup(
    source: Path,
    vault_dir: Path,
    passphrase: str,
    secret_files: tuple[Path, ...] = (),
) -> Path:
    archive_bytes = _build_archive(source, secret_files)
    encrypted = _encrypt(archive_bytes, passphrase)
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


def verify_backup(path: Path, passphrase: str) -> dict:
    archive, _buffer = _read_archive(path, passphrase)
    manifest = json.loads(archive.read("manifest.json"))
    payload_files = 0
    for item in archive.infolist():
        if item.is_dir():
            continue
        archive.read(item)  # trigger ZIP CRC validation for every payload member
        if item.filename != "manifest.json":
            payload_files += 1
    return {"manifest": manifest, "files": payload_files}


def restore_to_stage(path: Path, stage: Path, passphrase: str) -> dict:
    if stage.is_symlink():
        raise VaultError("Refusing to restore through a staging-directory symlink.")
    if stage.exists() and (not stage.is_dir() or any(stage.iterdir())):
        raise VaultError("Refusing to restore unless the staging directory is absent or empty.")
    archive, _buffer = _read_archive(path, passphrase)
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
        "--include-file", type=Path, action="append", default=[],
        help="include an extra file encrypted in external/<filename> inside the archive",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    restore = subparsers.add_parser("restore-stage")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--stage", type=Path, required=True)
    for action_parser in (backup, verify, restore):
        action_parser.add_argument(
            "--passphrase-stdin",
            action="store_true",
            help="read the passphrase from one line of standard input instead of the environment",
        )
    args = parser.parse_args(argv)
    try:
        secret = _passphrase_from_stdin() if args.passphrase_stdin else _passphrase()
        if args.action == "backup":
            result = create_backup(
                args.source, args.vault, secret, tuple(args.include_file),
            )
            print(f"X-VAULT backup created: {result}")
        elif args.action == "verify":
            result = verify_backup(args.archive, secret)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            result = restore_to_stage(args.archive, args.stage, secret)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (VaultError, OSError, ValueError) as exc:
        print(f"X-VAULT error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
