#!/usr/bin/env python3
"""Safely copy untrusted SSH uploads into root-owned X-VAULT storage."""

from __future__ import annotations

import errno
import os
import re
import secrets
import stat
import sys
import time


ARCHIVE_NAME = re.compile(r"^xvault-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{6}\.xvlt$")
STAGING_NAME = re.compile(r"^\.xvault-promote-[0-9]+-[a-f0-9]{16}$")
MAGIC = b"XVAULT2\0"
CHUNK_SIZE = 1024 * 1024


def fail(message: str) -> "NoReturn":
    print(f"X-VAULT standby error: {message}", file=sys.stderr)
    raise SystemExit(1)


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isascii() or not raw.isdecimal():
        fail(f"invalid {name}")
    value = int(raw)
    if not minimum <= value <= maximum:
        fail(f"invalid {name}")
    return value


def stat_at(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def unlink_if_same(directory_fd: int, name: str, original: os.stat_result) -> bool:
    current = stat_at(directory_fd, name)
    if current is None or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
        return False
    os.unlink(name, dir_fd=directory_fd)
    return True


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            fail("short write while staging X-VAULT archive")
        view = view[count:]


def open_directory(path: str, *, root_owned_private: bool = False) -> int:
    try:
        before = os.lstat(path)
    except OSError as exc:
        fail(f"cannot inspect directory {path}: {exc.strerror}")
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        fail(f"directory path is unsafe: {path}")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open directory {path}: {exc.strerror}")
    current = os.fstat(fd)
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        os.close(fd)
        fail(f"directory changed while being opened: {path}")
    if root_owned_private and (
        current.st_uid != 0 or current.st_gid != 0 or stat.S_IMODE(current.st_mode) != 0o700
    ):
        os.close(fd)
        fail("archive directory must be root-owned mode 0700")
    if not root_owned_private and current.st_mode & 0o022:
        os.close(fd)
        fail("incoming directory must not be group/world writable")
    return fd


def cleanup_expired_archives(archive_fd: int, retention_seconds: int, now: float) -> int:
    removed = 0
    with os.scandir(archive_fd) as entries:
        for entry in entries:
            if not ARCHIVE_NAME.fullmatch(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and now - info.st_mtime > retention_seconds:
                    os.unlink(entry.name, dir_fd=archive_fd)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed


def cleanup_stale_staging(archive_fd: int, now: float) -> int:
    removed = 0
    with os.scandir(archive_fd) as entries:
        for entry in entries:
            if not STAGING_NAME.fullmatch(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and now - info.st_mtime > 24 * 60 * 60:
                    os.unlink(entry.name, dir_fd=archive_fd)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed


def archive_bytes(archive_fd: int) -> int:
    total = 0
    with os.scandir(archive_fd) as entries:
        for entry in entries:
            if not ARCHIVE_NAME.fullmatch(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def cleanup_stale_incoming(incoming_fd: int, now: float) -> int:
    removed = 0
    with os.scandir(incoming_fd) as entries:
        for entry in entries:
            if not entry.name.startswith(".") or not entry.name.endswith(".partial"):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if now - info.st_mtime <= 24 * 60 * 60 or stat.S_ISDIR(info.st_mode):
                    continue
                os.unlink(entry.name, dir_fd=incoming_fd)
                removed += 1
            except FileNotFoundError:
                continue
    return removed


def copy_candidate(
    incoming_fd: int,
    archive_fd: int,
    name: str,
    max_archive_bytes: int,
) -> tuple[int, bool]:
    """Copy one candidate without following names supplied by the upload user."""
    try:
        source_fd = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=incoming_fd,
        )
    except FileNotFoundError:
        return 0, False
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            current = stat_at(incoming_fd, name)
            if current is not None and stat.S_ISLNK(current.st_mode):
                os.unlink(name, dir_fd=incoming_fd)
            print(f"Rejected symlinked X-VAULT upload: {name}", file=sys.stderr)
            return 0, False
        print(f"Could not open X-VAULT upload {name}: {exc.strerror}", file=sys.stderr)
        return 0, False

    temp_name = f".xvault-promote-{os.getpid()}-{secrets.token_hex(8)}"
    output_fd = -1
    original: os.stat_result | None = None
    try:
        original = os.fstat(source_fd)
        if not stat.S_ISREG(original.st_mode):
            print(f"Rejected non-regular X-VAULT upload: {name}", file=sys.stderr)
            if not stat.S_ISDIR(original.st_mode):
                unlink_if_same(incoming_fd, name, original)
            return 0, False
        if not 0 < original.st_size <= max_archive_bytes:
            print(f"Rejected X-VAULT upload with invalid size: {name}", file=sys.stderr)
            unlink_if_same(incoming_fd, name, original)
            return 0, False

        output_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=archive_fd,
        )
        magic = os.read(source_fd, len(MAGIC))
        if magic != MAGIC:
            print(f"Rejected upload with unsupported X-VAULT format: {name}", file=sys.stderr)
            unlink_if_same(incoming_fd, name, original)
            return 0, False

        write_all(output_fd, magic)
        total = len(magic)
        while True:
            chunk = os.read(source_fd, CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_archive_bytes or total > original.st_size:
                print(f"Rejected X-VAULT upload that changed size while being copied: {name}", file=sys.stderr)
                unlink_if_same(incoming_fd, name, original)
                return 0, False
            write_all(output_fd, chunk)

        final_source = os.fstat(source_fd)
        unchanged = (
            total == original.st_size
            and (final_source.st_dev, final_source.st_ino) == (original.st_dev, original.st_ino)
            and final_source.st_size == original.st_size
            and final_source.st_mtime_ns == original.st_mtime_ns
            and final_source.st_ctime_ns == original.st_ctime_ns
        )
        if not unchanged:
            print(f"Rejected X-VAULT upload modified during copy: {name}", file=sys.stderr)
            unlink_if_same(incoming_fd, name, original)
            return 0, False

        os.fsync(output_fd)
        os.fchmod(output_fd, 0o400)
        os.close(output_fd)
        output_fd = -1
        # Hard-link publication is atomic and refuses to overwrite an existing
        # archive; both names live in the root-only archive directory.
        os.link(
            temp_name,
            name,
            src_dir_fd=archive_fd,
            dst_dir_fd=archive_fd,
            follow_symlinks=False,
        )
        os.unlink(temp_name, dir_fd=archive_fd)
        os.fsync(archive_fd)
        unlink_if_same(incoming_fd, name, original)
        return total, True
    except FileExistsError:
        print(f"Refusing to overwrite existing X-VAULT archive: {name}", file=sys.stderr)
        if original is not None:
            unlink_if_same(incoming_fd, name, original)
        return 0, False
    except OSError as exc:
        print(f"X-VAULT promotion failed for {name}: {exc.strerror}", file=sys.stderr)
        return 0, False
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        try:
            os.unlink(temp_name, dir_fd=archive_fd)
        except FileNotFoundError:
            pass
        os.close(source_fd)


def main() -> int:
    if os.geteuid() != 0:
        fail("must run as root")
    incoming = os.environ.get("XIDER_VAULT_INCOMING", "/srv/xider-vault/incoming")
    archive_dir = os.environ.get("XIDER_VAULT_ARCHIVE_DIR", "/srv/xider-vault/archives")
    retention_days = env_int("XIDER_VAULT_RETENTION_DAYS", 60, 1, 3650)
    max_archive_bytes = env_int("XIDER_VAULT_MAX_ARCHIVE_BYTES", 1_073_741_824, 1_048_576, 1_099_511_627_776)
    max_total_bytes = env_int("XIDER_VAULT_MAX_TOTAL_BYTES", 10_737_418_240, 1_048_576, 1_099_511_627_776)
    if max_total_bytes < max_archive_bytes:
        fail("maximum total archive capacity must be at least the per-archive limit")
    now = time.time()

    incoming_fd = open_directory(incoming)
    archive_fd = open_directory(archive_dir, root_owned_private=True)
    try:
        expired = cleanup_expired_archives(archive_fd, retention_days * 24 * 60 * 60, now)
        stale_staging = cleanup_stale_staging(archive_fd, now)
        removed_partial = cleanup_stale_incoming(incoming_fd, now)
        used_bytes = archive_bytes(archive_fd)
        promoted = 0
        with os.scandir(incoming_fd) as entries:
            candidates = sorted(entry.name for entry in entries if ARCHIVE_NAME.fullmatch(entry.name))
        for name in candidates:
            current = stat_at(incoming_fd, name)
            if current is None:
                continue
            if stat.S_ISLNK(current.st_mode):
                os.unlink(name, dir_fd=incoming_fd)
                print(f"Rejected symlinked X-VAULT upload: {name}", file=sys.stderr)
                continue
            if not stat.S_ISREG(current.st_mode):
                print(f"Rejected non-regular X-VAULT upload: {name}", file=sys.stderr)
                continue
            if current.st_size > max_total_bytes - used_bytes:
                print(f"X-VAULT archive capacity reached; keeping upload for retry: {name}", file=sys.stderr)
                continue
            copied, ok = copy_candidate(incoming_fd, archive_fd, name, max_archive_bytes)
            if ok:
                used_bytes += copied
                promoted += 1
                print(f"Promoted encrypted X-VAULT archive: {name}")
        print(
            "X-VAULT standby promotion complete; "
            f"promoted={promoted} expired={expired} stale_staging_removed={stale_staging} "
            f"stale_partial_removed={removed_partial} "
            f"retention_days={retention_days} total_bytes={used_bytes}/{max_total_bytes}"
        )
    finally:
        os.close(incoming_fd)
        os.close(archive_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
