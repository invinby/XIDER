# XIDER stabilization acceptance matrix — 2026-10-30

This matrix separates repository evidence from physical-device and hosted-service evidence. A passing source test does not mean the feature was exercised on a user's Mac or Windows PC.

## Local acceptance

| Area | Result | Evidence | Still needed |
|---|---|---|---|
| Telegram device navigation | Pass at source/unit level | `TG-BOT-SERVER/tests/test_tarped.py`, `tests/test_bot_ui.py`; full Telegram suite: 1,752 passed, 4 skipped | Owner checks compact device/tools screens and Back history in Telegram |
| Owner approval switch and command hiding | Pass at source/unit level | Bot settings/UI and server operation tests in the full suite | Owner confirms preferred setting behavior in Telegram |
| Agent command/result contracts | Matrix generated for 195 workers and 7 Guardian commands on both platforms; source tests pass | `docs/x-map-command-matrix.json`; X-MAP suite: 11 passed | Exercise representative actions on both physical OSes; confirm each effect and correlated response |
| Windows source/installer fixtures | Pass | Windows suite: 554 passed; PowerShell 5.1 bootstrap, setup-selection, and transaction fixtures passed | Fresh install, permissions, recovery, and terminal-window behavior on a physical Windows device |
| macOS source/installer fixtures | Pass | macOS suite: 663 passed, 1 skipped; shell/integration fixtures available on Windows were run | Fresh install, update/rollback, permissions, camera, screenshot, GPS, and recovery on a physical Mac |
| Agent Files inventory | Pass at source/unit level; integrity remains unverified without a trusted per-file manifest | Windows/macOS inventory tests and Telegram owner callbacks included in full suite | Inspect actual installed component list/signature on each owner device |
| Windows build-cache cleanup | Policy and mocked cleanup tests pass; no build output deleted in this work | `tools/test_build_nuitka_windows.py`; next successful local Windows build removes only recognized legacy intermediate directories | Run a successful packaged build, then confirm final artifacts remain and cache space is reclaimed |
| Feature registry | Pass | 510 unique features across 17 areas; registry tests and validator pass | None for the generated local registry |
| Signed release workflow | Source/workflow contract passes; not executed on GitHub | Protected manual build workflow, manifest/signature tests, release-tool suite | Run owner-approved workflow and inspect signed artifact before publishing |

## Version and copy snapshot

| Surface | Version/state on 2026-10-30 | Meaning |
|---|---|---|
| Bot, agent source, registry/docs source | 4.3.4 | Current repository source version |
| GitHub `v4.3.4` | Windows-only preview | Explicit preview, not the stable cross-platform release |
| Latest stable signed release | `v4.2.1` | Current default for the macOS signed installer |
| Windows Pages installer default | `v4.3.4` | Deliberately tracks the Windows preview |
| macOS Pages installer default | `v4.2.1` | Deliberately remains on the latest stable signed cross-platform package |

The Telegram version picker can select an explicitly published version; the launchers validate the tag and verify the matching signed installer before execution. Version/copy checks and the full test runner passed locally. No GitHub release, Pages deployment, or VPS change was made.

## Full local verification

`tools/run_tests.py` completed with exit code 0:

- Feature registry: 510 unique features / 17 areas, passed.
- Telegram: 1,752 passed, 4 skipped.
- Windows: 554 passed.
- macOS: 663 passed, 1 skipped.
- Operations: 30 passed.
- Release/build tools: 196 passed, 3 skipped, 2 subtests passed.
- Deployment archives/signatures: 25 passed, 27 skipped.
- Windows bootstrap preflight, setup selection, installer selection, and transaction fixtures passed. Elevated install/rollback fixture skipped because this process was not elevated.

## Owner device acceptance still required

1. On Windows, install from the short command; approve the new device; verify the console does not flash; check the Agent Files inventory, camera/microphone/location permission guidance, screenshot, and one confirmed command/result.
2. On macOS, install/update through the signed path; approve permissions; verify capability recheck, screenshot, camera/microphone, location source/accuracy, Agent Files inventory, and one confirmed command/result.
3. On both, choose an explicitly published version, update, and rollback while confirming `.env` and owner configuration remain intact.
4. In Telegram, verify compact device tools, Back navigation, approval toggle, input hiding, Agent Files, and truthful pending/unknown/unsupported action messages.
5. After a successful Windows package build, inspect the output and confirm only the six recognized legacy Nuitka intermediate directories were cleaned.

Until these device steps are completed, physical behavior, live Telegram interaction, GitHub-hosted release execution, and published Pages state remain unverified.

## Outcome matrix coverage

The X-MAP snapshot contains 404 platform/action rows. Every row has expected-result evidence and a stability/evidence state. Counts: 389 source-only unverified, 3 dependency-gated unverified, 10 known source-limited, and 2 reported issues not retested. No row lacks evidence text. The shared contract distinguishes broker delivery acceptance from device confirmation, then preserves confirmed, failed, timed-out, unsupported, and unknown outcomes. This is source/documentation coverage; it is not a claim that every command and every branch was exercised on a physical device.

## Requirement-by-requirement audit

| Goal item | Repository result | Remaining proof |
|---|---|---|
| 1. Compact device menu | Implemented and covered by Telegram UI tests; device rows remain on the first screen and tools are nested | Owner visual check in Telegram |
| 2. Back navigation | Per-user previous-screen history, authorization recheck, and pending-input cleanup are covered by UI tests | Owner navigation check in Telegram |
| 3. Install/update confirmations | New-device approval is a persisted owner-controlled policy; it gates new enrollments and device actions. Version installation has a separate explicit release-selection confirmation. The two controls are not conflated. | Owner confirms the two flows and setting labels in Telegram |
| 4. Hide typed inputs | Owner setting defaults off and only removes the owner's recognized successfully published command input; errors and unrelated messages remain | Owner checks preference in Telegram |
| 5. Truthful device outcomes | Correlation, typed state, source, unsupported/unknown/failure, and launch/request-only wording are implemented across common paths and targeted tests | Physical effects/results on both OSes |
| 6. Windows/macOS feature status | Generated X-MAP covers all worker and Guardian commands on both platforms and labels source-only, limited, dependency-gated, and known reported issue states | Device trial for rows marked unverified |
| 7. Windows terminal flash | Guardian fallback now selects a packaged executable or `pythonw.exe`; mocked installer/task fixtures pass | Fresh physical Windows startup and recovery check |
| 8. Install/update/version selection | Both Pages launchers verify selected release artifacts; fixtures cover platform selection, retention, and rollback paths; release defaults are labeled accurately | Physical install, update, and rollback on Windows and Mac |
| 9. Storage/temp files | XIDER-owned media temp cleanup is bounded on both platforms; Windows build cleanup targets only recognized successful-build intermediates. Existing build cache was preserved. | Successful Windows package build and installed-device storage measurement |
| 10. Versions and RU/EN copy | Source, previews, stable package defaults, generated registry and local copy tests are synchronized and documented | Hosted Pages/release view after an owner-approved publish |
| Agent Files | Owner-only bounded inventory and explicit component details are implemented; no secret contents are exposed by default | Inspect actual installed paths and platform signatures on both devices |
| Build protection | Protected workflow builds only tagged source, signs/verifies an archive-member manifest, reports obfuscation accurately, and keeps owner inspection/uninstall/rollback available | Run the protected workflow and inspect its hosted artifact |
