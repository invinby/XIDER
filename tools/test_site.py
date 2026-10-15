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


def test_bilingual_landing_has_semantic_language_navigation_and_layout_stability() -> None:
    root = Path(__file__).resolve().parents[1] / "site"
    css = (root / "styles.css").read_text(encoding="utf-8")

    for filename, label in (
        ("index.html", 'aria-label="Language"'),
        ("ru.html", 'aria-label="&#1071;&#1079;&#1099;&#1082;"'),
    ):
        html = (root / filename).read_text(encoding="utf-8")
        assert f'<nav class="language-switch" {label}>' in html
        assert '<img src="assets/xider-mark.png" alt="" width="726" height="726">' in html
        assert 'name="theme-color" content="#f5f1ea"' in html
        assert 'name="theme-color" content="#171818"' in html

    assert "h1[id], h2[id], h3[id] { scroll-margin-top:" in css
