"""Verify and transactionally install the signed macOS XIDER app bundle set."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile


APP_EXECUTABLES = {
    "XIDER.app": "Contents/MacOS/XGENT-MCS",
    "XIDER-Guard-Keeper.app": "Contents/MacOS/XIDER-Guard-Keeper",
    "XIDER-Layers.app": "Contents/MacOS/XIDER-Layers",
}
MAX_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def _validated_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not members or len(members) > MAX_MEMBERS:
        raise ValueError("macOS bundle is empty or has too many members")
    seen: set[str] = set()
    roots: set[str] = set()
    symlinks: set[str] = set()
    total = 0
    for entry in members:
        name = entry.filename
        path = PurePosixPath(name)
        mode = (entry.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        normalized = "/".join(path.parts)
        if (
            not name or "\\" in name or path.is_absolute() or not path.parts
            or ".." in path.parts or "." in path.parts
            or path.parts[0] not in APP_EXECUTABLES
            or normalized in seen or entry.flag_bits & 1
            or file_type not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}
        ):
            raise ValueError(f"unsafe macOS bundle member: {name!r}")
        if file_type == stat.S_IFLNK:
            try:
                target = archive.read(entry).decode("utf-8")
            except (UnicodeDecodeError, RuntimeError, OSError) as exc:
                raise ValueError(f"unsafe macOS bundle symlink: {name!r}") from exc
            target_path = PurePosixPath(target)
            if (
                len(path.parts) < 2 or not target or "\\" in target or "\x00" in target
                or target_path.is_absolute() or any(part in {".", ".."} for part in target_path.parts)
            ):
                raise ValueError(f"unsafe macOS bundle symlink: {name!r}")
            symlinks.add(normalized)
        seen.add(normalized)
        roots.add(path.parts[0])
        total += entry.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("macOS bundle exceeds the uncompressed size limit")
    if roots != set(APP_EXECUTABLES):
        raise ValueError("macOS bundle must contain all three XIDER apps")
    for name in seen:
        parts = PurePosixPath(name).parts
        if any("/".join(parts[:index]) in symlinks for index in range(1, len(parts))):
            raise ValueError(f"macOS bundle member traverses an archive symlink: {name!r}")
    for app, executable in APP_EXECUTABLES.items():
        expected = f"{app}/{executable}"
        if expected not in seen:
            raise ValueError(f"macOS bundle is missing {expected}")
    return members


def _verify_signed_app(app_path: Path) -> None:
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app_path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    subprocess.run(
        ["xcrun", "stapler", "validate", str(app_path)],
        capture_output=True, text=True, check=True, timeout=60,
    )


def _extract_apps(archive_path: Path, stage_root: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        members = _validated_members(archive)
        symlink_members = []
        for entry in members:
            relative = PurePosixPath(entry.filename)
            destination = stage_root.joinpath(*relative.parts)
            resolved = destination.resolve()
            if resolved != destination.absolute() and os.name == "nt":
                raise ValueError("bundle extraction resolved through an unexpected path")
            if stage_root.resolve() not in resolved.parents and resolved != stage_root.resolve():
                raise ValueError("bundle member escaped the staging directory")
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            mode = (entry.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                symlink_members.append((entry, destination))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if entry.external_attr >> 16 & 0o111:
                destination.chmod(destination.stat().st_mode | 0o111)
        for entry, destination in symlink_members:
            destination.parent.mkdir(parents=True, exist_ok=True)
            target = archive.read(entry).decode("utf-8")
            os.symlink(target, destination)


def install_signed_bundle(archive_path: Path, install_root: Path, operation_id: str) -> Path:
    """Verify all signed apps before swapping any, and restore old apps on failure.

    The operation ID is supplied by the owner-approved bot update transaction;
    it is restricted to hex so it can only name isolated staging/backup paths.
    The backup directory is retained after success for explicit rollback.
    """
    if not isinstance(operation_id, str) or len(operation_id) != 32 or any(
        char not in "0123456789abcdef" for char in operation_id
    ):
        raise ValueError("invalid bundle update operation ID")
    archive_path = Path(archive_path)
    install_root = Path(install_root).resolve()
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("signed macOS bundle archive is missing or unsafe")
    stage_root = Path(tempfile.mkdtemp(prefix=f".xider-update-{operation_id}-", dir=install_root))
    backup_root = install_root / f".xider-update-{operation_id}.backup"
    moved: list[str] = []
    replaced: list[str] = []
    try:
        _extract_apps(archive_path, stage_root)
        for app in APP_EXECUTABLES:
            staged = stage_root / app
            if staged.is_symlink() or not staged.is_dir():
                raise ValueError(f"staged app is missing or unsafe: {app}")
            _verify_signed_app(staged)

        if backup_root.exists() or backup_root.is_symlink():
            raise FileExistsError("bundle update backup already exists")
        backup_root.mkdir(mode=0o700)
        for app in APP_EXECUTABLES:
            live = install_root / app
            old = backup_root / app
            staged = stage_root / app
            if live.is_symlink() or not live.is_dir():
                raise ValueError(f"installed app is missing or unsafe: {app}")
            os.replace(live, old)
            moved.append(app)
            os.replace(staged, live)
            replaced.append(app)
        return backup_root
    except Exception:
        for app in reversed(moved):
            live = install_root / app
            old = backup_root / app
            try:
                if app in replaced and live.exists() and not live.is_symlink():
                    shutil.rmtree(live)
                if old.exists() and not old.is_symlink():
                    os.replace(old, live)
            except Exception:
                # Keep the remaining backup material for manual recovery.
                pass
        if backup_root.is_dir() and not backup_root.is_symlink():
            try:
                backup_root.rmdir()
            except OSError:
                pass
        raise
    finally:
        if stage_root.exists() and not stage_root.is_symlink():
            shutil.rmtree(stage_root, ignore_errors=True)


def restore_backup_set(backup_root: Path, install_root: Path, operation_id: str) -> Path:
    """Restore all previous apps as one guarded operation, retaining failed apps."""
    if not isinstance(operation_id, str) or len(operation_id) != 32 or any(
        char not in "0123456789abcdef" for char in operation_id
    ):
        raise ValueError("invalid bundle update operation ID")
    install_root = Path(install_root).resolve()
    backup_root = Path(backup_root)
    failed_root = install_root / f".xider-update-{operation_id}.failed"
    if (backup_root.is_symlink() or not backup_root.is_dir()
            or backup_root.parent.resolve() != install_root
            or failed_root.exists() or failed_root.is_symlink()):
        raise ValueError("macOS bundle backup is missing or unsafe")
    for app in APP_EXECUTABLES:
        if (backup_root / app).is_symlink() or not (backup_root / app).is_dir():
            raise ValueError(f"previous signed app is missing or unsafe: {app}")
        live = install_root / app
        if live.is_symlink() or not live.is_dir():
            raise ValueError(f"current app is missing or unsafe: {app}")
    failed_root.mkdir(mode=0o700)
    moved_live: list[str] = []
    restored: list[str] = []
    try:
        for app in APP_EXECUTABLES:
            os.replace(install_root / app, failed_root / app)
            moved_live.append(app)
            os.replace(backup_root / app, install_root / app)
            restored.append(app)
        return failed_root
    except Exception:
        for app in reversed(moved_live):
            try:
                old_backup = install_root / app
                failed = failed_root / app
                if app in restored and old_backup.exists() and not old_backup.is_symlink():
                    os.replace(old_backup, backup_root / app)
                if failed.exists() and not failed.is_symlink():
                    os.replace(failed, install_root / app)
            except Exception:
                pass
        try:
            failed_root.rmdir()
        except OSError:
            pass
        raise
