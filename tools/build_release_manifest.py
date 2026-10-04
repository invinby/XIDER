"""Build a deterministic, optionally Ed25519-signed GitHub Release inventory.

Tagged release workflows require a signature from a key pinned in the agent's
trust ring. Plain unsigned manifests are useful only for local inventory tests
and are rejected by agents.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path


COMPONENTS = {
    "XIDER-source.zip": ("source", "all"),
    "XGENT-WDS.exe": ("windows_agent", "windows"),
    "XGENT-MCS-macos-bundle.zip": ("mac_agent", "macos"),
    "Guard-Keeper-Windows.zip": ("windows_keeper", "windows"),
    "Guard-Keeper-macOS.zip": ("mac_keeper", "macos"),
    "X-STAB-server.zip": ("server", "linux"),
    "XIDER-bootstrap-windows.ps1": ("bootstrap", "windows"),
    "XIDER-bootstrap-macos.sh": ("bootstrap", "macos"),
    "XIDER-bootstrap-windows.ps1.sig": ("bootstrap_signature", "windows"),
    "XIDER-bootstrap-macos.sh.sig": ("bootstrap_signature", "macos"),
    "XIDER-QUICKSTART.txt": ("quickstart", "all"),
    "XIDER-install-macos.sh": ("installer", "macos"),
    "XIDER-install-windows.ps1": ("installer", "windows"),
    "XIDER-install-macos.sh.sig": ("installer_signature", "macos"),
    "XIDER-install-windows.ps1.sig": ("installer_signature", "windows"),
}
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
ROOT = Path(__file__).resolve().parents[1]
MAC_AGENT_DIR = ROOT / "XGENT-MCS"
if str(MAC_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(MAC_AGENT_DIR))

from release_signature import TRUSTED_RELEASE_KEYS, release_key_id, sign_manifest


def build(tag: str, directory: Path, *, signing_key_pem: bytes | None = None) -> dict:
    if not TAG.fullmatch(tag):
        raise ValueError("tag must be vMAJOR.MINOR.PATCH")
    assets = []
    for name, (component, platform) in COMPONENTS.items():
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        assets.append({
            "name": name,
            "component": component,
            "platform": platform,
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
        })
    manifest = {"schema": 1, "release": tag, "assets": assets}
    return sign_manifest(manifest, signing_key_pem) if signing_key_pem is not None else manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="Require a pinned Ed25519 key from XIDER_RELEASE_PRIVATE_KEY_B64.",
    )
    args = parser.parse_args()
    signing_key_pem = None
    if args.require_signature:
        encoded_key = os.environ.get("XIDER_RELEASE_PRIVATE_KEY_B64", "")
        if not encoded_key:
            parser.error("XIDER_RELEASE_PRIVATE_KEY_B64 is required for signed releases.")
        try:
            signing_key_pem = base64.b64decode(encoded_key, validate=True)
            manifest = build(args.tag, args.directory, signing_key_pem=signing_key_pem)
        except (ValueError, TypeError) as exc:
            parser.error(str(exc))
        key_id = manifest["signature"]["key_id"]
        if key_id not in TRUSTED_RELEASE_KEYS:
            parser.error("Signing key does not match a public key pinned in XGENT-MCS/release_signature.py.")
    else:
        manifest = build(args.tag, args.directory)
    destination = args.directory / "release-manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
