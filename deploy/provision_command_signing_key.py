#!/usr/bin/env python3
"""Provision the persistent server key and atomically publish its public keyset.

Run as root after the xider service account exists. Overrides are intended for
isolated installer fixtures. Ordinary installs never replace an existing key.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_PATH = Path("/etc/xider/command-signing-private.pem")
ENV_NAME = "XIDER_COMMAND_VERIFY_KEYSET_B64"
ENV_PATTERN = re.compile(r"^\s*(?:export\s+)?" + ENV_NAME + r"\s*=\s*(.*?)\s*$")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value) or len(value) % 4 == 1:
        raise ValueError("invalid public keyset encoding")
    data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _encode(data) != value:
        raise ValueError("noncanonical public keyset encoding")
    return data


def _canonical(entries: dict) -> bytes:
    return json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _public_keyset(value: str) -> dict[str, str]:
    def unique(pairs):
        entries = {}
        for key, public in pairs:
            if key in entries:
                raise ValueError("duplicate public key id")
            entries[key] = public
        return entries

    if len(value) > 1024:
        raise ValueError("public keyset too large")
    raw = _decode(value)
    if len(raw) > 512:
        raise ValueError("public keyset too large")
    entries = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if type(entries) is not dict or not 1 <= len(entries) <= 2 or _canonical(entries) != raw:
        raise ValueError("invalid canonical public keyset")
    for key_id, public in entries.items():
        if not isinstance(key_id, str) or not re.fullmatch(r"[0-9a-f]{16}", key_id) or not isinstance(public, str):
            raise ValueError("invalid public key entry")
        public_bytes = _decode(public)
        if len(public_bytes) != 32 or hashlib.sha256(public_bytes).hexdigest()[:16] != key_id:
            raise ValueError("public key id mismatch")
    return entries


def _check_directory(directory: Path):
    for path in (directory, *directory.parents):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("symlink or invalid parent directory")
    info = directory.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("provisioning directory must be root-owned and not writable by others")


def _read_regular(path: Path, limit: int, *, allowed_links=(1,)):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink not in allowed_links or info.st_size > limit:
            raise ValueError("unsafe or oversized provisioning file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("oversized provisioning file")
        return data, info


def _sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publication_alias(path: Path, info, gid: int) -> Path | None:
    """Recognize only our root-owned, same-inode interrupted mkstemp link."""
    if info.st_nlink == 1:
        return None
    if info.st_nlink != 2 or info.st_uid != 0 or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o640:
        raise ValueError("unsafe linked signing key")
    pattern = re.compile(re.escape(f".{path.name}.") + r"[a-z0-9_]{8}\.tmp")
    aliases = []
    for candidate in path.parent.iterdir():
        if not pattern.fullmatch(candidate.name):
            continue
        alias_info = candidate.lstat()
        if stat.S_ISREG(alias_info.st_mode) and (alias_info.st_dev, alias_info.st_ino) == (info.st_dev, info.st_ino):
            aliases.append(candidate)
    if len(aliases) != 1:
        raise ValueError("unrecognized signing-key hardlink")
    return aliases[0]


def _recover_publication(path: Path, alias: Path, info):
    # The caller holds the provisioning lock in a root-owned directory that
    # nobody else can write. Recheck both names before removing only the alias.
    current, pending = path.lstat(), alias.lstat()
    identity = (info.st_dev, info.st_ino)
    if not all(stat.S_ISREG(item.st_mode) and item.st_nlink == 2 and
               (item.st_dev, item.st_ino) == identity for item in (current, pending)):
        raise ValueError("signing-key publication state changed")
    os.unlink(alias)
    _sync_directory(path.parent)
    if path.lstat().st_nlink != 1:
        raise ValueError("signing-key recovery is incomplete")


def _atomic_file(path: Path, data: bytes, *, mode: int, uid: int, gid: int, create_only=False):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            # An exclusive link publishes the fully written key without ever
            # replacing a concurrently created key or following a symlink.
            os.link(temporary, path, follow_symlinks=False)
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
            # Persist the alias removal as well as final-name publication. A
            # crash before this sync is recoverable on the next installer run.
            _sync_directory(path.parent)


def provision(key_path: Path = KEY_PATH, env_file: Path = Path("/etc/xider/bot.env"), *, group="xider") -> str:
    if os.geteuid() != 0:
        raise PermissionError("provisioning requires root")
    key_path, env_file = Path(key_path).absolute(), Path(env_file).absolute()
    if key_path == env_file:
        raise ValueError("key and environment paths must differ")
    gid = grp.getgrnam(group).gr_gid
    _check_directory(key_path.parent)
    _check_directory(env_file.parent)
    lock_fd = os.open(key_path.parent / ".command-signing-provision.lock",
                      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("unsafe provisioning lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        original, env_info = _read_regular(env_file, 1024 * 1024)
        text = original.decode("utf-8")
        # Keep the BOM and unrelated lines byte-for-byte, including CRLF.
        bom = "\ufeff" if text.startswith("\ufeff") else ""
        lines = text.removeprefix(bom).splitlines(keepends=True)
        matches = [(index, ENV_PATTERN.fullmatch(line.rstrip("\r\n"))) for index, line in enumerate(lines)]
        matches = [(index, match) for index, match in matches if match]
        if len(matches) > 1:
            raise ValueError("duplicate public keyset environment variable")
        entries = {}
        if matches:
            value = matches[0][1].group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            entries = _public_keyset(value)
        try:
            private_bytes, key_info = _read_regular(key_path, 4096, allowed_links=(1, 2))
        except FileNotFoundError:
            key = Ed25519PrivateKey.generate()
            absent = True
        else:
            key = serialization.load_pem_private_key(private_bytes, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("existing key is not Ed25519")
            publication_alias = _publication_alias(key_path, key_info, gid)
            absent = False
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        key_id = hashlib.sha256(public).hexdigest()[:16]
        if key_id in entries and entries[key_id] != _encode(public):
            raise ValueError("current signing key public bytes mismatch")
        if key_id not in entries and len(entries) >= 2:
            raise ValueError("current signing key missing from full overlap keyset")
        entries[key_id] = _encode(public)
        value = _encode(_canonical(entries))
        if absent:
            private_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())
            _atomic_file(key_path, private_bytes, mode=0o640, uid=0, gid=gid, create_only=True)
        else:
            if publication_alias is not None:
                _recover_publication(key_path, publication_alias, key_info)
            fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fchown(fd, 0, gid)
                os.fchmod(fd, 0o640)
            finally:
                os.close(fd)
        os.chown(key_path.parent, 0, gid, follow_symlinks=False)
        os.chmod(key_path.parent, 0o750)
        newline = "\r\n" if b"\r\n" in original else "\n"
        replacement = f"{ENV_NAME}={value}{newline}"
        if matches:
            lines[matches[0][0]] = replacement
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(replacement)
        updated = (bom + "".join(lines)).encode("utf-8")
        if updated != original:
            _atomic_file(env_file, updated, mode=stat.S_IMODE(env_info.st_mode), uid=env_info.st_uid, gid=env_info.st_gid)
        return key_id
    finally:
        os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-path", type=Path, default=KEY_PATH)
    parser.add_argument("--env-file", type=Path, default=Path("/etc/xider/bot.env"))
    parser.add_argument("--group", default="xider")
    args = parser.parse_args()
    try:
        provision(args.key_path, args.env_file, group=args.group)
    except (OSError, ValueError, TypeError, KeyError):
        # Exception details from cryptography/filesystem/env parsing are never
        # printed: neither private material nor unrelated runtime secrets leak.
        print("Command signing provisioning refused unsafe or invalid state.", file=sys.stderr)
        return 1
    print("Command signing key preserved/provisioned; public verification keyset ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
