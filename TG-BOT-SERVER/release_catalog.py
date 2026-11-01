"""X-LEDGER: read-only catalogue of published XIDER GitHub Releases.

A Git tag or source archive is not an installable agent package.  This module
only marks a component available when its expected release asset exists.
Installation is intentionally handled by a separate, verified updater.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
import sys
from pathlib import Path
from dataclasses import dataclass, replace
from typing import Callable

# Server bundles carry this canonical module; never maintain a second verifier.
sys.path.append(str(Path(__file__).resolve().parents[1] / "XGENT-MCS"))
from release_signature import verify_manifest_signature, manifest_signing_bytes


RELEASES_URL = "https://api.github.com/repos/invinby/XIDER/releases"
_TAG = re.compile(r"^v([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})$")
_ASSET_NAMES = {
    "windows_agent": {"XGENT-WDS.exe", "XGENT-WDS-Windows.zip"},
    "mac_agent": {"XGENT-MCS-macos-bundle.zip"},
    "windows_keeper": {"Guard-Keeper-Windows.zip"},
    "mac_keeper": {"Guard-Keeper-macOS.zip"},
    "server": {"X-STAB-server.zip"},
}
_SOURCE_UPDATE_ASSETS = frozenset({"XIDER-source.zip", "release-manifest.json"})
_PROTECTED_BUILD_MANIFEST = "protected-build-manifest.json"
_BUILD_COMPONENTS = {
    "windows_agent": ("windows", "XGENT-WDS-Windows.zip", "XGENT-WDS.exe"),
    "windows_guard_keeper": ("windows", "Guard-Keeper-Windows.zip", "XIDER-Guard-Keeper.exe"),
    "macos_agent": ("macos", "XGENT-MCS-macos-bundle.zip", "XIDER.app/Contents/MacOS/XGENT-MCS"),
    "macos_guard_keeper": ("macos", "Guard-Keeper-macOS.zip", "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper"),
    "safari": ("macos", "XGENT-MCS-macos-bundle.zip", "XIDER-Layers.app/Contents/MacOS/XIDER-Layers"),
    "siri": ("macos", "XGENT-MCS-macos-bundle.zip", "XIDER-Layers.app/Contents/MacOS/XIDER-Layers"),
}
_BUILD_COMPONENT_FOR_RELEASE_COMPONENT = {
    "windows_agent": "windows_agent", "windows_keeper": "windows_guard_keeper",
    "mac_agent": "macos_agent", "mac_keeper": "macos_guard_keeper",
}


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, int, int]
    name: str
    published_at: str
    notes: str
    asset_names: frozenset[str]
    manifest: dict | None = None
    protected_manifest: dict | None = None
    prerelease: bool = False

    def signed_package(self, component: str) -> bool:
        return verify_release_metadata(self, self.manifest) and any(
            asset["component"] == component for asset in self.manifest["assets"]
        )

    def signed_asset(self, name: str, component: str) -> bool:
        return verify_release_metadata(self, self.manifest) and any(
            asset["name"] == name and asset["component"] == component
            for asset in self.manifest["assets"]
        )

    def has_package(self, component: str) -> bool:
        return bool(self.asset_names & _ASSET_NAMES.get(component, set()))

    def has_source_update_payload(self, component: str) -> bool:
        """Require both the platform bundle and the inputs used by source updater."""
        return self.has_package(component) and _SOURCE_UPDATE_ASSETS.issubset(self.asset_names)

    def signed_source_update_payload(self, component: str) -> bool:
        return self.has_source_update_payload(component) and self.signed_package(component) and any(
            asset["name"] == "XIDER-source.zip" and asset["component"] == "source"
            for asset in self.manifest["assets"]
        )

    def protected_component(self, component: str) -> dict | None:
        """Return only a component row from a signed report bound to this release."""
        rows = self.protected_components(component)
        return rows[0] if rows else None

    def protected_components(self, component: str) -> list[dict] | None:
        """Return signed build rows represented by a selectable release component."""
        component_id = _BUILD_COMPONENT_FOR_RELEASE_COMPONENT.get(component)
        manifest = self.protected_manifest
        if component_id is None or not verify_protected_build_metadata(self, manifest, self.manifest):
            return None
        component_ids = [component_id]
        if component == "mac_agent":
            component_ids.extend(("safari", "siri"))
        by_id = {row["id"]: row for row in manifest["components"]}
        return [by_id[item] for item in component_ids]


def component_for(os_name: str, keeper: bool = False) -> str | None:
    low = str(os_name or "").lower()
    if "mac" in low or "darwin" in low:
        return "mac_keeper" if keeper else "mac_agent"
    if "win" in low:
        return "windows_keeper" if keeper else "windows_agent"
    return None


_PLATFORMS = {"windows_agent": "windows", "mac_agent": "macos", "windows_keeper": "windows", "mac_keeper": "macos", "server": "linux"}


def verify_release_metadata(release: Release, manifest: dict) -> bool:
    """Verify publisher inventory, not downloaded artifact bytes (agent owns that)."""
    try:
        verify_manifest_signature(manifest)
        if manifest.get("schema") != 1 or manifest.get("release") != release.tag:
            return False
        assets = manifest.get("assets")
        if not isinstance(assets, list) or not assets or "release-manifest.json" not in release.asset_names:
            return False
        seen = set()
        for asset in assets:
            if not isinstance(asset, dict):
                return False
            name = asset.get("name")
            component = asset.get("component")
            if not isinstance(name, str) or name in seen or name not in release.asset_names:
                return False
            seen.add(name)
            if type(asset.get("size")) is not int or asset["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))):
                return False
            expected = next((key for key, names in _ASSET_NAMES.items() if name in names), None)
            if expected and (component != expected or asset.get("platform") != _PLATFORMS[expected]):
                return False
            if component in _PLATFORMS and (name not in _ASSET_NAMES[component] or asset.get("platform") != _PLATFORMS[component]):
                return False
            if name == "XIDER-source.zip" and (component != "source" or asset.get("platform") != "all"):
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def verify_protected_build_metadata(release: Release, protected: dict, release_manifest: dict | None = None) -> bool:
    """Validate component build claims and bind every package hash to the release inventory."""
    try:
        release_manifest = release_manifest if release_manifest is not None else release.manifest
        if not verify_release_metadata(release, release_manifest):
            return False
        verify_manifest_signature(protected)
        if (protected.get("schema") != 1 or protected.get("source_ref") != release.tag
                or not re.fullmatch(r"[0-9a-f]{40}", str(protected.get("source_commit", "")))
                or protected.get("build_kind") != "artifact_inventory"):
            return False
        platform_signing = protected.get("platform_signing")
        if (not isinstance(platform_signing, dict)
                or platform_signing.get("windows") not in {"not_claimed", "authenticode_signed"}
                or platform_signing.get("macos") not in {
                    "source_preview_no_native_package", "unsigned_preview", "apple_signed_notarized",
                }
                or platform_signing.get("inventory") != "signed_and_verified"):
            return False

        release_assets = {row["name"]: row for row in release_manifest["assets"]}
        assets = protected.get("assets")
        if not isinstance(assets, list) or not assets or len(assets) > 32:
            return False
        member_hashes_by_asset = {}
        for asset in assets:
            if not isinstance(asset, dict):
                return False
            name = asset.get("name")
            size = asset.get("size")
            digest = asset.get("sha256")
            published = release_assets.get(name) if isinstance(name, str) else None
            if (published is None or name in member_hashes_by_asset or not name.endswith(".zip")
                    or type(size) is not int or type(published.get("size")) is not int
                    or size != published["size"]
                    or not re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))
                    or digest != published.get("sha256")):
                return False
            members = asset.get("members")
            if not isinstance(members, list) or not members or len(members) > 20000:
                return False
            member_hashes = {}
            for member in members:
                if not isinstance(member, dict):
                    return False
                path = member.get("path")
                member_digest = member.get("sha256")
                member_size = member.get("size")
                if (not isinstance(path, str) or not path or len(path) > 500 or path in member_hashes
                        or type(member_size) is not int or member_size < 0
                        or not re.fullmatch(r"[0-9a-f]{64}", str(member_digest or ""))):
                    return False
                member_hashes[path] = member_digest
            member_hashes_by_asset[name] = member_hashes

        rows = protected.get("components")
        if not isinstance(rows, list) or len(rows) != len(_BUILD_COMPONENTS):
            return False
        rows_by_id = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in _BUILD_COMPONENTS or row["id"] in rows_by_id:
                return False
            rows_by_id[row["id"]] = row
        if set(rows_by_id) != set(_BUILD_COMPONENTS):
            return False

        for component_id, (platform, package_asset, entrypoint) in _BUILD_COMPONENTS.items():
            row = rows_by_id[component_id]
            mode = row.get("build_mode")
            if row.get("platform") != platform or row.get("version") != release.tag:
                return False
            if mode == "not_built":
                if (row.get("package_asset") is not None or row.get("entrypoint") is not None
                        or row.get("output_sha256") is not None or package_asset in member_hashes_by_asset):
                    return False
                continue
            if mode not in {"nuitka_compiled", "python_source"} or row.get("package_asset") != package_asset:
                return False
            if row.get("entrypoint") != entrypoint or package_asset not in member_hashes_by_asset:
                return False
            if row.get("source_included") is not (mode == "python_source"):
                return False
            digest = member_hashes_by_asset[package_asset].get(entrypoint)
            if digest is None or row.get("output_sha256") != digest:
                return False
            obfuscation = row.get("obfuscation")
            if not isinstance(obfuscation, dict) or obfuscation.get("state") not in {
                "obfuscated", "not_applied", "partial", "not_obfuscated",
            }:
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def version_state(current: str, release: Release, component: str, manifest: dict | None = None) -> dict:
    metadata = manifest if manifest is not None else release.manifest
    verified = verify_release_metadata(release, metadata)
    supported = release.has_package(component)
    available = verified and supported and any(asset["component"] == component for asset in metadata["assets"])
    known = bool(_TAG.fullmatch("v" + str(current or "").removeprefix("v")))
    return {"installed": current if known else None, "available": release.tag if available else None,
            "unsupported": not supported, "unknown": not known, "os": _PLATFORMS.get(component, "unknown"),
            "signature": "verified" if verified else "unknown", "published_at": release.published_at, "changelog": release.notes}


def _download_manifest(release: Release) -> dict:
    request = urllib.request.Request(
        f"https://github.com/invinby/XIDER/releases/download/{release.tag}/release-manifest.json",
        headers={"User-Agent": "XIDER-X-LEDGER"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Manifest too large")
    return json.loads(raw.decode("utf-8"))


def _download_protected_manifest(release: Release) -> dict:
    request = urllib.request.Request(
        f"https://github.com/invinby/XIDER/releases/download/{release.tag}/{_PROTECTED_BUILD_MANIFEST}",
        headers={"User-Agent": "XIDER-X-LEDGER"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Protected build manifest too large")
    return json.loads(raw.decode("utf-8"))


def parse_releases(payload: object) -> list[Release]:
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release list")
    result = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = str(item.get("tag_name") or "")
        match = _TAG.fullmatch(tag)
        if not match:
            continue
        names = frozenset(
            str(asset.get("name")) for asset in item.get("assets", [])
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        )
        result.append(Release(
            tag=tag,
            version=tuple(map(int, match.groups())),
            name=str(item.get("name") or tag)[:100],
            published_at=str(item.get("published_at") or "")[:32],
            notes=str(item.get("body") or ""),
            asset_names=names,
            prerelease=item.get("prerelease") is True,
        ))
    return sorted(result, key=lambda release: (release.version, not release.prerelease), reverse=True)


def _download() -> object:
    all_releases = []
    for page in range(1, 11):
        request = urllib.request.Request(
            f"{RELEASES_URL}?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "XIDER-X-LEDGER"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("GitHub response is too large")
        batch = json.loads(raw.decode("utf-8"))
        if not isinstance(batch, list):
            raise ValueError("GitHub returned an invalid release page")
        all_releases.extend(batch)
        if len(batch) < 100:
            return all_releases
    raise ValueError("Release list exceeds 1000 entries; refusing a truncated catalogue")


class Catalog:
    def __init__(
        self,
        loader: Callable[[], object] = _download,
        manifest_loader: Callable[[Release], dict] = _download_manifest,
        protected_manifest_loader: Callable[[Release], dict] = _download_protected_manifest,
    ) -> None:
        self._loader = loader
        self._manifest_loader = manifest_loader
        self._protected_manifest_loader = protected_manifest_loader
        self._lock = threading.Lock()
        self._releases: list[Release] = []
        self._fetched_at = 0.0

    def list(self, *, refresh: bool = False) -> list[Release]:
        with self._lock:
            # На свежем CI/хосте monotonic() может быть меньше TTL. Нулевой
            # timestamp означает, что каталог ещё ни разу не загружался, а не
            # действующий кэш от эпохи процесса.
            if not refresh and self._fetched_at and time.monotonic() - self._fetched_at < 600:
                return list(self._releases)
            try:
                releases = parse_releases(self._loader())
                signed = []
                for release in releases:
                    if "release-manifest.json" in release.asset_names:
                        try:
                            manifest = self._manifest_loader(release)
                            if verify_release_metadata(release, manifest):
                                release = replace(release, manifest=manifest)
                        except (OSError, ValueError):
                            pass
                    if release.manifest and _PROTECTED_BUILD_MANIFEST in release.asset_names:
                        try:
                            protected = self._protected_manifest_loader(release)
                            if verify_protected_build_metadata(release, protected, release.manifest):
                                release = replace(release, protected_manifest=protected)
                        except (OSError, ValueError):
                            pass
                    signed.append(release)
                releases = signed
            except (OSError, ValueError, json.JSONDecodeError):
                if self._fetched_at and not refresh:
                    return list(self._releases)
                raise
            self._releases = releases
            self._fetched_at = time.monotonic()
            return list(releases)

    def cached(self) -> list[Release]:
        with self._lock:
            return list(self._releases)


def newest_with_package(
    releases: list[Release], component: str, *, include_prereleases: bool = False,
) -> Release | None:
    """Return the stable update target by default; previews require explicit opt-in."""
    return next((item for item in releases
                 if (include_prereleases or not item.prerelease) and item.has_package(component)), None)


def is_older(current: str, latest: Release | None) -> bool:
    match = _TAG.fullmatch("v" + str(current or "").removeprefix("v"))
    return bool(latest and match and tuple(map(int, match.groups())) < latest.version)


def supports_release_manifest_update(current: str, minimum: str = "4.0.1") -> bool:
    """Return whether an agent understands the release-manifest update protocol."""
    current_match = _TAG.fullmatch("v" + str(current or "").strip().removeprefix("v"))
    minimum_match = _TAG.fullmatch("v" + str(minimum or "").strip().removeprefix("v"))
    return bool(
        current_match
        and minimum_match
        and tuple(map(int, current_match.groups())) >= tuple(map(int, minimum_match.groups()))
    )


catalog = Catalog()
