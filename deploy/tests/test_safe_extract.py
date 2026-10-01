from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from safe_extract import extract_release


def make_zip(path: Path, entries: list[tuple[str, bytes, int | None]]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content, mode in entries:
            info = ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, content)


def test_extracts_regular_files_and_nested_directories(tmp_path: Path) -> None:
    bundle = tmp_path / "release.zip"
    destination = tmp_path / "stage"
    make_zip(bundle, [("./TG-BOT-SERVER/bot.py", b"print('ok')", None)])

    extract_release(bundle, destination)

    assert (destination / "TG-BOT-SERVER" / "bot.py").read_bytes() == b"print('ok')"


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/drive.txt", r"..\escape.txt"])
def test_rejects_unsafe_paths_without_writing_outside_stage(tmp_path: Path, name: str) -> None:
    bundle = tmp_path / "release.zip"
    destination = tmp_path / "stage"
    make_zip(bundle, [(name, b"bad", None)])

    with pytest.raises(ValueError):
        extract_release(bundle, destination)

    assert not (tmp_path / "escape.txt").exists()


def test_rejects_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "release.zip"
    make_zip(bundle, [("link", b"../outside", 0o120777)])

    with pytest.raises(ValueError, match="symlink"):
        extract_release(bundle, tmp_path / "stage")


def test_rejects_duplicate_normalized_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "release.zip"
    make_zip(bundle, [("./bot.py", b"one", None), ("bot.py", b"two", None)])

    with pytest.raises(ValueError, match="Duplicate"):
        extract_release(bundle, tmp_path / "stage")


def test_rejects_uncompressed_size_limit(tmp_path: Path) -> None:
    bundle = tmp_path / "release.zip"
    make_zip(bundle, [("large.bin", b"12345", None)])

    with pytest.raises(ValueError, match="size limit"):
        extract_release(bundle, tmp_path / "stage", max_uncompressed_size=4)
