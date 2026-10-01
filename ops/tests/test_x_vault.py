import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.x_vault import (
    VaultError,
    _derive_key,
    _encrypt,
    create_backup,
    create_recipient_keypair,
    main,
    restore_to_stage,
    verify_backup,
)


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


def test_cli_reads_passphrase_from_stdin_without_environment(monkeypatch, tmp_path, capsys):
    archive = create_backup(_source(tmp_path / "source"), tmp_path / "vault", PASSPHRASE)
    monkeypatch.delenv("XIDER_VAULT_PASSPHRASE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSPHRASE + "\n"))

    assert main(["verify", "--archive", str(archive), "--passphrase-stdin"]) == 0
    output = capsys.readouterr().out
    assert '"files": 2' in output
    assert PASSPHRASE not in output


def test_cli_rejects_missing_stdin_passphrase(monkeypatch, tmp_path):
    archive = create_backup(_source(tmp_path / "source"), tmp_path / "vault", PASSPHRASE)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert main(["verify", "--archive", str(archive), "--passphrase-stdin"]) == 1


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


def test_recipient_encryption_needs_no_secret_on_backup_host_and_restores(tmp_path):
    keys = create_recipient_keypair(tmp_path / "offline-vault-key")
    source = _source(tmp_path / "source")
    archive = create_backup(
        source,
        tmp_path / "vault",
        recipient_public_key_path=Path(keys["public_key_path"]),
    )

    encrypted = archive.read_bytes()
    assert encrypted.startswith(b"XVAULT2\0")
    assert b"secret-test-marker" not in encrypted
    verified = verify_backup(
        archive,
        recipient_private_key_path=Path(keys["private_key_path"]),
    )
    assert verified["files"] == 2
    assert verified["manifest"]["format"] == "X-VAULT/2"
    assert verified["manifest"]["encryption"] == "X25519 + HKDF-SHA256 + AES-256-GCM"

    stage = tmp_path / "stage"
    result = restore_to_stage(
        archive,
        stage,
        recipient_private_key_path=Path(keys["private_key_path"]),
    )
    assert result["restored_files"] == 2
    assert (stage / ".env").read_text(encoding="utf-8") == "BOT_TOKEN=secret-test-marker\n"


def test_recipient_archive_refuses_a_different_private_key(tmp_path):
    first = create_recipient_keypair(tmp_path / "offline-key-1")
    second = create_recipient_keypair(tmp_path / "offline-key-2")
    archive = create_backup(
        _source(tmp_path / "source"),
        tmp_path / "vault",
        recipient_public_key_path=Path(first["public_key_path"]),
    )

    with pytest.raises(VaultError, match="wrong recovery key"):
        verify_backup(archive, recipient_private_key_path=Path(second["private_key_path"]))


def test_recipient_keygen_refuses_repository_path_and_never_prints_private_material(tmp_path, capsys):
    with pytest.raises(VaultError, match="outside the repository"):
        create_recipient_keypair(ROOT / "ops" / "tests" / "should-not-exist")

    destination = tmp_path / "owner-only-vault-key"
    assert main(["keygen", "--directory", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "fingerprint=" in output
    assert "private_key_file=" in output
    assert "PRIVATE KEY" not in output
    private_key = (destination / "vault-recipient-private.pem").read_text(encoding="ascii")
    assert "PRIVATE KEY" in private_key
    if os.name != "nt":
        assert destination.stat().st_mode & 0o077 == 0
        assert (destination / "vault-recipient-private.pem").stat().st_mode & 0o077 == 0


def test_recipient_backup_verify_and_restore_cli(tmp_path, monkeypatch, capsys):
    keys = create_recipient_keypair(tmp_path / "offline-key")
    source = _source(tmp_path / "source")
    vault = tmp_path / "vault"
    monkeypatch.delenv("XIDER_VAULT_PASSPHRASE", raising=False)

    assert main([
        "backup", "--source", str(source), "--vault", str(vault),
        "--recipient-public-key", keys["public_key_path"],
    ]) == 0
    backup_output = capsys.readouterr().out
    archive = Path(backup_output.strip().split(": ", 1)[1])

    assert main([
        "verify", "--archive", str(archive), "--private-key", keys["private_key_path"],
    ]) == 0
    assert '"format": "X-VAULT/2"' in capsys.readouterr().out

    stage = tmp_path / "cli-stage"
    assert main([
        "restore-stage", "--archive", str(archive), "--stage", str(stage),
        "--private-key", keys["private_key_path"],
    ]) == 0
    assert (stage / "bot.py").read_text(encoding="utf-8") == "safe source\n"
