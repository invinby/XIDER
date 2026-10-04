"""Generate pinned-key short installer entry points without a release tag.

Example: python tools/build_pages_entrypoints.py docs
The other documentation files in the output directory are left unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from build_install_launchers import build_pages_launchers, write_files


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


if __name__ == "__main__":
    main()
