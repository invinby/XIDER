import json
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import sign_server_bundle as signer
from release_signature import release_key_id

sys.path.insert(0, str(signer.ROOT / "deploy"))
from verify_server_bundle import verify_server_bundle


def _keypair():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return pem, {release_key_id(public): public}


def test_build_manifest_signs_exact_server_bundle(tmp_path, monkeypatch):
    pem, trusted = _keypair()
    monkeypatch.setattr(signer, "TRUSTED_RELEASE_KEYS", trusted)
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"source artifact")

    manifest = signer.build_manifest(bundle, "local-test", pem)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert verify_server_bundle(manifest_path, bundle, trusted_keys=trusted) == "local-test"
    assert manifest["assets"][0]["size"] == len(b"source artifact")


def test_cli_reads_private_key_from_file_and_rejects_key_inside_repo(tmp_path, monkeypatch, capsys):
    pem, trusted = _keypair()
    monkeypatch.setattr(signer, "TRUSTED_RELEASE_KEYS", trusted)
    monkeypatch.setattr(signer, "ROOT", tmp_path / "repo")
    bundle = tmp_path / "bundle.zip"
    key = tmp_path / "signing-key.pem"
    manifest = tmp_path / "release-manifest.json"
    bundle.write_bytes(b"source artifact")
    key.write_bytes(pem)

    assert signer.main([
        "--bundle", str(bundle), "--manifest", str(manifest),
        "--key", str(key), "--release", "local-cli-test",
    ]) == 0
    assert "PRIVATE KEY" not in capsys.readouterr().out
    assert verify_server_bundle(manifest, bundle, trusted_keys=trusted) == "local-cli-test"

    repo_key = tmp_path / "repo" / "signing-key.pem"
    repo_key.parent.mkdir()
    repo_key.write_bytes(pem)
    try:
        signer.main([
            "--bundle", str(bundle), "--manifest", str(tmp_path / "blocked.json"),
            "--key", str(repo_key),
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Signing helper must reject a private key inside the checkout.")
    assert not (tmp_path / "blocked.json").exists()
