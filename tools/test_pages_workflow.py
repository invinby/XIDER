from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_pages_workflow_validates_pull_requests_without_deploying() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r"^\s*pull_request:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", workflow, re.MULTILINE)
    assert "actions/deploy-pages" not in workflow
    assert not re.search(r"^\s*pages:\s*write\s*$", workflow, re.MULTILINE)
    assert not re.search(r"^\s*id-token:\s*write\s*$", workflow, re.MULTILINE)
    assert not re.search(r"^\s*environment:\s*$", workflow, re.MULTILINE)


def test_pages_artifact_is_built_only_from_the_static_site_directory() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/upload-pages-artifact@" in workflow
    assert re.search(r"^\s*path:\s*site/?\s*$", workflow, re.MULTILINE)
    assert "deploy-pages" not in workflow


def test_every_external_action_is_pinned_to_a_full_commit_sha() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")
    uses_lines = re.findall(r"^\s*uses:\s*([^\s]+)", workflow, re.MULTILINE)

    assert uses_lines, "Pages workflow must use the repository's pinned actions"
    assert all(re.search(r"@[0-9a-f]{40}$", action) for action in uses_lines), uses_lines


def test_component_ci_checks_python_syntax_while_excluding_generated_trees() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m compileall -q" in workflow
    for component in ("TG-BOT-SERVER", "XGENT-WDS", "XGENT-MCS", "ops", "tools", "deploy"):
        assert component in workflow
    for generated in ("venv", "node_modules", "build", "dist"):
        assert generated in workflow
