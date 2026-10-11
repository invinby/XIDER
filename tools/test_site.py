from __future__ import annotations

import subprocess
import sys
from pathlib import Path


CHECKER = Path(__file__).with_name("check_site.py")


def make_site(tmp_path: Path, *, html: str | None = None, css: str | None = None) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    (site / "styles.css").write_text(
        css
        if css is not None
        else ".skip-link:focus-visible { outline: 3px solid red; }\n"
        "@media (prefers-reduced-motion: reduce) { * { animation: none !important; } }\n",
        encoding="utf-8",
    )
    (site / "index.html").write_text(
        html
        if html is not None
        else '<!doctype html><html lang="en"><head><title>XIDER</title>'
        '<link rel="stylesheet" href="styles.css"></head><body>'
        '<main><h1>XIDER</h1><a href="about.html">About</a></main></body></html>',
        encoding="utf-8",
    )
    (site / "about.html").write_text(
        '<!doctype html><html lang="en"><head><title>About</title>'
        '<link rel="stylesheet" href="styles.css"></head><body>'
        '<main><h1>About</h1></main></body></html>',
        encoding="utf-8",
    )
    return site


def run_checker(site: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(site)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_accepts_valid_static_site(tmp_path: Path) -> None:
    result = run_checker(make_site(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr


def test_reports_missing_title_and_document_language(tmp_path: Path) -> None:
    html = (
        '<!doctype html><html><head><link rel="stylesheet" href="styles.css">'
        '</head><body><main><h1>XIDER</h1></main></body></html>'
    )

    result = run_checker(make_site(tmp_path, html=html))

    assert result.returncode != 0
    assert "missing <title>" in result.stdout
    assert "missing lang" in result.stdout


def test_reports_broken_internal_link(tmp_path: Path) -> None:
    html = (
        '<!doctype html><html lang="en"><head><title>XIDER</title>'
        '<link rel="stylesheet" href="styles.css"></head><body>'
        '<main><h1>XIDER</h1><a href="missing.html">Missing</a></main>'
        '</body></html>'
    )

    result = run_checker(make_site(tmp_path, html=html))

    assert result.returncode != 0
    assert "broken local link" in result.stdout
    assert "missing.html" in result.stdout


def test_requires_reduced_motion_behavior(tmp_path: Path) -> None:
    result = run_checker(
        make_site(
            tmp_path,
            css=".skip-link:focus-visible { outline: 3px solid red; }",
        )
    )

    assert result.returncode != 0
    assert "prefers-reduced-motion" in result.stdout


def test_requires_keyboard_visible_focus_treatment(tmp_path: Path) -> None:
    result = run_checker(
        make_site(
            tmp_path,
            css="@media (prefers-reduced-motion: reduce) { * { animation: none; } }",
        )
    )

    assert result.returncode != 0
    assert ":focus-visible" in result.stdout
