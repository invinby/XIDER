# XIDER stabilization acceptance matrix — updated 2026-11-02

This matrix separates repository evidence from physical-device and hosted-service evidence. A passing source test does not mean the feature was exercised on a user's Mac or Windows PC.

## Local acceptance

| Area | Result | Evidence | Still needed |
|---|---|---|---|
| Telegram device navigation | Pass at source/unit level | `TG-BOT-SERVER/tests/test_tarped.py`, `tests/test_bot_ui.py`; hosted Python 3.12 suite: 1,805 passed, 4 skipped | Owner checks compact device/tools screens and Back history in Telegram |
| Owner approval switch and command hiding | Pass at source/unit level | Bot settings/UI and server operation tests in the full suite | Owner confirms preferred setting behavior in Telegram |
| Agent command/result contracts | Matrix generated for 195 workers and 7 Guardian commands on both platforms; source tests pass | `docs/x-map-command-matrix.json`; X-MAP suite: 11 passed | Exercise representative actions on both physical OSes; confirm each effect and correlated response |
| Windows source/installer fixtures | Pass | Hosted Python 3.12 suite: 556 passed; PowerShell 5.1 bootstrap, setup-selection, and transaction fixtures passed | Fresh install, permissions, recovery, and terminal-window behavior on a physical Windows device |
| macOS source/installer fixtures | Pass | Hosted Python 3.12 suite: 670 passed, 1 skipped; shell/integration fixtures available on Windows were run | Fresh install, update/rollback, permissions, camera, screenshot, GPS, and recovery on a physical Mac |
| Agent Files inventory | Pass at source/unit level; integrity remains unverified without a trusted per-file manifest | Windows/macOS inventory tests and Telegram owner callbacks included in full suite | Inspect actual installed component list/signature on each owner device |
| Windows build-cache cleanup | Policy and mocked cleanup tests pass; tagged Windows release package built successfully on GitHub Actions | `tools/test_build_nuitka_windows.py`; release workflow `41327801306`; both Windows executables are present in signed release assets | Local Windows cache cleanup and physical install still need owner-device confirmation |
| Feature registry | Pass | 510 unique features across 17 areas; registry tests and validator pass | None for the generated local registry |
| Signed release workflow | `v4.3.18` published successfully as a Windows + macOS source-preview release; three CI Python matrices and Unix installer validation passed | [Release v4.3.18](https://github.com/invinby/XIDER/releases/tag/v4.3.18); release workflow `41327801306`; Pages workflow `41331208446`; Ed25519 signatures verified locally against pinned key `521c56c0c89f0ddd` | Physical Windows/Mac installation and command-result checks remain owner-device work; macOS native package is not built |

## Version and copy snapshot

| Surface | Version/state on 2026-11-01 | Meaning |
|---|---|---|
| Last published GitHub release | 4.3.18 | Windows compiled preview + macOS source preview; current source has a prepared `4.3.19` candidate |
| GitHub `v4.3.18` | Windows compiled preview + macOS source preview | Windows Agent and Guard Keeper are Nuitka-compiled; macOS Agent, Guard Keeper, Safari, and Siri are explicitly reported `not_built` |
| Windows Pages installer default | `v4.3.18` | Live `win.txt` returns HTTP 200 and selects the signed v4.3.18 release assets |
| macOS Pages installer | `v4.3.18` source preview | Live `/mac` returns HTTP 200 and points at the v4.3.18 source-preview bootstrap; it states the native runtime is not Apple-signed or notarized |

The Telegram version picker can select an explicitly published version; the launchers validate the tag and verify the matching signed installer before execution. This update did not change the VPS. The release and Pages state were checked against GitHub and the live site after publication.

## Hosted release evidence — 2026-11-01

- Tag `v4.3.18` points to commit `f69f01faa648498ac3cb575c3b9466f8c477aaf9`; repository tree was clean at publication.
- Release workflow `41327801306` completed successfully. Unix installer validation and the Python 3.11, 3.12, and 3.14 gates passed; Windows Agent and Guard Keeper compilation and publication passed.
- Pages workflow `41331208446` completed successfully. Live `win.txt` and `/mac` each returned HTTP 200 and reference v4.3.18.
- Both `release-manifest.json` and `protected-build-manifest.json` verified with pinned Ed25519 key `521c56c0c89f0ddd`.
- Downloaded `XGENT-WDS-Windows.zip`, `Guard-Keeper-Windows.zip`, and `X-STAB-server.zip`; byte sizes and SHA-256 digests matched the signed release manifest.
- Protected build inventory reports Windows Agent and Guard Keeper as Nuitka 4.3.1 compiled, with `source_included: false`. It explicitly reports obfuscation as `not_applied`; compilation is not represented as obfuscation.
- The same inventory reports macOS Agent, macOS Guard Keeper, Safari, and Siri as `not_built`. The release is not a native signed/notarized macOS build and is not cross-platform complete.

This is hosted artifact and workflow evidence. It does not establish successful installation or runtime behavior on the owner's physical devices.

## Full hosted CI — 2026-11-02

- Main commit `9bba45d0962329766978502e98daf14ca5bc500a` passed `CI - Test Suite` run `41339547834` (run 431), including Python 3.11, 3.12, and 3.14 plus the Unix installer checks.
- The three Windows matrices installed the pinned `pyobfus==0.11.1`, then completed the full isolated component suites and Windows installer/rollback fixtures.
- In the Python 3.12 matrix: Telegram 1,805 passed / 4 skipped; Windows 556 passed; macOS 670 passed / 1 skipped; operations 30 passed; release/build tools 209 passed / 6 skipped / 2 subtests; deployment archive/signature checks 27 passed / 27 skipped.
- GitHub Pages workflow `41339547832` passed its static-source validation and published the validated site. This does not publish a new agent release or deploy the bot VPS.

The next source version is prepared as `4.3.19`; it has not yet been tagged or published. The hosted `v4.3.18` manifests remain unchanged and correctly say obfuscation was not applied.

## Earlier full local verification — 2026-11-01

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

## Follow-up build-protection work — main CI passed; release candidate prepared

- Both Nuitka builders now pin PyObfus 0.11.1, require offline preflight with zero parse/high-risk findings, verify generated syntax and cross-file names without executing transformed source, and emit `xider-obfuscation-report.json`.
- Local Windows transform: 50 source files processed, 2,875 names transformed; both Windows executables built. The Agent passed the embedded valid-signature, tamper-rejection, and durable-replay verifier. The report was accepted by the protected manifest builder as `obfuscated` for the actual Windows packages.
- macOS transform: 59 source files processed, 3,318 names transformed; syntax/name checks and transformed-module import smoke passed on Windows. This is not a native macOS build or Mac-device test.
- Focused local checks: protected manifest/workflow/release catalog 30 + 31 passed; Windows and macOS file inventory 12 + 13 passed. Source changes are pushed on `main` and full hosted CI passed at `9bba45d`; the published v4.3.18 release still truthfully says obfuscation was not applied.
- The `4.3.19` release candidate adds the Windows compiled obfuscated package and keeps macOS labeled as a source preview until native Mac compilation and Apple signing/notarization are available. Remaining gates are the exact-tag release build, signed artifact inspection, and owner-device installation/behavior checks. PyObfus obfuscation raises inspection effort; it is not encryption and does not prevent reverse engineering.

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
