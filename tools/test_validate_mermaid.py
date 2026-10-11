"""Exercise the real pinned Mermaid CLI, including invalid input diagnostics."""
import subprocess
import sys
from pathlib import Path

import validate_mermaid

ROOT = Path(__file__).resolve().parents[1]


def run_validator(path):
    return subprocess.run([sys.executable, str(ROOT / "tools/validate_mermaid.py"), str(path)], capture_output=True, text=True)


def test_valid_diagram(tmp_path):
    path = tmp_path / "valid.md"
    path.write_text("```mermaid\nflowchart TD\n A --> B\n```\n", encoding="utf-8")
    result = run_validator(path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_malformed_second_diagram_reports_file_block_line(tmp_path):
    path = tmp_path / "invalid.md"
    path.write_text("```mermaid\nflowchart TD\n A --> B\n```\n\n```mermaid\nflowchart TD\n A[unterminated\n```\n", encoding="utf-8")
    result = run_validator(path)
    assert result.returncode != 0
    assert "invalid.md:6" in result.stdout
    assert "block 2" in result.stdout
    assert "Parse error" in result.stdout


def test_missing_diagram_fails(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("# No diagrams\n", encoding="utf-8")
    result = run_validator(path)
    assert result.returncode != 0
    assert "no Mermaid blocks" in result.stdout


def test_unclosed_mermaid_fence_fails(tmp_path):
    path = tmp_path / "unclosed.md"
    path.write_text("```mermaid\nflowchart TD\n A --> B\n", encoding="utf-8")
    result = run_validator(path)
    assert result.returncode != 0
    assert "unclosed" in result.stdout


def test_github_linux_renderer_uses_runner_compatible_sandbox_config(monkeypatch):
    monkeypatch.setattr(validate_mermaid.sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert validate_mermaid._puppeteer_args() == ["--no-sandbox", "--disable-setuid-sandbox"]


def test_local_renderer_does_not_disable_browser_sandbox(monkeypatch):
    monkeypatch.setattr(validate_mermaid.sys, "platform", "linux")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert validate_mermaid._puppeteer_args() == []
