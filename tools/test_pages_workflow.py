from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_pages_workflow_validates_pull_requests_and_gates_production_deploy_events() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r"^\s*pull_request:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s*workflow_dispatch:\s*\n\s+inputs:\s*\n\s+installer_tag:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s*required:\s*false\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s*type:\s*string\s*$", workflow, re.MULTILINE)
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", workflow, re.MULTILINE)

    deploy = re.search(r"(?ms)^  deploy:\n(?P<body>.*?)(?=^  [a-zA-Z_-]+:|\Z)", workflow)
    assert deploy, "Pages deploy must be an explicit, separate job"
    body = deploy.group("body")
    assert re.search(
        r"^    if:\s*\(github\.event_name == 'push' && github\.ref == 'refs/heads/main'\) \|\| \(github\.event_name == 'workflow_dispatch' && github\.ref == 'refs/heads/main'\)\s*$",
        body,
        re.MULTILINE,
    )
    assert re.search(r"^    needs:\s*build\s*$", body, re.MULTILINE)
    assert re.search(r"^    environment:\s*$\n\s+name:\s*github-pages\s*$", body, re.MULTILINE)
    assert re.search(r"^\s+pages:\s*write\s*$", body, re.MULTILINE)
    assert re.search(r"^\s+id-token:\s*write\s*$", body, re.MULTILINE)
    assert "actions/deploy-pages@" in body


def test_pages_artifact_is_built_only_from_the_static_site_directory() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/upload-pages-artifact@" in workflow
    assert re.search(r"^\s*path:\s*site/?\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s*id:\s*deployment\s*$", workflow, re.MULTILINE)


def test_pages_generates_signed_installers_before_checking_and_uploading_site() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pip install pytest cryptography" in workflow
    generate = "python tools/build_pages_entrypoints.py site"
    check = "python tools/check_site.py"
    upload = "uses: actions/upload-pages-artifact@"
    assert generate in workflow
    assert workflow.index(generate) < workflow.index(check)
    assert workflow.index(generate) < workflow.index(upload)
    assert "gh api \"repos/${GITHUB_REPOSITORY}/releases?per_page=100\"" in workflow
    assert "REQUESTED_TAG: ${{ inputs.installer_tag }}" in workflow
    assert "RELEASE_EVENT_TAG:" not in workflow
    assert "XIDER_WINDOWS_INSTALLER_TAG: ${{ steps.release-tag.outputs.tag }}" in workflow
    assert "XIDER_MACOS_INSTALLER_TAG: ${{ steps.release-tag.outputs.tag }}" in workflow
    assert re.search(r"^\s*workflow_dispatch:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s+XIDER_MACOS_SOURCE_PREVIEW:\s*'1'\s*$", workflow, re.MULTILINE)

    for source in (
        '"tools/build_pages_entrypoints.py"',
        '"tools/build_install_launchers.py"',
        '"tools/build_bootstrap_commands.py"',
        '"XGENT-MCS/release_signature.py"',
    ):
        assert source in workflow, f"Pages must rebuild when launcher input changes: {source}"


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
