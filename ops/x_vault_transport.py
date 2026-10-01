#!/usr/bin/env python3
"""Bounded SSH stream transport for encrypted X-VAULT archives.

The receiver accepts one length-prefixed archive at a time. It never accepts
client paths or commands, reserves bounded inbox capacity before writing, and
shares a lock with the root-only promoter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import os
import re
import secrets
import signal
import stat
import sys
import time
from pathlib import Path
from typing import BinaryIO


MAGIC = b"XVAULT2\0"
ARCHIVE_NAME = re.compile(r"^xvault-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{6}\.xvlt$")
PARTIAL_NAME = re.compile(r"^\.xvault-upload-[a-f0-9]{24}\.partial$")
HEADER_BYTES = 8
CHUNK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_INCOMING_BYTES = 2 * MAX_ARCHIVE_BYTES
INCOMING_DIR = "/srv/xider-vault/incoming"
UPLOAD_LOCK = "/run/xider-vault-upload.lock"
MAX_TRANSFER_SECONDS = 60 * 60


class UploadError(ValueError):
    """A malformed or over-limit X-VAULT stream."""


def _read_exact(stream: BinaryIO, count: int) -> bytes:
    parts = bytearray()
    while len(parts) < count:
        block = stream.read(count - len(parts))
        if not block:
            raise UploadError("truncated X-VAULT stream")
        parts.extend(block)
    return bytes(parts)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise UploadError("short write while receiving X-VAULT archive")
        view = view[written:]


def _open_private_directory(path: str | Path, expected_owner: int) -> int:
    path = str(path)
    try:
        before = os.lstat(path)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise UploadError("X-VAULT incoming path is not a real directory")
        if before.st_uid != expected_owner or stat.S_IMODE(before.st_mode) != 0o700:
            raise UploadError("X-VAULT incoming directory must be owned by the uploader and mode 0700")
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            os.close(fd)
            raise UploadError("X-VAULT incoming directory changed while opening")
        return fd
    except OSError as exc:
        raise UploadError(f"cannot open X-VAULT incoming directory: {exc.strerror}") from exc


def _acquire_shared_lock(path: str | Path, wait_seconds: float = 60) -> int:
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(fd)
        groups = set(os.getgroups()) | {os.getegid()}
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid not in groups
            or stat.S_IMODE(info.st_mode) != 0o660
        ):
            os.close(fd)
            raise UploadError("X-VAULT shared lock must be root-owned mode 0660 and group-accessible")
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise UploadError("X-VAULT promoter or another upload is still running")
                time.sleep(0.1)
    except OSError as exc:
        raise UploadError(f"cannot acquire X-VAULT shared lock: {exc.strerror}") from exc


def _incoming_usage(directory_fd: int) -> int:
    total = 0
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if PARTIAL_NAME.fullmatch(entry.name):
                if not stat.S_ISREG(info.st_mode):
                    raise UploadError("unexpected non-regular X-VAULT staging entry")
                # The shared lock proves no live receiver owns a staging file.
                os.unlink(entry.name, dir_fd=directory_fd)
                continue
            if not ARCHIVE_NAME.fullmatch(entry.name) or not stat.S_ISREG(info.st_mode):
                raise UploadError("unexpected entry in bounded X-VAULT inbox")
            total += info.st_size
    return total


def receive_upload(
    stream: BinaryIO,
    incoming_dir: str | Path = INCOMING_DIR,
    lock_path: str | Path = UPLOAD_LOCK,
    *,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
    max_incoming_bytes: int = MAX_INCOMING_BYTES,
    lock_wait_seconds: float = 60,
) -> str:
    """Receive a frame from ``stream`` and atomically publish its archive."""
    if (
        isinstance(max_archive_bytes, bool)
        or isinstance(max_incoming_bytes, bool)
        or not 1 <= max_archive_bytes <= MAX_ARCHIVE_BYTES
        or not max_archive_bytes <= max_incoming_bytes <= MAX_INCOMING_BYTES
    ):
        raise UploadError("invalid X-VAULT receiver capacity configuration")

    header = _read_exact(stream, HEADER_BYTES)
    declared = int.from_bytes(header, "big", signed=False)
    if not len(MAGIC) <= declared <= max_archive_bytes:
        raise UploadError("declared archive size is outside the receiver limit")

    directory_fd = _open_private_directory(incoming_dir, os.geteuid())
    lock_fd = -1
    output_fd = -1
    partial_name = f".xvault-upload-{secrets.token_hex(12)}.partial"
    try:
        lock_fd = _acquire_shared_lock(lock_path, lock_wait_seconds)
        existing_bytes = _incoming_usage(directory_fd)
        if existing_bytes + declared > max_incoming_bytes:
            raise UploadError("bounded X-VAULT inbox is full; promotion must run before retrying")

        output_fd = os.open(
            partial_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        digest = hashlib.sha256()
        remaining = declared
        prefix = bytearray()
        while remaining:
            block = _read_exact(stream, min(CHUNK_BYTES, remaining))
            if len(prefix) < len(MAGIC):
                prefix.extend(block[: len(MAGIC) - len(prefix)])
            _write_all(output_fd, block)
            digest.update(block)
            remaining -= len(block)
        if bytes(prefix) != MAGIC:
            raise UploadError("unsupported X-VAULT archive header")
        if stream.read(1):
            raise UploadError("extra bytes follow the declared X-VAULT archive")

        os.fsync(output_fd)
        os.fchmod(output_fd, 0o400)
        os.close(output_fd)
        output_fd = -1

        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final_name = f"xvault-{timestamp}-{digest.hexdigest()[:6]}.xvlt"
        try:
            os.link(
                partial_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise UploadError("an archive with the generated name already exists; retry the backup") from exc
        os.unlink(partial_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return final_name
    except OSError as exc:
        if isinstance(exc, UploadError):
            raise
        raise UploadError(f"X-VAULT receive failed: {exc.strerror}") from exc
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        try:
            os.unlink(partial_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(directory_fd)


def send_archive(path: str | Path, output: BinaryIO) -> None:
    """Write an 8-byte length prefix followed by one stable regular file."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise UploadError("X-VAULT source must be a regular non-symlink file")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UploadError(f"cannot open X-VAULT source: {exc.strerror}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not len(MAGIC) <= before.st_size <= MAX_ARCHIVE_BYTES:
            raise UploadError("X-VAULT source size is outside the transport limit")
        output.write(before.st_size.to_bytes(HEADER_BYTES, "big"))
        output.flush()
        total = 0
        while True:
            block = os.read(fd, CHUNK_BYTES)
            if not block:
                break
            total += len(block)
            if total > before.st_size:
                raise UploadError("X-VAULT source changed while sending")
            output.write(block)
        output.flush()
        after = os.fstat(fd)
        if (
            total != before.st_size
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise UploadError("X-VAULT source changed while sending")
    except OSError as exc:
        raise UploadError(f"X-VAULT send failed: {exc.strerror}") from exc
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    send_parser = commands.add_parser("send")
    send_parser.add_argument("archive")
    commands.add_parser("receive")
    args = parser.parse_args()
    try:
        if args.command == "send":
            send_archive(args.archive, sys.stdout.buffer)
        else:
            def deadline(_signum, _frame):
                raise UploadError("X-VAULT transfer exceeded the one-hour deadline")

            signal.signal(signal.SIGALRM, deadline)
            signal.alarm(MAX_TRANSFER_SECONDS)
            try:
                name = receive_upload(sys.stdin.buffer)
                print(f"X-VAULT upload accepted: {name}")
            finally:
                signal.alarm(0)
    except (OSError, UploadError) as exc:
        print(f"X-VAULT transport error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
