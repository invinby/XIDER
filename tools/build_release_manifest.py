"""Build a deterministic SHA-256 inventory for a GitHub Release upload.

The manifest records bytes, not publisher authenticity.  A future installer
must authenticate the manifest before trusting it for unattended updates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


COMPONENTS = {
    "XIDER-source.zip": ("source", "all"),
    "XGENT-WDS.exe": ("windows_agent", "windows"),
    "XGENT-MCS-macos-bundle.zip": ("mac_agent", "macos"),
    "Guard-Keeper-Windows.zip": ("windows_keeper", "windows"),
    "Guard-Keeper-macOS.zip": ("mac_keeper", "macos"),
    "X-STAB-server.zip": ("server", "linux"),
}
TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def build(tag: str, directory: Path) -> dict:
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
    return {"schema": 1, "release": tag, "assets": assets}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    manifest = build(args.tag, args.directory)
    destination = args.directory / "release-manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
