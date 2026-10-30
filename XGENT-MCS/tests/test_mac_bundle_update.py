from __future__ import annotations

import os
from pathlib import Path
import sys
import stat
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mac_bundle_update as bundle_update


def _bundle(path: Path, *, marker: str = "new") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for app, executable in bundle_update.APP_EXECUTABLES.items():
            archive.writestr(f"{app}/Contents/Info.plist", f"{marker}-{app}")
            archive.writestr(f"{app}/{executable}", f"{marker}-{app}-binary")


def _installed(root: Path, marker: str = "old") -> None:
    for app, executable in bundle_update.APP_EXECUTABLES.items():
        app_root = root / app
        binary = app_root / executable
        binary.parent.mkdir(parents=True, exist_ok=True)
        (app_root / "Contents/Info.plist").write_text(f"{marker}-{app}", encoding="utf-8")
        binary.write_text(f"{marker}-{app}-binary", encoding="utf-8")


def test_mac_bundle_updates_all_three_signed_apps_and_keeps_old_set(tmp_path, monkeypatch):
    archive = tmp_path / "signed.zip"
    _bundle(archive)
    install = tmp_path / "install"
    install.mkdir()
    _installed(install)
    monkeypatch.setattr(bundle_update, "_verify_signed_app", lambda _path: None)

    backup = bundle_update.install_signed_bundle(archive, install, "a" * 32)

    assert backup.is_dir()
    for app, executable in bundle_update.APP_EXECUTABLES.items():
        assert (install / app / executable).read_text(encoding="utf-8") == f"new-{app}-binary"
        assert (backup / app / executable).read_text(encoding="utf-8") == f"old-{app}-binary"


def test_mac_bundle_rejects_zip_slip_before_touching_installed_apps(tmp_path, monkeypatch):
    archive = tmp_path / "bad.zip"
    _bundle(archive)
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("XIDER.app/../../outside", "bad")
    install = tmp_path / "install"
    install.mkdir()
    _installed(install)
    monkeypatch.setattr(bundle_update, "_verify_signed_app", lambda _path: None)

    with pytest.raises(ValueError, match="unsafe macOS bundle member"):
        bundle_update.install_signed_bundle(archive, install, "b" * 32)

    assert (tmp_path / "outside").exists() is False
    assert (install / "XIDER.app/Contents/MacOS/XGENT-MCS").read_text(encoding="utf-8") == "old-XIDER.app-binary"


def test_mac_bundle_allows_internal_app_symlinks(tmp_path):
    archive_path = tmp_path / "signed-with-framework-link.zip"
    _bundle(archive_path)
    entry = zipfile.ZipInfo("XIDER.app/Contents/Frameworks/Example.framework/Versions/Current")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr(entry, "A")

    with zipfile.ZipFile(archive_path) as archive:
        members = bundle_update._validated_members(archive)

    assert any(member.filename.endswith("/Versions/Current") for member in members)


def test_mac_bundle_rejects_symlink_escape_from_app_root(tmp_path):
    archive_path = tmp_path / "symlink-escape.zip"
    _bundle(archive_path)
    entry = zipfile.ZipInfo("XIDER.app/Contents/Frameworks/escape")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr(entry, "../../../../../../outside")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="unsafe macOS bundle symlink"):
            bundle_update._validated_members(archive)


def test_mac_bundle_verifies_every_app_before_replacing_any(tmp_path, monkeypatch):
    archive = tmp_path / "signed.zip"
    _bundle(archive)
    install = tmp_path / "install"
    install.mkdir()
    _installed(install)
    verified = []

    def verify(app_path):
        verified.append(app_path.name)
        if app_path.name == "XIDER-Guard-Keeper.app":
            raise RuntimeError("invalid code signature")

    monkeypatch.setattr(bundle_update, "_verify_signed_app", verify)

    with pytest.raises(RuntimeError, match="invalid code signature"):
        bundle_update.install_signed_bundle(archive, install, "c" * 32)

    assert verified == list(bundle_update.APP_EXECUTABLES)[:2]
    for app, executable in bundle_update.APP_EXECUTABLES.items():
        assert (install / app / executable).read_text(encoding="utf-8") == f"old-{app}-binary"


def test_mac_bundle_rolls_back_partial_app_swap_on_filesystem_error(tmp_path, monkeypatch):
    archive = tmp_path / "signed.zip"
    _bundle(archive)
    install = tmp_path / "install"
    install.mkdir()
    _installed(install)
    monkeypatch.setattr(bundle_update, "_verify_signed_app", lambda _path: None)
    real_replace = os.replace
    failed = False

    def replace(source, destination):
        nonlocal failed
        if (not failed and Path(source).name == "XIDER-Guard-Keeper.app"
                and Path(source).parent.name.startswith(".xider-update-")):
            failed = True
            raise OSError("simulated second app swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr(bundle_update.os, "replace", replace)

    with pytest.raises(OSError, match="simulated second app swap failure"):
        bundle_update.install_signed_bundle(archive, install, "d" * 32)

    for app, executable in bundle_update.APP_EXECUTABLES.items():
        assert (install / app / executable).read_text(encoding="utf-8") == f"old-{app}-binary"


def test_mac_bundle_can_restore_previous_signed_app_set(tmp_path, monkeypatch):
    archive = tmp_path / "signed.zip"
    _bundle(archive)
    install = tmp_path / "install"
    install.mkdir()
    _installed(install)
    monkeypatch.setattr(bundle_update, "_verify_signed_app", lambda _path: None)
    backup = bundle_update.install_signed_bundle(archive, install, "e" * 32)

    failed = bundle_update.restore_backup_set(backup, install, "e" * 32)

    for app, executable in bundle_update.APP_EXECUTABLES.items():
        assert (install / app / executable).read_text(encoding="utf-8") == f"old-{app}-binary"
        assert (failed / app / executable).read_text(encoding="utf-8") == f"new-{app}-binary"


def test_prune_bundle_backups_keeps_latest_two_and_current_and_ignores_unknown(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    operation_ids = ["1" * 32, "2" * 32, "3" * 32, "4" * 32]
    roots = []
    for index, operation_id in enumerate(operation_ids):
        root = install / f".xider-update-{operation_id}.backup"
        root.mkdir()
        for app in bundle_update.APP_EXECUTABLES:
            (root / app).mkdir()
        os.utime(root, ns=(index + 1, index + 1))
        roots.append(root)
    unknown = install / ".xider-update-not-an-operation.backup"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("owner data", encoding="utf-8")

    removed = bundle_update.prune_bundle_backups(install, current_backup=roots[0], keep=2)

    assert removed == 1
    assert roots[0].is_dir()
    assert not roots[1].exists()
    assert roots[2].is_dir() and roots[3].is_dir()
    assert (unknown / "keep.txt").read_text(encoding="utf-8") == "owner data"
