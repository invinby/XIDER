"""Ed25519 signatures for XIDER release manifests.

Only owner-provisioned publisher public keys belong in this trust ring. An
unsigned or unknown-key release must fail closed; hashes alone only detect
accidental corruption.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# The matching private half must remain outside the repository and off agents.
# The tagged-release workflow may use a protected GitHub environment secret
# after the owner provisions it; local owner signing is separate from that CI
# secret and must never copy private material into source or release assets.
TRUSTED_RELEASE_KEYS: dict[str, bytes] = {
    "521c56c0c89f0ddd": bytes.fromhex(
        "670125c11ecd112c68ad86ccaf4f40594b30afa198fa5673d460446eb91d75c2"
    ),
}
SIGNATURE_ALGORITHM = "Ed25519"


def release_key_id(public_key: bytes) -> str:
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError("Ed25519 public key must be exactly 32 bytes.")
    return hashlib.sha256(public_key).hexdigest()[:16]


def trusted_release_keys_ready() -> bool:
    """Whether at least one correctly pinned Ed25519 public key is available."""
    return any(
        isinstance(key_id, str)
        and isinstance(public_key, bytes)
        and len(public_key) == 32
        and release_key_id(public_key) == key_id
        for key_id, public_key in TRUSTED_RELEASE_KEYS.items()
    )


def manifest_signing_bytes(manifest: Mapping[str, object]) -> bytes:
    if not isinstance(manifest, Mapping):
        raise ValueError("Release manifest must be an object.")
    payload = {key: value for key, value in manifest.items() if key != "signature"}
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Release manifest cannot be canonically encoded.") from exc


def sign_manifest(manifest: Mapping[str, object], private_key_pem: bytes) -> dict:
    """Sign a manifest for release tooling; clients only need its public key."""
    if "signature" in manifest:
        raise ValueError("Refusing to re-sign a manifest that already has a signature.")
    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("Release signing key is not an unencrypted PEM private key.") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Release signing key must be Ed25519.")

    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    signed = dict(manifest)
    signed["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": release_key_id(public_key),
        "value": base64.b64encode(private_key.sign(manifest_signing_bytes(manifest))).decode("ascii"),
    }
    return signed


def verify_manifest_signature(
    manifest: Mapping[str, object],
    trusted_keys: Mapping[str, bytes] | None = None,
) -> str:
    """Verify an Ed25519 signature against the pinned key ring; return key ID."""
    keyring = TRUSTED_RELEASE_KEYS if trusted_keys is None else trusted_keys
    signature = manifest.get("signature") if isinstance(manifest, Mapping) else None
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "key_id", "value"}:
        raise ValueError("Release manifest has no valid publisher signature.")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ValueError("Unsupported release signature algorithm.")

    key_id = signature.get("key_id")
    encoded_signature = signature.get("value")
    if not isinstance(key_id, str) or not isinstance(encoded_signature, str):
        raise ValueError("Malformed release signature fields.")
    public_bytes = keyring.get(key_id)
    if not isinstance(public_bytes, bytes):
        raise ValueError("Release signature uses an unknown publisher key.")
    if release_key_id(public_bytes) != key_id:
        raise ValueError("Pinned release public key ID does not match its key bytes.")
    try:
        raw_signature = base64.b64decode(encoded_signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Release signature is not valid base64.") from exc
    if len(raw_signature) != 64:
        raise ValueError("Ed25519 release signature must be exactly 64 bytes.")

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            raw_signature,
            manifest_signing_bytes(manifest),
        )
    except InvalidSignature as exc:
        raise ValueError("Release manifest signature verification failed.") from exc
    return key_id
