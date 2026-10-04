"""Local, publisher-signed recovery inputs for the visible XIDER Guardian.

The installer stages a verified release archive outside the mutable checkout.
Slots are content addressed, never overwritten, and verified again before use.
Ordinary owner deletion/uninstall remains possible; no OS immutable flags or
additional supervisors are installed here. Secrets are never recovery assets.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

from release_signature import verify_manifest_signature
from update_package import (
    MANIFEST_ASSET,
    MAX_ARCHIVE_BYTES,
    MAX_MANIFEST_BYTES,
    SOURCE_ASSET,
    extract_agent_files,
)


RECOVERY_FILES = (
    "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
    "release_signature.py", "update_package.py", "guardian_recovery.py",
    "xider_guardian.py", "requirements.txt", "setup_mac.py", "fake_update_screen.py",
    "start_agent.sh", "stop_agent.sh", "start_guardian.sh",
)
_RELEASE = re.compile(r"^v\d{1,6}\.\d{1,6}\.\d{1,6}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SLOT = re.compile(r"^v\d{1,6}\.\d{1,6}\.\d{1,6}-[0-9a-f]{64}$")


def _plain_path(path: Path) -> Path:
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if not candidate.is_symlink():
            continue
        # macOS itself exposes /var and /tmp through root-owned links to
        # /private. Its tempfile paths use these prefixes; user-owned links
        # and linked input files still fail closed.
        system_target = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}.get(candidate)
        if (
            sys.platform != "darwin" or system_target is None
            or candidate.lstat().st_uid != 0 or candidate.resolve() != system_target
        ):
            raise ValueError("Recovery path must not contain symlinks.")
    return path.resolve()


def _read_bounded(path: Path, limit: int) -> bytes:
    path = _plain_path(path)
    if not path.is_file():
        raise ValueError(f"Recovery input is not a regular file: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError(f"Recovery input exceeds its size limit: {path.name}")
    return data


def _verified_inputs(manifest_path: Path, archive_path: Path) -> tuple[str, str, bytes, bytes]:
    manifest_bytes = _read_bounded(manifest_path, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Recovery release manifest is damaged.") from exc
    verify_manifest_signature(manifest)
    release = manifest.get("release")
    if (
        type(manifest.get("schema")) is not int or manifest["schema"] != 1
        or not isinstance(release, str) or not _RELEASE.fullmatch(release)
    ):
        raise ValueError("Recovery release manifest has invalid metadata.")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Recovery manifest has no asset inventory.")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == SOURCE_ASSET]
    if len(matches) != 1:
        raise ValueError("Recovery manifest must identify one source archive.")
    asset = matches[0]
    size, digest = asset.get("size"), asset.get("sha256")
    if (
        asset.get("component") != "source" or asset.get("platform") != "all"
        or type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES
        or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
    ):
        raise ValueError("Recovery source archive metadata is invalid.")
    # Hash and later extract these same bytes, so changing the cache while it is
    # being read cannot swap a different archive into the extraction step.
    archive_bytes = _read_bounded(archive_path, size)
    if len(archive_bytes) != size or not hmac.compare_digest(hashlib.sha256(archive_bytes).hexdigest(), digest):
        raise ValueError("Recovery archive size or SHA-256 does not match the signed manifest.")
    return release, digest, manifest_bytes, archive_bytes


def _prepare_archive(archive_bytes: bytes, temporary_root: Path, files: tuple[str, ...]) -> Path:
    archive_path = temporary_root / SOURCE_ASSET
    archive_path.write_bytes(archive_bytes)
    return extract_agent_files(archive_path, temporary_root / "agent", files)


def _write_new_file(path: Path, data: bytes, mode: int = 0o400) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_recovery_package(
    manifest_path: Path,
    archive_path: Path,
    recovery_root: Path,
    *,
    activate: bool = True,
) -> str:
    """Stage verified installer inputs; return their content-addressed slot ID.

    This never copies executable recovery files from the installed checkout.
    Existing slots must contain exactly the same signed bytes; they are not
    replaced. Updates use activate=False until the new worker passes its health
    check; first installation may select the package immediately.
    """
    release, digest, manifest_bytes, archive_bytes = _verified_inputs(manifest_path, archive_path)
    with tempfile.TemporaryDirectory(prefix="xider-recovery-verify-") as temporary:
        _prepare_archive(archive_bytes, Path(temporary), RECOVERY_FILES)
    recovery_root = _plain_path(recovery_root)
    slots = _plain_path(recovery_root / "slots")
    slots.mkdir(parents=True, exist_ok=True, mode=0o700)
    slot_name = f"{release}-{digest}"
    slot = _plain_path(slots / slot_name)
    if slot.exists():
        cached = _verified_inputs(slot / MANIFEST_ASSET, slot / SOURCE_ASSET)
        if cached[2:] != (manifest_bytes, archive_bytes):
            raise ValueError("An existing recovery slot differs from the installer inputs.")
    else:
        slot.mkdir(mode=0o700)
        # A failed interrupted write leaves an unselected slot; a later install
        # fails closed and the owner can remove that incomplete cache normally.
        _write_new_file(slot / MANIFEST_ASSET, manifest_bytes)
        _write_new_file(slot / SOURCE_ASSET, archive_bytes)
        _flush_directory(slot)
        _flush_directory(slots)
        _flush_directory(recovery_root)
    if activate:
        activate_recovery_package(recovery_root, slot_name)
    return slot_name


def activate_recovery_package(recovery_root: Path, slot_name: str) -> str:
    """Reverify and atomically select a staged slot; return its signed tag."""
    recovery_root = _plain_path(recovery_root)
    if not isinstance(slot_name, str) or not _SLOT.fullmatch(slot_name):
        raise ValueError("Recovery slot ID is invalid.")
    slot = _plain_path(recovery_root / "slots" / slot_name)
    release, digest, _manifest, archive_bytes = _verified_inputs(slot / MANIFEST_ASSET, slot / SOURCE_ASSET)
    if slot_name != f"{release}-{digest}":
        raise ValueError("Recovery slot does not match its signed contents.")
    with tempfile.TemporaryDirectory(prefix="xider-recovery-activate-") as temporary:
        _prepare_archive(archive_bytes, Path(temporary), RECOVERY_FILES)
    pointer = _plain_path(recovery_root / "current.json")
    descriptor, temporary = tempfile.mkstemp(prefix=".recovery-current-", dir=recovery_root)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "slot": slot_name}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, pointer)
        _flush_directory(recovery_root)
    finally:
        temporary_path.unlink(missing_ok=True)
    return release


def validate_recovery_install(install_dir: Path, recovery_root: Path, slot_name: str) -> str:
    """Verify that a complete candidate checkout matches this signed slot.

    Installers call this before selecting a recovery package or starting the
    staged candidate. No installed file or cache selection is changed.
    """
    install_dir, recovery_root = _plain_path(install_dir), _plain_path(recovery_root)
    if (
        install_dir == recovery_root or install_dir in recovery_root.parents
        or recovery_root in install_dir.parents
    ):
        raise ValueError("Recovery package must be outside the installed checkout.")
    if not isinstance(slot_name, str) or not _SLOT.fullmatch(slot_name):
        raise ValueError("Recovery slot ID is invalid.")
    slot = _plain_path(recovery_root / "slots" / slot_name)
    release, digest, _manifest, archive_bytes = _verified_inputs(slot / MANIFEST_ASSET, slot / SOURCE_ASSET)
    if slot_name != f"{release}-{digest}":
        raise ValueError("Recovery slot does not match its signed contents.")
    with tempfile.TemporaryDirectory(prefix="xider-recovery-candidate-") as temporary:
        prepared = _prepare_archive(archive_bytes, Path(temporary), RECOVERY_FILES)
        for name in RECOVERY_FILES:
            if _read_bounded(install_dir / name, MAX_ARCHIVE_BYTES) != (prepared / name).read_bytes():
                raise ValueError(f"Installed file differs from the selected recovery release: {name}")
    return release


def restore_missing_agent_files(
    install_dir: Path,
    recovery_root: Path,
    files: tuple[str, ...] = RECOVERY_FILES,
    *,
    before_restore: Callable[[], None] | None = None,
) -> list[str]:
    """Verify the cached package, then create only missing allowlisted files.

    Existing files are preserved. Symlinks, damaged packages, traversal paths
    and secrets are rejected before any installed file is written.
    """
    install_dir = _plain_path(install_dir)
    recovery_root = _plain_path(recovery_root)
    if (
        install_dir == recovery_root or install_dir in recovery_root.parents
        or recovery_root in install_dir.parents
    ):
        raise ValueError("Recovery package must be outside the installed checkout.")
    if not files or len(set(files)) != len(files) or any(name not in RECOVERY_FILES for name in files):
        raise ValueError("Invalid recovery file allowlist.")
    targets = {name: _plain_path(install_dir / name) for name in files}
    if any(path.exists() and not path.is_file() for path in targets.values()):
        raise ValueError("Recovery target is not a regular file.")
    missing = tuple(name for name, path in targets.items() if not path.exists())
    if not missing:
        return []
    pointer_path = recovery_root / "current.json"
    if not pointer_path.exists():
        raise RuntimeError("Подписанный recovery-пакет ещё не установлен; нужна проверенная установка X-DOCK.")
    try:
        pointer = json.loads(_read_bounded(pointer_path, 4096))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Recovery package selection is damaged.") from exc
    slot_name = pointer.get("slot") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict) or type(pointer.get("schema")) is not int
        or pointer["schema"] != 1 or not isinstance(slot_name, str) or not _SLOT.fullmatch(slot_name)
    ):
        raise ValueError("Recovery package selection is invalid.")
    slot = _plain_path(recovery_root / "slots" / slot_name)
    release, digest, _manifest, archive_bytes = _verified_inputs(slot / MANIFEST_ASSET, slot / SOURCE_ASSET)
    if slot_name != f"{release}-{digest}":
        raise ValueError("Recovery slot does not match its signed contents.")
    restored = []
    with tempfile.TemporaryDirectory(prefix="xider-recovery-") as temporary:
        prepared = _prepare_archive(archive_bytes, Path(temporary), files)
        for name, target in targets.items():
            if target.exists() and _read_bounded(target, MAX_ARCHIVE_BYTES) != (prepared / name).read_bytes():
                raise ValueError(f"Installed file differs from the selected recovery release: {name}")
        if before_restore:
            before_restore()
        install_dir.mkdir(parents=True, exist_ok=True)
        for name in missing:
            if before_restore:
                before_restore()
            target = _plain_path(install_dir / name)
            # A hard link from a flushed file on this same volume creates the
            # name atomically and fails if another installer already created it.
            descriptor, scratch = tempfile.mkstemp(prefix=f".{name}.recovery-", dir=install_dir)
            scratch_path = Path(scratch)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write((prepared / name).read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                scratch_path.chmod(0o755 if name.endswith(".sh") else 0o644)
                try:
                    os.link(scratch_path, target)
                except FileExistsError:
                    continue
                _flush_directory(install_dir)
                restored.append(name)
            finally:
                scratch_path.unlink(missing_ok=True)
    return restored
