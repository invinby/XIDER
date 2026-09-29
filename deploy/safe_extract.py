#!/usr/bin/env python3
"""Extract a ZIP release into a fresh staging directory without path escapes."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000


def extract_release(
    archive_path: Path,
    destination: Path,
    *,
    max_uncompressed_size: int = MAX_UNCOMPRESSED_SIZE,
) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = set()
    total_size = 0

    with ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBER_COUNT:
            raise ValueError("Release archive contains too many entries.")

        for info in members:
            name = info.filename
            path = PurePosixPath(name)
            mode = (info.external_attr >> 16) & 0o170000
            if "\\" in name or path.is_absolute() or ".." in path.parts or mode == 0o120000:
                raise ValueError(f"Unsafe archive path or symlink: {name!r}")
            if path.parts and ":" in path.parts[0]:
                raise ValueError(f"Unsafe archive drive path: {name!r}")

            target = (destination / Path(*path.parts)).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"Archive path escapes staging directory: {name!r}")
            if target in seen:
                raise ValueError(f"Duplicate archive path: {name!r}")
            seen.add(target)

            total_size += info.file_size
            if total_size > max_uncompressed_size:
                raise ValueError("Release archive expands beyond the configured size limit.")

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: safe_extract.py RELEASE.zip STAGING_DIR", file=sys.stderr)
        return 2
    try:
        extract_release(Path(argv[1]), Path(argv[2]))
    except Exception as exc:
        print(f"Release archive rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
