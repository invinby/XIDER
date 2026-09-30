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
from dataclasses import dataclass
from typing import Callable


RELEASES_URL = "https://api.github.com/repos/invinby/XIDER/releases"
_TAG = re.compile(r"^v([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})$")
_ASSET_NAMES = {
    "windows_agent": {"XGENT-WDS.exe", "XGENT-WDS-Windows.zip"},
    "mac_agent": {"XGENT-MCS-macos-bundle.zip"},
    "windows_keeper": {"Guard-Keeper-Windows.zip"},
    "mac_keeper": {"Guard-Keeper-macOS.zip"},
    "server": {"X-STAB-server.zip"},
}


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple[int, int, int]
    name: str
    published_at: str
    notes: str
    asset_names: frozenset[str]

    def has_package(self, component: str) -> bool:
        return bool(self.asset_names & _ASSET_NAMES.get(component, set()))


def component_for(os_name: str, keeper: bool = False) -> str | None:
    low = str(os_name or "").lower()
    if "win" in low:
        return "windows_keeper" if keeper else "windows_agent"
    if "mac" in low or "darwin" in low:
        return "mac_keeper" if keeper else "mac_agent"
    return None


def parse_releases(payload: object) -> list[Release]:
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release list")
    result = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
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
            notes=str(item.get("body") or "")[:6000],
            asset_names=names,
        ))
    return sorted(result, key=lambda release: release.version, reverse=True)


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
    def __init__(self, loader: Callable[[], object] = _download) -> None:
        self._loader = loader
        self._lock = threading.Lock()
        self._releases: list[Release] = []
        self._fetched_at = 0.0

    def list(self) -> list[Release]:
        with self._lock:
            # На свежем CI/хосте monotonic() может быть меньше TTL. Нулевой
            # timestamp означает, что каталог ещё ни разу не загружался, а не
            # действующий кэш от эпохи процесса.
            if self._fetched_at and time.monotonic() - self._fetched_at < 600:
                return list(self._releases)
            try:
                releases = parse_releases(self._loader())
            except (OSError, ValueError, json.JSONDecodeError):
                if self._fetched_at:
                    return list(self._releases)
                raise
            self._releases = releases
            self._fetched_at = time.monotonic()
            return list(releases)

    def cached(self) -> list[Release]:
        with self._lock:
            return list(self._releases)


def newest_with_package(releases: list[Release], component: str) -> Release | None:
    return next((item for item in releases if item.has_package(component)), None)


def is_older(current: str, latest: Release | None) -> bool:
    match = _TAG.fullmatch("v" + str(current or "").removeprefix("v"))
    return bool(latest and match and tuple(map(int, match.groups())) < latest.version)


catalog = Catalog()
