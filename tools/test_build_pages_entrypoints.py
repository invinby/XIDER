from __future__ import annotations

from pathlib import Path

import pytest

from build_pages_entrypoints import ROOT, source_version_from_config, sync_source_versions


def _write_site(site: Path, version: str = "4.3.9") -> None:
    site.mkdir()
    for name, title in (("index.html", "Source version"), ("ru.html", "Версия исходников")):
        (site / name).write_text(
            f'<p class="hero-note">{title} <strong>{version}</strong></p>\n'
            f'<span class="status-version">{version}</span>\n',
            encoding="utf-8",
        )


def test_sync_source_versions_updates_bilingual_pages_from_repo_config(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_site(site)

    version = sync_source_versions(site, source_version="4.3.16")

    assert version == "4.3.16"
    for name in ("index.html", "ru.html"):
        text = (site / name).read_text(encoding="utf-8")
        assert "4.3.16" in text
        assert "4.3.9" not in text


def test_sync_source_versions_fails_if_expected_version_slots_are_missing(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _write_site(site)
    (site / "ru.html").write_text("<main>markup changed</main>", encoding="utf-8")

    with pytest.raises(ValueError, match="ru.html"):
        sync_source_versions(site, source_version="4.3.16")


def test_bilingual_pages_show_the_source_version_from_both_agent_configs() -> None:
    version = source_version_from_config()

    for name in ("index.html", "ru.html"):
        text = (ROOT / "site" / name).read_text(encoding="utf-8")
        assert f'<strong>{version}</strong>' in text
        assert f'<span class="status-version">{version}</span>' in text
