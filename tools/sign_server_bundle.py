"""Sign a locally built XIDER source bundle without copying the key into it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNATURE_DIR = ROOT / "XGENT-MCS"
if str(SIGNATURE_DIR) not in sys.path:
    sys.path.insert(0, str(SIGNATURE_DIR))

from release_signature import TRUSTED_RELEASE_KEYS, sign_manifest


ASSET_NAME = "XIDER-source.zip"
RELEASE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(bundle: Path, release: str, private_key_pem: bytes) -> dict:
    bundle = Path(bundle).resolve()
    if not bundle.is_file() or bundle.stat().st_size <= 0:
        raise ValueError("Server source bundle is missing or empty.")
    if not isinstance(release, str) or not RELEASE_LABEL.fullmatch(release):
        raise ValueError("Release label contains unsupported characters.")
    manifest = {
        "schema": 1,
        "release": release,
        "assets": [{
            "name": ASSET_NAME,
            "component": "source",
            "platform": "all",
            "size": bundle.stat().st_size,
            "sha256": _sha256(bundle),
        }],
    }
    signed = sign_manifest(manifest, private_key_pem)
    if signed["signature"]["key_id"] not in TRUSTED_RELEASE_KEYS:
        raise ValueError("Local signing key is not pinned in the repository trust ring.")
    return signed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True, help="Protected local PEM path; never pass key contents.")
    parser.add_argument("--release", default=f"local-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    args = parser.parse_args(argv)
    try:
        key_path = args.key.resolve(strict=True)
        if key_path.is_relative_to(ROOT):
            raise ValueError("Release signing key must be outside the repository.")
        manifest_path = args.manifest.resolve()
        bundle_path = args.bundle.resolve(strict=True)
        if manifest_path in {key_path, bundle_path}:
            raise ValueError("Manifest output must be separate from the key and source bundle.")
        private_key = key_path.read_bytes()
        signed = build_manifest(bundle_path, args.release, private_key)
        payload = json.dumps(signed, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        temp = manifest_path.with_name(manifest_path.name + f".{os.getpid()}.tmp")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            temp.replace(manifest_path)
        finally:
            temp.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(f"Signed release inventory created: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
