"""Nuitka intermediates are removed only after a successful local package build."""

from pathlib import Path

import pytest

import build_nuitka_windows as builder


def test_successful_build_removes_only_its_staging_tree_and_keeps_outputs(tmp_path, monkeypatch):
    output = tmp_path / "dist" / "nuitka"
    build_parent = tmp_path / "build"
    output.mkdir(parents=True)
    unrelated = output / "owner-note.txt"
    unrelated.write_text("keep", encoding="utf-8")
    legacy = output / "xgent_wds.build"
    legacy.mkdir()
    (legacy / "generated.c").write_text("generated", encoding="utf-8")
    unknown = output / "guardian"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(builder, "BUILD_PARENT", build_parent)
    monkeypatch.setattr(builder, "_hidden_imports", lambda: ("fixture",))

    def build(_entry, output_dir: Path, output_name: str, _modules=()):
        output_dir.mkdir(parents=True)
        artifact = output_dir / output_name
        artifact.write_bytes(b"compiled fixture")
        (output_dir / "intermediate.c").write_text("generated", encoding="utf-8")
        return artifact

    monkeypatch.setattr(builder, "_build", build)

    agent, guardian = builder.build_packages()

    assert agent.read_bytes() == guardian.read_bytes() == b"compiled fixture"
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not legacy.exists()
    assert (unknown / "keep.txt").read_text(encoding="utf-8") == "preserve"
    assert list(build_parent.iterdir()) == []


def test_legacy_intermediate_with_diagnostics_or_env_is_preserved(tmp_path, monkeypatch):
    output = tmp_path / "dist" / "nuitka"
    legacy = output / "xgent_wds.build"
    legacy.mkdir(parents=True)
    diagnostic = legacy / "nuitka-crash-report.xml"
    diagnostic.write_text("diagnostic", encoding="utf-8")
    (legacy / ".env").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(builder, "OUTPUT", output)

    builder._cleanup_legacy_nuitka_intermediates()

    assert diagnostic.read_text(encoding="utf-8") == "diagnostic"
    assert (legacy / ".env").read_text(encoding="utf-8") == "fixture"


def test_failed_build_keeps_only_its_staging_tree_for_diagnosis(tmp_path, monkeypatch):
    output = tmp_path / "dist" / "nuitka"
    build_parent = tmp_path / "build"
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(builder, "BUILD_PARENT", build_parent)
    monkeypatch.setattr(builder, "_hidden_imports", lambda: ("fixture",))

    def fail_after_writing(_entry, output_dir: Path, _output_name: str, _modules=()):
        output_dir.mkdir(parents=True)
        (output_dir / "nuitka-crash-report.xml").write_text("diagnostic", encoding="utf-8")
        raise RuntimeError("fixture compile failure")

    monkeypatch.setattr(builder, "_build", fail_after_writing)

    with pytest.raises(RuntimeError, match="fixture compile failure"):
        builder.build_packages()

    stages = list(build_parent.glob("nuitka-windows-*"))
    assert len(stages) == 1
    assert list(stages[0].rglob("nuitka-crash-report.xml"))[0].read_text(encoding="utf-8") == "diagnostic"


def test_staging_cleanup_error_does_not_turn_a_successful_build_into_failure(tmp_path, monkeypatch):
    output = tmp_path / "dist" / "nuitka"
    build_parent = tmp_path / "build"
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(builder, "BUILD_PARENT", build_parent)
    monkeypatch.setattr(builder, "_hidden_imports", lambda: ("fixture",))

    def build(_entry, output_dir: Path, output_name: str, _modules=()):
        output_dir.mkdir(parents=True)
        artifact = output_dir / output_name
        artifact.write_bytes(b"compiled fixture")
        return artifact

    monkeypatch.setattr(builder, "_build", build)
    def cleanup_busy(*_args, **_kwargs):
        raise OSError("busy")

    monkeypatch.setattr(builder.shutil, "rmtree", cleanup_busy)

    agent, guardian = builder.build_packages()

    assert agent.is_file() and guardian.is_file()
    assert list(build_parent.glob("nuitka-windows-*"))
