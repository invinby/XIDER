#!/usr/bin/env python3
"""Verify the signed inventory entry for a server source ZIP."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from pathlib import Path


try:
    # Production installs this verifier and its trust-ring module together in
    # /usr/local/libexec/xider. The fallback is only for repository tests.
    from release_signature import verify_manifest_signature
except ImportError:  # pragma: no cover - exercised by repository test imports
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "XGENT-MCS"))
    from release_signature import verify_manifest_signature


SERVER_ASSET = "XIDER-source.zip"
MAX_SERVER_BUNDLE_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def verify_server_bundle(
    manifest_path: Path,
    bundle_path: Path,
    *,
    trusted_keys: dict[str, bytes] | None = None,
) -> str:
    """Verify publisher signature, release inventory, bundle size and digest."""
    manifest_path = Path(manifest_path)
    bundle_path = Path(bundle_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Signed release manifest is missing or malformed.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Signed release manifest must be a JSON object.")

    # The installed root-owned verifier imports the root-owned trust ring; tests
    # may provide a generated key without adding a production override.
    if trusted_keys is None:
        key_id = verify_manifest_signature(manifest)
    else:
        key_id = verify_manifest_signature(manifest, trusted_keys)

    release = manifest.get("release")
    assets = manifest.get("assets")
    if (
        type(manifest.get("schema")) is not int
        or manifest.get("schema") != 1
        or not isinstance(release, str)
        or not _RELEASE.fullmatch(release)
        or not isinstance(assets, list)
    ):
        raise ValueError("Release manifest schema or release label is invalid.")

    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == SERVER_ASSET]
    if len(matches) != 1:
        raise ValueError("Release manifest must contain exactly one XIDER-source.zip entry.")
    asset = matches[0]
    size = asset.get("size")
    digest = asset.get("sha256")
    if (
        asset.get("component") != "source"
        or asset.get("platform") != "all"
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= MAX_SERVER_BUNDLE_BYTES
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise ValueError("XIDER-source.zip manifest entry is invalid.")

    if not bundle_path.is_file() or bundle_path.stat().st_size != size:
        raise ValueError("Server bundle size does not match its signed manifest.")
    actual = hashlib.sha256()
    with bundle_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(block)
    if not hmac.compare_digest(actual.hexdigest(), digest):
        raise ValueError("Server bundle SHA-256 does not match its signed manifest.")
    return release


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: verify_server_bundle.py RELEASE-MANIFEST.json XIDER-source.zip", file=sys.stderr)
        return 2
    try:
        release = verify_server_bundle(Path(args[0]), Path(args[1]))
    except Exception as exc:
        print(f"Server bundle rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Signed server bundle verified: {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
