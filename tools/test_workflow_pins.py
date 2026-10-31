import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-agents.yml"
SERVER_BUNDLE_WORKFLOW = ROOT / ".github" / "workflows" / "server-deploy-bundle.yml"
WINDOWS_PREVIEW_WORKFLOW = ROOT / ".github" / "workflows" / "release-windows-preview.yml"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
EXPECTED_ACTIONS = {
    "actions/checkout": ("11bd71901bbe5b1630ceea73d27597364c9af683", "v4.2.2"),
    "actions/setup-node": ("49933ea5288caeca8642d1e84afbd3f7d6820020", "v4.4.0"),
    "actions/setup-python": ("a26af69be951a213d495a4c3e4e4022e16d87065", "v5.6.0"),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4.6.2"),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4.3.0"),
    "actions/upload-pages-artifact": ("fc324d3547104276b827a68afc52ff2a11cc49c9", "v5.0.0"),
    "actions/deploy-pages": ("368f82528645a54fb793d4d04e342629a3f51346", "v5.0.1"),
    "softprops/action-gh-release": ("72f2c25fcb47643c292f7107632f7a47c1df5cd8", "v2.3.2"),
}


def test_every_github_action_uses_an_explicit_immutable_commit_pin():
    observed = {}
    assert WORKFLOWS
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        uses_lines = re.findall(r"(?m)^\s*uses:\s*(.+?)\s*$", text)
        for value in uses_lines:
            if value == "./.github/workflows/ci.yml":
                assert workflow.name in {"build-agents.yml", "release-windows-preview.yml"}
                assert (ROOT / ".github" / "workflows" / "ci.yml").is_file()
                continue
            if value == "./.github/workflows/pages.yml":
                assert workflow.name == "release-windows-preview.yml"
                assert (ROOT / ".github" / "workflows" / "pages.yml").is_file()
                continue
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
    assert "cp artifacts/windows/XGENT-WDS-Windows.zip artifacts/XGENT-WDS-Windows.zip" in release
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in release
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
        "XIDER-bootstrap-windows.ps1.sig",
        "XIDER-bootstrap-macos.sh.sig",
        "XIDER-install-windows.ps1",
        "XIDER-install-macos.sh",
        "XIDER-install-windows.ps1.sig",
        "XIDER-install-macos.sh.sig",
        "XIDER-QUICKSTART.txt",
    ):
        assert f"artifacts/{asset}" in published_assets


def test_tagged_release_uses_version_specific_bilingual_release_notes():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]
    release_notes = ROOT / "docs" / "releases" / "v4.1.7.md"

    assert release_notes.is_file()
    assert "body_path: ${{ github.workspace }}/docs/releases/${{ github.ref_name }}.md" in release
    assert "generate_release_notes: true" in release
    assert "## Русский" in release_notes.read_text(encoding="utf-8")
    assert "## English" in release_notes.read_text(encoding="utf-8")


def test_tagged_release_waits_for_the_full_ci_workflow():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    native_release = WORKFLOW.read_text(encoding="utf-8")
    preview_release = WINDOWS_PREVIEW_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in ci and "source_ref:" in ci
    assert 'if: github.event_name == \'workflow_dispatch\' || (startsWith(github.ref, \'refs/tags/v\') && vars.XIDER_MACOS_RELEASE_READY == \'true\')' in native_release
    assert "    uses: ./.github/workflows/ci.yml" in native_release
    assert "source_ref: ${{ needs.source-state.outputs.source_commit }}" in native_release
    native_release_job = native_release.split("  create-release:", 1)[1]
    assert "needs: [release-ci, source-state, build-windows, build-macos, pack-components]" in native_release_job

    assert "  release-ci:\n    name: Release test gate" in preview_release
    assert "    uses: ./.github/workflows/ci.yml" in preview_release
    assert "    if: needs.release-state.outputs.exists == 'false'" in preview_release
    assert "source_ref: ${{ needs.release-state.outputs.source_commit }}" in preview_release
    assert "needs: [release-state, release-ci]" in preview_release.split("  build-windows:", 1)[1].split("  publish-windows:", 1)[0]
    assert "needs: [release-state, release-ci, build-windows]" in preview_release.split("  publish-windows:", 1)[1].split("  dispatch-pages:", 1)[0]


def test_manual_tag_build_creates_signed_inventory_without_publishing_release():
    text = WORKFLOW.read_text(encoding="utf-8")
    inventory = text.split("  protected-build-inventory:", 1)[1].split("  create-release:", 1)[0]
    release = text.split("  create-release:", 1)[1]

    assert "if: github.event_name == 'workflow_dispatch'" in inventory
    source = text.split("  source-state:", 1)[1].split("  release-ci:", 1)[0]
    assert "release_tag:" in text.split("  workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
    assert "ref: ${{ github.event_name == 'workflow_dispatch' && inputs.release_tag || github.sha }}" in source
    assert 'git fetch --force --no-tags origin "refs/tags/$tag:refs/tags/$tag"' in source
    assert 'git rev-parse --verify "refs/tags/$tag^{commit}"' in source
    assert '[[ "$tag_commit" == "$commit" ]]' in source
    assert "needs.source-state.outputs.source_commit" in inventory
    assert "XIDER_RELEASE_PRIVATE_KEY_B64: ${{ secrets.XIDER_RELEASE_PRIVATE_KEY_B64 }}" in inventory
    assert "tools/build_protected_build_manifest.py" in inventory
    assert "--require-signature" in inventory
    assert "name: XIDER-Protected-Build-Inventory" in inventory
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in release


def test_manual_signed_build_inventory_is_owner_only_and_never_publishes_release():
    text = WORKFLOW.read_text(encoding="utf-8")
    source_state = text.split("  source-state:", 1)[1].split("  release-ci:", 1)[0]
    inventory = text.split("  protected-build-inventory:", 1)[1].split("  create-release:", 1)[0]
    release = text.split("  create-release:", 1)[1]

    assert "github.actor == github.repository_owner" in source_state
    assert "github.actor == github.repository_owner" in inventory
    assert "needs: [release-ci, source-state, pack-components]" in inventory
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in release


def test_tagged_release_publishes_commit_pinned_quickstart_for_both_platforms():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]
    published_assets = release.split("          files: |", 1)[1].split("          draft:", 1)[0]

    assert 'python tools/sign_bootstrap.py "${GITHUB_REF_NAME}" "${GITHUB_SHA}" artifacts' in release
    assert 'python tools/build_bootstrap_commands.py "${GITHUB_SHA}" "${GITHUB_REF_NAME}" artifacts/XIDER-QUICKSTART.txt' in release
    assert "artifacts/XIDER-QUICKSTART.txt" in published_assets
    assert "cp deploy/bootstrap-agent.ps1 artifacts/XIDER-bootstrap-windows.ps1" in release
    assert "cp deploy/bootstrap.sh artifacts/XIDER-bootstrap-macos.sh" in release
    assert "XIDER-bootstrap-windows.ps1.sig" in release
    assert "XIDER-bootstrap-macos.sh.sig" in release
    assert 'python tools/build_release_manifest.py "${GITHUB_REF_NAME}" artifacts --require-signature' in release
    assert release.index('python tools/sign_bootstrap.py "${GITHUB_REF_NAME}" "${GITHUB_SHA}" artifacts') < release.index(
        'python tools/build_bootstrap_commands.py "${GITHUB_SHA}" "${GITHUB_REF_NAME}" artifacts/XIDER-QUICKSTART.txt'
    ) < release.index('python tools/build_release_manifest.py "${GITHUB_REF_NAME}" artifacts --require-signature')

    builder = (ROOT / "tools" / "build_bootstrap_commands.py").read_text(encoding="utf-8")
    assert 'raw_base = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}"' in builder
    assert 'for path in ("deploy/bootstrap-agent.ps1", "deploy/bootstrap.sh")' in builder
    assert "_committed_file(ref, path)" in builder
    assert '["git", "show", f"{commit_id}:{relative_path}"]' in builder
    assert "core.autocrlf" in builder
    assert "[Security.Cryptography.SHA256]::Create()" in builder
    assert "shasum -a 256" in builder
    assert "ssh-keygen -Y verify" in builder
    assert "XIDER_REF='{ref}' bash" in builder
    assert "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Ref '" in builder


def test_short_installers_are_generated_and_signed_before_the_asset_manifest():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]
    generate = 'python tools/build_install_launchers.py "${GITHUB_SHA}" "${GITHUB_REF_NAME}" artifacts --pages-dir artifacts/install-pages'
    signing = "python tools/sign_installers.py artifacts"
    inventory = 'python tools/build_release_manifest.py "${GITHUB_REF_NAME}" artifacts --require-signature'
    assert release.index(generate) < release.index(signing) < release.index(inventory)
    assert "name: XIDER-install-pages" in release
    assert "include-hidden-files: true" in release


def test_guard_keeper_macos_package_uses_compiled_app_without_python_helpers():
    text = WORKFLOW.read_text(encoding="utf-8")
    packaging = text.split("  pack-components:", 1)[1].split("  create-release:", 1)[0]
    assert "cp artifacts/macos/Guard-Keeper-macOS.zip artifacts/Guard-Keeper-macOS.zip" in packaging
    assert "unzip -Z1 artifacts/Guard-Keeper-macOS.zip | grep -Fq 'XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper'" in packaging
    assert "grep -Eq '(^|/)(guardian_recovery|release_signature|update_package)\\.py$'" in packaging


def test_macos_notarization_uses_the_nuitka_bundle_paths():
    text = WORKFLOW.read_text(encoding="utf-8")
    build = text.split('  build-macos:', 1)[1].split('  pack-components:', 1)[0]
    notarization = build.split('      - name: "Notarize and staple without changing signed app contents"', 1)[1]
    notarization = notarization.split('      - name: "Create and verify the final ZIP artifact"', 1)[0]

    for app in ("XIDER.app", "XIDER-Guard-Keeper.app", "XIDER-Layers.app"):
        assert f"XGENT-MCS/dist/nuitka/{app}" in notarization
    assert "XGENT-MCS/dist/guardian/" not in notarization
    assert "XGENT-MCS/dist/layers/" not in notarization


def test_release_signing_secret_is_scoped_to_protected_release_environment():
    text = WORKFLOW.read_text(encoding="utf-8")
    release = text.split("  create-release:", 1)[1]
    inventory = text.split("  protected-build-inventory:", 1)[1].split("  create-release:", 1)[0]

    assert "permissions:\n  contents: read" in text
    assert "environment:\n      name: xider-release-signing" in release
    assert "permissions:\n      contents: write" in release
    assert "XIDER_RELEASE_PRIVATE_KEY_B64: ${{ secrets.XIDER_RELEASE_PRIVATE_KEY_B64 }}" in release
    assert "environment:\n      name: xider-release-signing" in inventory
    assert "XIDER_RELEASE_PRIVATE_KEY_B64: ${{ secrets.XIDER_RELEASE_PRIVATE_KEY_B64 }}" in inventory
    assert "XIDER_RELEASE_PRIVATE_KEY_B64" not in text.split("  protected-build-inventory:", 1)[0]


def test_server_bundle_workflow_signs_source_without_publishing_a_general_release():
    assert SERVER_BUNDLE_WORKFLOW.is_file()
    text = SERVER_BUNDLE_WORKFLOW.read_text(encoding="utf-8")

    assert 'tags: ["server-deploy-*"]' in text
    assert "environment:\n      name: xider-release-signing" in text
    assert "permissions:\n  contents: read" in text
    assert "XIDER_RELEASE_PRIVATE_KEY_B64: ${{ secrets.XIDER_RELEASE_PRIVATE_KEY_B64 }}" in text
    assert "git archive --format=zip --prefix=XIDER-source/ --output=artifacts/XIDER-source.zip HEAD" in text
    assert "python tools/verify_command_approval_bundle.py --public-only-zip artifacts/XIDER-source.zip" in text
    assert "tools/sign_server_bundle.py" in text
    assert "deploy/verify_server_bundle.py" in text
    assert "name: XIDER-server-deploy-bundle" in text
    assert "softprops/action-gh-release" not in text


def test_windows_preview_build_and_signed_assets_share_the_selected_tag_commit():
    text = WINDOWS_PREVIEW_WORKFLOW.read_text(encoding="utf-8")
    source_version = re.search(
        r'^VERSION = "([0-9.]+)"$',
        (ROOT / "XGENT-WDS" / "config.py").read_text(encoding="utf-8"), re.M,
    )
    default_tag = re.search(r"(?m)^\s+default:\s+(v[0-9.]+)\s*$", text)
    assert source_version and default_tag
    assert default_tag.group(1) == f"v{source_version.group(1)}"

    build = text.split("  build-windows:", 1)[1].split("  publish-windows:", 1)[0]
    publish = text.split("  publish-windows:", 1)[1]
    source_state = text.split("  release-state:", 1)[1].split("  release-ci:", 1)[0]
    assert "ref: ${{ github.event_name == 'workflow_dispatch' && inputs.release_tag || github.ref_name }}" in source_state
    assert "source_commit: ${{ steps.check.outputs.source_commit }}" in source_state
    assert "ref: ${{ needs.release-state.outputs.source_commit }}" in build
    assert "source_commit: ${{ steps.source.outputs.commit }}" in build
    assert "git rev-parse HEAD" in build
    assert "ref: ${{ needs.build-windows.outputs.source_commit }}" in publish
    assert 'BUILD_SHA: ${{ needs.build-windows.outputs.source_commit }}' in publish
    assert 'COMMIT=$(git rev-parse "${BUILD_SHA}^{commit}")' in publish
    assert "git show \"${COMMIT}:deploy/bootstrap.sh\" > artifacts/XIDER-bootstrap-macos.sh" in publish
    assert "build_install_launchers.py \"$COMMIT\" \"$TAG\" artifacts --macos-source-preview" in publish
    assert "sign_bootstrap.py \"$TAG\" \"$COMMIT\" artifacts" in publish
    assert "sign_installers.py artifacts" in publish
    assert "--platform windows --platform macos --exclude-asset XGENT-MCS-macos-bundle.zip --exclude-asset Guard-Keeper-macOS.zip --require-signature" in publish
    assert "artifacts/XIDER-install-macos.sh.sig" in publish
    assert "runtime is not Apple-signed or notarized" in publish
    assert "${{ github.sha }}" not in build + publish
