import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.x_vault import VaultError, _derive_key, _encrypt, create_backup, restore_to_stage, verify_backup


PASSPHRASE = "test-only-vault-passphrase-0123456789"


def _source(path):
    path.mkdir()
    (path / "bot.py").write_text("safe source\n", encoding="utf-8")
    (path / ".env").write_text("BOT_TOKEN=secret-test-marker\n", encoding="utf-8")
    (path / "venv").mkdir()
    (path / "venv" / "ignored.txt").write_text("not backed up\n", encoding="utf-8")
    return path


def test_backup_is_authenticated_encrypted_and_restores_to_stage(tmp_path):
    source = _source(tmp_path / "source")
    archive = create_backup(source, tmp_path / "vault", PASSPHRASE)

    raw = archive.read_bytes()
    assert b"secret-test-marker" not in raw
    assert verify_backup(archive, PASSPHRASE)["files"] == 2

    stage = tmp_path / "stage"
    result = restore_to_stage(archive, stage, PASSPHRASE)
    assert result["restored_files"] == 2
    assert (stage / "bot.py").read_text(encoding="utf-8") == "safe source\n"
    assert (stage / ".env").read_text(encoding="utf-8") == "BOT_TOKEN=secret-test-marker\n"
    assert not (stage / "venv").exists()


def test_wrong_passphrase_is_rejected(tmp_path):
    archive = create_backup(_source(tmp_path / "source"), tmp_path / "vault", PASSPHRASE)

    with pytest.raises(VaultError, match="Authentication failed"):
        verify_backup(archive, "different-passphrase")


def test_short_passphrase_is_rejected():
    with pytest.raises(VaultError, match="at least 16 characters"):
        _derive_key("too-short", b"0123456789abcdef")


def test_external_secret_file_is_encrypted_and_restored_under_external(tmp_path):
    source = _source(tmp_path / "source")
    external = tmp_path / "bot.env"
    external.write_text("MQTT_PASSWORD=extra-secret-marker\n", encoding="utf-8")
    archive = create_backup(source, tmp_path / "vault", PASSPHRASE, (external,))

    assert b"extra-secret-marker" not in archive.read_bytes()
    stage = tmp_path / "stage"
    restore_to_stage(archive, stage, PASSPHRASE)
    assert (stage / "external" / "bot.env").read_text(encoding="utf-8") == "MQTT_PASSWORD=extra-secret-marker\n"


def test_restore_refuses_nonempty_stage(tmp_path):
    archive = create_backup(_source(tmp_path / "source"), tmp_path / "vault", PASSPHRASE)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "keep.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(VaultError, match="staging directory"):
        restore_to_stage(archive, stage, PASSPHRASE)
    assert (stage / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_archive_rejects_traversal_member(tmp_path):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("manifest.json", '{"format":"X-VAULT/1"}')
        archive.writestr("../outside.txt", "must not escape")
    sealed = tmp_path / "unsafe.xvlt"
    sealed.write_bytes(_encrypt(raw.getvalue(), PASSPHRASE))

    with pytest.raises(VaultError, match="Unsafe backup member"):
        verify_backup(sealed, PASSPHRASE)


def test_duplicate_external_filenames_are_rejected(tmp_path):
    source = _source(tmp_path / "source")
    first = tmp_path / "first" / "bot.env"
    second = tmp_path / "second" / "bot.env"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    with pytest.raises(VaultError, match="same filename"):
        create_backup(source, tmp_path / "vault", PASSPHRASE, (first, second))


def test_symlink_is_not_included(tmp_path):
    source = _source(tmp_path / "source")
    link = source / "outside-link"
    try:
        link.symlink_to(source / "bot.py")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available in this environment")

    archive = create_backup(source, tmp_path / "vault", PASSPHRASE)

    assert verify_backup(archive, PASSPHRASE)["files"] == 2
