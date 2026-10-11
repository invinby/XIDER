#!/usr/bin/env python3
"""Check the static XIDER landing pages for basic accessibility and local links."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title_seen = False
        self.title_text = ""
        self._inside_title = False
        self.h1_count = 0
        self.main_count = 0
        self.ids: set[str] = set()
        self.links: list[tuple[str, str, bool]] = []
        self.images_without_alt: int = 0
        self.scripts: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.lang = (values.get("lang") or "").strip()
        elif tag == "title":
            self.title_seen = True
            self._inside_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "main":
            self.main_count += 1
        elif tag == "img" and "alt" not in values:
            self.images_without_alt += 1
        elif tag == "script":
            self.scripts += 1

        if values.get("id"):
            self.ids.add(values["id"] or "")
        if tag == "a" and values.get("href") is not None:
            self.links.append((values["href"] or "", "href", False))
        if tag in {"link", "img", "source"}:
            attribute = "src" if tag in {"img", "source"} else "href"
            if values.get(attribute) is not None:
                self.links.append((values[attribute] or "", attribute, tag != "a"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.title_text += data


def _load_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def check_site(site_dir: Path) -> list[str]:
    site_root = site_dir.resolve()
    if not site_root.is_dir():
        return [f"site directory does not exist: {site_dir}"]

    html_files = sorted(site_root.rglob("*.html"))
    css_files = sorted(site_root.rglob("*.css"))
    if not html_files:
        return ["no HTML pages found"]
    if not css_files:
        return ["no CSS files found"]

    errors: list[str] = []
    page_parsers: dict[Path, PageParser] = {}
    for page in html_files:
        try:
            parser = _load_page(page)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{page.relative_to(site_root)}: cannot read HTML: {exc}")
            continue

        page_parsers[page] = parser
        label = page.relative_to(site_root).as_posix()
        if not parser.lang:
            errors.append(f"{label}: missing lang on <html>")
        if not parser.title_seen or not parser.title_text.strip():
            errors.append(f"{label}: missing <title>")
        if parser.main_count != 1:
            errors.append(f"{label}: expected exactly one <main>, found {parser.main_count}")
        if parser.h1_count != 1:
            errors.append(f"{label}: expected exactly one <h1>, found {parser.h1_count}")
        if parser.images_without_alt:
            errors.append(f"{label}: {parser.images_without_alt} image(s) missing alt text")
        if parser.scripts:
            errors.append(f"{label}: static pages must not contain <script>")

    for page, parser in page_parsers.items():
        label = page.relative_to(site_root).as_posix()
        for raw_url, attribute, is_resource in parser.links:
            parsed = urlsplit(raw_url.strip())
            if parsed.scheme or parsed.netloc:
                if is_resource:
                    errors.append(f"{label}: remote resource is not allowed in {attribute}: {raw_url}")
                continue

            relative = unquote(parsed.path)
            target = (page.parent / relative).resolve() if relative else page.resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(site_root):
                errors.append(f"{label}: local link escapes site directory: {raw_url}")
                continue
            if not target.is_file():
                errors.append(f"{label}: broken local link: {raw_url}")
                continue
            if parsed.fragment and target.suffix.lower() == ".html":
                target_parser = page_parsers.get(target)
                if target_parser is None:
                    try:
                        target_parser = _load_page(target)
                    except (OSError, UnicodeError):
                        target_parser = None
                if target_parser and unquote(parsed.fragment) not in target_parser.ids:
                    errors.append(f"{label}: missing fragment #{unquote(parsed.fragment)} in {raw_url}")

    css_text = "\n".join(path.read_text(encoding="utf-8") for path in css_files).lower()
    if ":focus-visible" not in css_text:
        errors.append("CSS: missing keyboard-visible :focus-visible treatment")
    if "prefers-reduced-motion" not in css_text:
        errors.append("CSS: missing prefers-reduced-motion behavior")
    if "@import" in css_text and "https://" in css_text:
        errors.append("CSS: remote @import is not allowed")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "site_dir",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "site",
        help="site directory to validate (defaults to the repository's site/)",
    )
    args = parser.parse_args(argv)

    errors = check_site(args.site_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Static site checks passed ({len(list(args.site_dir.rglob('*.html')))} HTML pages).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
