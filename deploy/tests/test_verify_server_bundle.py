import hashlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_server_bundle import verify_server_bundle
from release_signature import sign_manifest


def _keypair():
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    import hashlib
    key_id = hashlib.sha256(public).hexdigest()[:16]
    return private_pem, {key_id: public}


def _write_pair(tmp_path, *, bundle_bytes=b"server source", change=None, signed=True):
    private_pem, trusted_keys = _keypair()
    bundle = tmp_path / "source.zip"
    bundle.write_bytes(bundle_bytes)
    entry = {
        "name": "XIDER-source.zip", "component": "source", "platform": "all",
        "size": len(bundle_bytes), "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
    }
    manifest = {"schema": 1, "release": "local-test", "assets": [entry]}
    if change:
        change(manifest)
    if signed:
        manifest = sign_manifest(manifest, private_pem)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, bundle, trusted_keys


def test_verifies_signed_server_bundle_inventory(tmp_path):
    manifest, bundle, trusted_keys = _write_pair(tmp_path)
    assert verify_server_bundle(manifest, bundle, trusted_keys=trusted_keys) == "local-test"


def test_verifies_server_bundle_when_manifest_also_lists_bootstrap_assets(tmp_path):
    def add_bootstrap_assets(manifest):
        for name, component, platform in (
            ("XIDER-bootstrap-windows.ps1", "bootstrap", "windows"),
            ("XIDER-bootstrap-macos.sh", "bootstrap", "macos"),
            ("XIDER-QUICKSTART.txt", "quickstart", "all"),
        ):
            manifest["assets"].append({
                "name": name,
                "component": component,
                "platform": platform,
                "size": 1,
                "sha256": "0" * 64,
            })

    manifest, bundle, trusted_keys = _write_pair(tmp_path, change=add_bootstrap_assets)
    assert verify_server_bundle(manifest, bundle, trusted_keys=trusted_keys) == "local-test"


@pytest.mark.parametrize("failure", ["tampered", "unsigned", "wrong_component", "duplicate_asset", "wrong_size"])
def test_rejects_untrusted_or_malformed_server_bundle(tmp_path, failure):
    change = None
    signed = True
    if failure == "unsigned":
        signed = False
    elif failure == "wrong_component":
        change = lambda manifest: manifest["assets"][0].update(component="windows_agent")
    elif failure == "duplicate_asset":
        change = lambda manifest: manifest["assets"].append(dict(manifest["assets"][0]))
    elif failure == "wrong_size":
        change = lambda manifest: manifest["assets"][0].update(size=999)
    manifest, bundle, trusted_keys = _write_pair(tmp_path, change=change, signed=signed)
    if failure == "tampered":
        bundle.write_bytes(b"modified source")

    with pytest.raises(ValueError):
        verify_server_bundle(manifest, bundle, trusted_keys=trusted_keys)


def test_rejects_unknown_publisher_key(tmp_path):
    manifest, bundle, _trusted_keys = _write_pair(tmp_path)
    with pytest.raises(ValueError, match="unknown publisher key"):
        verify_server_bundle(manifest, bundle, trusted_keys={})
