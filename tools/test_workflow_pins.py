import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-agents.yml"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
EXPECTED_ACTIONS = {
    "actions/checkout": ("11bd71901bbe5b1630ceea73d27597364c9af683", "v4.2.2"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5.6.0"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4.6.2"),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4.3.0"),
    "softprops/action-gh-release": ("72f2c25fcb47643c292f7107632f7a47c1df5cd8", "v2.3.2"),
}


def test_every_github_action_uses_an_explicit_immutable_commit_pin():
    observed = {}
    assert WORKFLOWS
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        uses_lines = re.findall(r"(?m)^\s*uses:\s*(.+?)\s*$", text)
        for value in uses_lines:
            match = re.fullmatch(r"([^@\s]+)@([0-9a-f]{40})\s+#\s+(v\S+)", value)
            assert match, f"{workflow.name} action is not pinned to an immutable SHA: {value}"
            action, sha, release = match.groups()
            assert action in EXPECTED_ACTIONS, f"Unexpected external action in {workflow.name}: {action}"
            assert EXPECTED_ACTIONS[action] == (sha, release)
            observed.setdefault(action, set()).add((sha, release))

    assert observed == {name: {pin} for name, pin in EXPECTED_ACTIONS.items()}


def test_ci_workflow_has_read_only_github_token_permissions():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text


def test_tagged_release_builds_and_publishes_signed_source_package():
    text = WORKFLOW.read_text(encoding="utf-8")
    packaging = text.split("  pack-components:", 1)[1].split("  create-release:", 1)[0]
    release = text.split("  create-release:", 1)[1]

    assert "--output=artifacts/XIDER-source.zip HEAD" in packaging
    assert "path: artifacts/*.zip" in packaging
    assert "if: startsWith(github.ref, 'refs/tags/v')" in release
    assert 'python tools/build_release_manifest.py "${GITHUB_REF_NAME}" artifacts --require-signature' in release

    published_assets = release.split("          files: |", 1)[1].split("          draft:", 1)[0]
    for asset in (
        "XIDER-source.zip",
        "XGENT-WDS.exe",
        "XGENT-MCS-macos-bundle.zip",
        "Guard-Keeper-Windows.zip",
        "Guard-Keeper-macOS.zip",
        "X-STAB-server.zip",
        "release-manifest.json",
        "XIDER-bootstrap-windows.ps1",
        "XIDER-bootstrap-macos.sh",
        "XIDER-QUICKSTART.txt",
    ):
        assert f"artifacts/{asset}" in published_assets


def test_tagged_release_publishes_commit_pinned_quickstart_for_both_platforms():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]
    published_assets = release.split("          files: |", 1)[1].split("          draft:", 1)[0]

    assert 'python tools/build_bootstrap_commands.py "${GITHUB_SHA}" artifacts/XIDER-QUICKSTART.txt' in release
    assert "artifacts/XIDER-QUICKSTART.txt" in published_assets
    assert "cp deploy/bootstrap.ps1 artifacts/XIDER-bootstrap-windows.ps1" in release
    assert "cp deploy/bootstrap.sh artifacts/XIDER-bootstrap-macos.sh" in release
    assert 'python tools/build_release_manifest.py "${GITHUB_REF_NAME}" artifacts --require-signature' in release

    builder = (ROOT / "tools" / "build_bootstrap_commands.py").read_text(encoding="utf-8")
    assert 'raw_base = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}"' in builder
    assert "hashlib.sha256((ROOT / \"deploy\" / \"bootstrap.ps1\").read_bytes())" in builder
    assert "hashlib.sha256((ROOT / \"deploy\" / \"bootstrap.sh\").read_bytes())" in builder
    assert "Get-FileHash -LiteralPath $p -Algorithm SHA256" in builder
    assert "shasum -a 256 -c -" in builder
    assert 'XIDER_REF={ref} bash' in builder
    assert "& $p -Ref '" in builder


def test_release_signing_secret_is_scoped_to_protected_release_environment():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]

    assert "permissions:\n  contents: read" in text
    assert "environment:\n      name: xider-release-signing" in release
    assert "permissions:\n      contents: write" in release
    assert "XIDER_RELEASE_PRIVATE_KEY_B64: ${{ secrets.XIDER_RELEASE_PRIVATE_KEY_B64 }}" in release
    assert "XIDER_RELEASE_PRIVATE_KEY_B64" not in text.split("  create-release:", 1)[0]
