"""Build and verify a signed inventory for a manually requested XIDER build.

This is an artifact inventory, not an obfuscator. Nuitka-built binaries are
reported as compiled/source-free; no obfuscation is claimed or performed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "XGENT-MCS"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from release_signature import TRUSTED_RELEASE_KEYS, sign_manifest, verify_manifest_signature


TAG = re.compile(r"^v\d+\.\d+\.\d+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
OUTPUT_NAME = "protected-build-manifest.json"
SOURCE_SUFFIXES = {".py", ".ps1", ".psm1", ".bat", ".cmd", ".sh"}
EXPECTED_ARCHIVES = {
    "XIDER-source.zip",
    "XGENT-WDS-Windows.zip",
    "XGENT-MCS-macos-bundle.zip",
    "Guard-Keeper-Windows.zip",
    "Guard-Keeper-macOS.zip",
    "X-STAB-server.zip",
}
COMPONENT_OUTPUTS = (
    ("windows_agent", "windows", "XGENT-WDS-Windows.zip", "XGENT-WDS.exe"),
    ("windows_guard_keeper", "windows", "Guard-Keeper-Windows.zip", "XIDER-Guard-Keeper.exe"),
    ("macos_agent", "macos", "XGENT-MCS-macos-bundle.zip", "XIDER.app/Contents/MacOS/XGENT-MCS"),
    ("macos_guard_keeper", "macos", "Guard-Keeper-macOS.zip", "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper"),
    ("safari", "macos", "XGENT-MCS-macos-bundle.zip", "XIDER-Layers.app/Contents/MacOS/XIDER-Layers"),
    ("siri", "macos", "XGENT-MCS-macos-bundle.zip", "XIDER-Layers.app/Contents/MacOS/XIDER-Layers"),
)


def _hash_stream(stream) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        digest.update(block)
    return size, digest.hexdigest()


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    has_drive_prefix = bool(re.match(r"^[A-Za-z]:", name))
    return (bool(name) and not path.is_absolute() and not has_drive_prefix
            and ".." not in path.parts and "\\" not in name and "\x00" not in name)


def build_manifest(
    directory: Path, *, source_ref: str, source_commit: str, allow_missing_platforms: bool = False,
) -> dict:
    if not isinstance(source_ref, str) or not TAG.fullmatch(source_ref):
        raise ValueError("source_ref must be a published semantic-version tag (vMAJOR.MINOR.PATCH)")
    if not isinstance(source_commit, str) or not COMMIT.fullmatch(source_commit):
        raise ValueError("source_commit must be a full lowercase Git commit SHA")
    directory = Path(directory)
    archives = sorted(path for path in directory.glob("*.zip") if path.is_file())
    names = {path.name for path in archives}
    required_archives = EXPECTED_ARCHIVES - (
        {"XGENT-MCS-macos-bundle.zip", "Guard-Keeper-macOS.zip"}
        if allow_missing_platforms else set()
    )
    if not required_archives <= names or names - EXPECTED_ARCHIVES:
        missing = sorted(required_archives - names)
        unexpected = sorted(names - EXPECTED_ARCHIVES)
        raise ValueError(f"Build archive set mismatch; missing={missing}, unexpected={unexpected}")

    assets = []
    for archive in archives:
        members = []
        seen = set()
        with zipfile.ZipFile(archive) as package:
            for info in sorted(package.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                if not _safe_member_name(info.filename) or info.filename in seen:
                    raise ValueError(f"Unsafe or duplicate archive member in {archive.name}: {info.filename}")
                seen.add(info.filename)
                with package.open(info, "r") as member_stream:
                    member_size, member_digest = _hash_stream(member_stream)
                members.append({
                    "path": info.filename,
                    "size": member_size,
                    "sha256": member_digest,
                })
        if not members:
            raise ValueError(f"Build archive is empty: {archive.name}")
        with archive.open("rb") as archive_stream:
            archive_size, archive_digest = _hash_stream(archive_stream)
        assets.append({
            "name": archive.name,
            "build_kind": (
                "source_bundle" if archive.name in {"XIDER-source.zip", "X-STAB-server.zip"}
                or any(Path(member["path"]).suffix.lower() in SOURCE_SUFFIXES for member in members)
                else "compiled_source_free_package"
            ),
            "size": archive_size,
            "sha256": archive_digest,
            "members": members,
        })

    assets_by_name = {asset["name"]: asset for asset in assets}
    components = []
    for component_id, platform, asset_name, entrypoint in COMPONENT_OUTPUTS:
        asset = assets_by_name.get(asset_name)
        if asset is None:
            components.append({
                "id": component_id,
                "platform": platform,
                "version": source_ref,
                "package_asset": None,
                "entrypoint": None,
                "build_mode": "not_built",
                "compiler": None,
                "source_included": None,
                "obfuscation": {
                    "state": "not_applied",
                    "tool": None,
                    "version": None,
                    "configuration": None,
                    "reason": "This preview release contains no native package for this platform component.",
                },
                "output_sha256": None,
                "output_verification": "not_available_no_package",
            })
            continue
        member = next((row for row in asset["members"] if row["path"] == entrypoint), None)
        if member is None:
            raise ValueError(f"Required {component_id} entrypoint is missing from {asset_name}: {entrypoint}")
        source_included = any(Path(row["path"]).suffix.lower() in SOURCE_SUFFIXES for row in asset["members"])
        if source_included:
            raise ValueError(f"Compiled component archive unexpectedly includes plaintext source: {asset_name}")
        components.append({
            "id": component_id,
            "platform": platform,
            "version": source_ref,
            "package_asset": asset_name,
            "entrypoint": entrypoint,
            "build_mode": "nuitka_compiled",
            "compiler": "Nuitka 4.3.1",
            "source_included": source_included,
            "obfuscation": {
                "state": "not_applied",
                "tool": None,
                "version": None,
                "configuration": None,
                "reason": "No reproducible supported obfuscation step is configured; compilation is not obfuscation.",
            },
            "output_sha256": member["sha256"] if member else None,
            "output_verification": "sha256_in_signed_member_inventory",
        })

    return {
        "schema": 1,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "build_kind": "artifact_inventory",
        "obfuscation": {
            "state": "not_applied",
            "reason": "No reproducible supported obfuscation step is configured; compilation is not obfuscation.",
        },
        "components": components,
        "platform_signing": {
            "windows": "not_claimed",
            "macos": (
                "source_preview_no_native_package"
                if "XGENT-MCS-macos-bundle.zip" not in assets_by_name else "unsigned_preview"
            ),
            "inventory": "signed_and_verified",
        },
        "assets": assets,
    }


def sign_and_verify(manifest: dict, private_key_pem: bytes, trusted_keys=None) -> dict:
    signed = sign_manifest(manifest, private_key_pem)
    key_id = signed.get("signature", {}).get("key_id")
    keyring = TRUSTED_RELEASE_KEYS if trusted_keys is None else trusted_keys
    if key_id not in keyring:
        raise ValueError("Release signing key is not pinned by the agents.")
    verify_manifest_signature(signed, keyring)
    return signed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--require-signature", action="store_true")
    parser.add_argument(
        "--allow-missing-platforms", action="store_true",
        help="Allow the two macOS app archives to be absent and record each component as not_built.",
    )
    args = parser.parse_args()

    manifest = build_manifest(
        args.directory, source_ref=args.source_ref, source_commit=args.source_commit,
        allow_missing_platforms=args.allow_missing_platforms,
    )
    if args.require_signature:
        encoded = os.environ.get("XIDER_RELEASE_PRIVATE_KEY_B64", "")
        if not encoded:
            parser.error("XIDER_RELEASE_PRIVATE_KEY_B64 is required to sign this build inventory.")
        try:
            manifest = sign_and_verify(manifest, base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError) as exc:
            parser.error(str(exc))
    else:
        parser.error("Refusing to write an unsigned protected-build inventory.")

    output = args.directory / OUTPUT_NAME
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
