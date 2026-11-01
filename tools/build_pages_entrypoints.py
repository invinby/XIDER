"""Generate pinned-key installer entry points and synchronized site versions.

Example: python tools/build_pages_entrypoints.py site
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from build_install_launchers import build_pages_launchers, write_files


ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\Z")


def source_version_from_config() -> str:
    """Read the source version and reject platform metadata drift."""
    versions = {}
    for component in ("XGENT-WDS", "XGENT-MCS"):
        config = ROOT / component / "config.py"
        match = re.search(r'(?m)^VERSION\s*=\s*"(?P<version>[^\"]+)"\s*$', config.read_text(encoding="utf-8"))
        if not match or not _VERSION_RE.fullmatch(match.group("version")):
            raise ValueError(f"Could not read a valid source version from {config.relative_to(ROOT)}")
        versions[component] = match.group("version")
    if len(set(versions.values())) != 1:
        raise ValueError(f"Windows/macOS source versions differ: {versions}")
    return next(iter(versions.values()))


def sync_source_versions(site_dir: Path, source_version: str | None = None) -> str:
    """Update both visible source-version slots in each localized landing page."""
    version = source_version or source_version_from_config()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid XIDER source version: {version!r}")

    for name in ("index.html", "ru.html"):
        path = site_dir / name
        text = path.read_text(encoding="utf-8")
        text, hero_count = re.subn(
            r'(<p class="hero-note">[^<]*<strong>)\d+\.\d+\.\d+(</strong>)',
            rf"\g<1>{version}\g<2>",
            text,
            count=1,
        )
        text, status_count = re.subn(
            r'(<span class="status-version">)\d+\.\d+\.\d+(</span>)',
            rf"\g<1>{version}\g<2>",
            text,
            count=1,
        )
        if hero_count != 1 or status_count != 1:
            raise ValueError(
                f"{name} must contain exactly one hero and one status source-version slot "
                f"(found {hero_count} and {status_count})"
            )
        path.write_text(text, encoding="utf-8", newline="")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        pages = build_pages_launchers()
    except ValueError as exc:
        parser.error(str(exc))
    for path in write_files(args.output, pages):
        print(path)
    if (args.output / "index.html").is_file() and (args.output / "ru.html").is_file():
        version = sync_source_versions(args.output)
        print(f"Synchronized bilingual source version: {version}")


if __name__ == "__main__":
    main()
