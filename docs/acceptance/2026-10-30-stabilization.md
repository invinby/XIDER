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
| Windows build-cache cleanup | Policy and mocked cleanup tests pass; v4.3.22 Windows release package built successfully on GitHub Actions | `tools/test_build_nuitka_windows.py`; release workflow `41374030576`; both Windows executables are present in signed release assets | Local Windows cache cleanup and physical install still need owner-device confirmation |
| Feature registry | Pass | 510 unique features across 17 areas; registry tests and validator pass | None for the generated local registry |
| Signed release workflow | `v4.3.22` is the latest GitHub prerelease: compiled Windows Agent/Guard Keeper + macOS source preview | [Release v4.3.22](https://github.com/invinby/XIDER/releases/tag/v4.3.22); release workflow `41374030576`; Pages workflow `41377881289`; both Ed25519 manifests and both detached installer signatures verified; Windows ZIP hashes and every member hash match the signed inventory | Physical Windows install/update/rollback and Mac source-preview acceptance remain open; native Mac packages remain `not_built` |

## Version and copy snapshot

| Surface | Version/state on 2026-11-02 | Meaning |
|---|---|---|
| Latest published prerelease | 4.3.22 | Windows Agent + Guard Keeper compiled; macOS signed source preview; no native Mac runtime package |
| Latest non-prerelease from GitHub Releases API | 4.2.1 | GitHub classifies v4.3.22 as prerelease; do not report it as stable |
| GitHub `v4.3.22` | Compiled Windows + macOS source preview | Windows Agent/Guard Keeper: PyObfus 0.11.1 (50 files, 2,875 names) and Nuitka 4.3.1. macOS Agent, Guard Keeper, Safari, and Siri: `not_built`; runtime is not Apple-signed or notarized |
| Windows Pages installer default | `v4.3.22` | Live `win.txt` returns HTTP 200 and selects signed v4.3.22 installer/release assets |
| macOS Pages installer | `v4.3.22` source preview | Live `/mac` returns HTTP 200 and selects the signed source-preview installer; runtime is not Apple-signed or notarized |

The Telegram version picker can select an explicitly published version; the launchers validate the tag and verify the matching signed installer before execution. The release includes a signed server source bundle, but this workflow did not deploy the VPS. Release and Pages state were checked against GitHub and the live site after publication.

## Hosted release evidence — v4.3.22 (2026-11-02)

- Tag `v4.3.22` points to exact source commit `0f23d4a2a96977534469e1ca2c5973bfd6559119`. Release workflow `41374030576` passed exact-tag CI on Python 3.11, 3.12, and 3.14, Unix installer/docs validation, Windows Agent/Guard Keeper compilation, and signed inventory publication. The release is a prerelease, not a stable release.
- Pages workflow `41377881289` succeeded for the same commit. Live `/`, `/ru.html`, `/win.txt`, and `/mac` returned HTTP 200; RU/EN pages and both short installers reference v4.3.22.
- Downloaded `XGENT-WDS-Windows.zip` (76,681,733 bytes; SHA-256 `9f0d5e2785e1a3f1272756a090b4fee5e865068e042b2c1065843f0fce200f94`) and `Guard-Keeper-Windows.zip` (11,530,582 bytes; SHA-256 `7385e9bb6e6c5580a6532f3d0fff96b2411923fad3f314e50ca2dbc629de21ec`). Both match GitHub's asset digests and signed inventory; every ZIP member's size/hash matched, archives passed integrity checks, and compiled ZIPs contain no `.py`, `.pyc`, `.pyo`, `.pyi`, or `.pyw` files.
- Independently verified `release-manifest.json` and `protected-build-manifest.json` with pinned Ed25519 key `521c56c0c89f0ddd`. Windows Agent and Guard Keeper are each reported as PyObfus 0.11.1 obfuscated (50 source files, 2,875 names) and Nuitka 4.3.1 compiled. Compilation is not counted as obfuscation. The Windows EXEs do not claim Authenticode signing.
- Independently verified the detached OpenSSH signatures on `XIDER-install-windows.ps1` and `XIDER-install-macos.sh`. The release contains a Windows compiled package and macOS source-preview installer. Native macOS Agent, Guard Keeper, Safari, and Siri are `not_built` / `not_applied`; the macOS runtime is not Apple-signed or notarized.
- Audited the separate owner-preview artifact for the same source commit (`XGENT-MCS-macOS`, run `41372218597`, artifact `13109951744`): outer SHA-256 `2877eb8e00e443486fbffc31e2bad5e0e8c0cd87a176beaf7cd4468dda300a49`; nested Agent bundle SHA-256 `50558071a36a5c436d602a0fe731a4b85ab31f526b67793c5f1df851a362b869`; nested Guard Keeper SHA-256 `b4e22957c8e7956a27723e97cd202e0a6e18ab2f5d0dc9152baefc8188d5c5a6`. Both nested ZIPs pass integrity checks, contain no Python source/bytecode or separate `.pem`, `.key`, `.p12`, or `.pfx` files, and contain arm64 Mach-O payloads. `.env.example` is present; no `.env` secrets file was found. This is an unsigned Actions preview, not a GitHub Release asset or a signed protected-build report. Its included PyObfus report records 59 first-party files and 3,313 transformed names; Agent, Guard Keeper, Safari, and Siri are each marked `applied` in this preview.
- The preview's OpenCV-bundled `libgnutls.30.dylib` contains eight parseable private-key blobs. Their public-key fingerprints exactly match the eight blobs in the official [PyPI OpenCV 4.12.0.88 macOS arm64 wheel](https://pypi.org/project/opencv-python/4.12.0.88/); three also match entries in upstream GnuTLS [`lib/crypto-selftests-pk.c`](https://gitlab.com/gnutls/gnutls/-/blob/master/lib/crypto-selftests-pk.c). The library binary hashes differ, so this establishes matching third-party key material, not identical binaries; the origin of the other five blobs was not independently traced. No separate XIDER key-container file was found. Do not describe the preview as containing zero private-key material, and do not treat it as the published/signed macOS runtime.
- The project Prank Hub is represented in source as one catalog with five filters, search, favorites, action cards, and Stop All pinned first. Physical effects/restoration and live Telegram visual acceptance still require owner-device checks.
- CI, signed artifact, and live Pages checks do not prove physical install/update/rollback or every Telegram → MQTT → device command result. Current device acceptance remains separate from hosted evidence.

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

The `v4.3.19` release is published. Its signed inventory reports Windows obfuscation as applied and native macOS components as `not_built`; historical `v4.3.18` manifests remain unchanged.

## Hosted release evidence — v4.3.19 (2026-11-02)

- Tag `v4.3.19` points to commit `7be985a973d3274fbcff20485db113b877a6d2a4`. This commit corrected the Windows PE numeric version and RU/EN Pages version slots after the first release gate caught both mismatches.
- Release workflow `41341904538`: source validation, Unix installer validation, and full suites on Python 3.11/3.12/3.14 passed; Windows Agent and Guard Keeper compiled; publication and Pages dispatch succeeded.
- The release contains 17 uploaded assets: Windows Agent and Guard Keeper executables/archives, X-STAB server source bundle, signed installers, and the two signed manifests. The hosted workflow verified both Ed25519 manifests against key ID `521c56c0c89f0ddd` before upload.
- The protected inventory reports Windows Agent and Guard Keeper as PyObfus 0.11.1 obfuscated and Nuitka 4.3.1 compiled; 50 source files and 2,875 names were transformed with syntax/name-binding verification. Compilation is not counted as obfuscation.
- Native macOS Agent, Guard Keeper, Safari, and Siri are `not_built`; their obfuscation is `not_applied` in this release. macOS bootstrap/installer scripts are XIDER-signed, but the runtime is not Apple-signed or notarized.
- Pages workflow `41345558775` succeeded. Live `/`, `/ru.html`, `/win.txt`, and `/mac` returned HTTP 200; RU/EN pages show 4.3.19 and both launchers select v4.3.19.
- No VPS deployment or physical device install/update/rollback was performed by this release workflow.

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

Until these device steps are completed, physical behavior and live Telegram interaction remain unverified. Hosted CI, release and Pages publication evidence is recorded above; it does not prove runtime behavior on an installed device.

## Follow-up build-protection work — v4.3.19 published; Mac native build remains open

- Both Nuitka builders now pin PyObfus 0.11.1, require offline preflight with zero parse/high-risk findings, verify generated syntax and cross-file names without executing transformed source, and emit `xider-obfuscation-report.json`.
- Local Windows transform: 50 source files processed, 2,875 names transformed; both Windows executables built. The Agent passed the embedded valid-signature, tamper-rejection, and durable-replay verifier. The report was accepted by the protected manifest builder as `obfuscated` for the actual Windows packages.
- macOS transform: 59 source files processed, 3,318 names transformed; syntax/name checks and transformed-module import smoke passed on Windows. This is not a native macOS build or Mac-device test.
- Main CI after metadata corrections passed in run `41341058916` on Python 3.11, 3.12, and 3.14. The release-tag matrix also passed in run `41341904538`; Windows build and publish jobs succeeded.
- Published release [v4.3.19](https://github.com/invinby/XIDER/releases/tag/v4.3.19) pins source commit `7be985a973d3274fbcff20485db113b877a6d2a4`. The protected inventory reports Windows Agent and Guard Keeper as PyObfus 0.11.1 obfuscated (50 source files, 2,875 names) and Nuitka 4.3.1 compiled; the hosted release workflow verified the signed manifests before publication.
- Mac Agent, Guard Keeper, Safari, and Siri remain `not_built` with `not_applied` obfuscation; bootstrap/installer scripts are publisher-signed, but the runtime is not Apple-signed or notarized. Physical install/update/rollback and runtime checks remain unverified. PyObfus increases inspection effort; it is not encryption and does not prevent reverse engineering.
- The `build-agents.yml` owner-triggered `workflow_dispatch` path and owner-only `xider-local-prep-*` branch path build platform previews and upload workflow artifacts. The branch path builds both Windows and unsigned macOS; the Mac job uses `xider-macos-preview`, so it does not wait for Apple signing secrets or the signing environment's approval rules. Preview artifacts are not signed/notarized distributable releases. Tagged release builds remain behind the full CI gate and use the protected signing environment.

## macOS preview probe failure — 2026-11-02

- The first hosted unsigned preview compiled the Agent, Guard Keeper, and Layers Nuitka apps, then failed the command-approval bundle probe: the obfuscated `command_module_loader` no longer exposed the dynamically accessed `load_component` attribute.
- Cause: `xgent_mcs.py` loads the trusted helper as a module object and calls `.load_component(...)`; PyObfus cannot safely rewrite a dynamic module-object attribute from static name binding alone.
- Fix in source: `tools/pyobfus.yaml` explicitly preserves the names resolved dynamically across the command-approval loader/signing/replay/execution modules; internal implementation names remain eligible for obfuscation. The build tool passes this config explicitly and records the preserved API boundary in its report.
- The preview job condition is narrowed: unsigned preview builds run only for owner pushes to `xider-local-prep-*`; workflow dispatch remains available to the repository owner, and tagged release builds require the release-ready flag.
- First config-backed retry preserved `load_component` and advanced further, then exposed another dynamically resolved API (`shell_timeout`) in the same service-free probe. The preserved-name set was expanded to the full external module/class/method contract.
- Targeted local transform with PyObfus 0.11.1 accepted the config, transformed 59 macOS source files / 3,309 names, and confirmed all 11 external command-approval API names remain present; generated syntax and static name binding passed. Transformed code was not executed by this local check.
- Hosted unsigned preview run `41354547268` on commit `b569b903b3613f19ce91839b5a2fc380d4938fd1` passed the compiled-bundle probe and package checks, then uploaded artifact `XGENT-MCS-macOS` (113,464,495 bytes; SHA-256 `e73f0e51e18a3e0347eb3e9b5e48c9560c3b35a01b9e08ad8dadbb1d683f94ce`). It contains `XIDER.app`, `XIDER-Guard-Keeper.app`, `XIDER-Layers.app` (the shared Safari/Siri runtime), `.env.example`, and an obfuscation report. The ZIP checks reject first-party Python/script files and credential-like/private-key material. The service-free probe confirms command loader presence, valid Ed25519 approval, tamper rejection, and durable replay rejection.
- This is hosted build/probe evidence only. The artifact is explicitly unsigned and not distributable; it has no Apple Developer ID signature/notarization and no protected signed release manifest. No physical Mac install or runtime behavior is claimed.
- Full CI run `41354547389` passed on Python 3.11, 3.12, 3.14 and Unix installer fixtures.
- The owner-only Windows preview lane is now enabled. Current combined preview run `41355642609` on commit `09f23b0c09103262530e66f62d5f5c30d53e7f33` completed the macOS build, bundle probe, ZIP checks, and artifact upload. Artifact `XGENT-MCS-macOS`: 113,464,809 bytes; SHA-256 `b8e93656a03c0f14d0f49b42745bca59aef2676bd86e31e556c4ad091da3c712`.
- The Windows job then completed successfully: `Build Windows Agent and Guard Keeper as compiled one-file apps` and `Upload Windows EXE Artifact` both passed. Artifact `XGENT-WDS-Windows` is 153,385,931 bytes with SHA-256 `14eee75d3b47bfacb59e3db300cdb2515d9280b33875ca13a24ac628abcd74e8`.
- The current run uploaded separate Windows and macOS preview artifacts. `Package Guard Keeper and server components`, signed build inventory, and GitHub Release publication were skipped. The aggregator needs an explicit owner-preview condition because its release CI dependency is intentionally skipped; this is being corrected before the next preview run. No new release or signed protected inventory was created.
- CI run `41355642686` on the same commit completed successfully. This is source/CI evidence, not physical device acceptance.

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
| 7. Windows terminal flash | Read-only process and Task Scheduler evidence does not show a console child under XIDER. One saved PowerShell capture is a child of Antigravity's `agy.exe` and runs adb/VNC work. | This attributes that captured event to Antigravity, not XIDER; it does not prove every reported flash has the same source |
| 8. Install/update/version selection | Both Pages launchers verify selected release artifacts; fixtures cover platform selection, retention, and rollback paths; release defaults are labeled accurately | Physical install, update, and rollback on Windows and Mac |
| 9. Storage/temp files | XIDER-owned media temp cleanup is bounded on both platforms; Windows build cleanup targets only recognized successful-build intermediates. Existing build cache was preserved. | Successful Windows package build and installed-device storage measurement |
| 10. Versions and RU/EN copy | Source, previews, stable package defaults, generated registry and local copy tests are synchronized and documented | Hosted Pages/release view after an owner-approved publish |
| Agent Files | Owner-only bounded inventory and explicit component details are implemented; no secret contents are exposed by default | Inspect actual installed paths and platform signatures on both devices |
| Build protection | Protected workflow builds only tagged source, signs/verifies an archive-member manifest, reports obfuscation accurately, and keeps owner inspection/uninstall/rollback available | Run the protected workflow and inspect its hosted artifact |

## Signed VPS update · 2026-11-02

- GitHub Actions run `41359628318` built and signed the server source bundle for
  exact commit `d690fd6fa4c5bb56c25afc9bc4f6b775156ce4a8`; release:
  [server-deploy-20261102](https://github.com/invinby/XIDER/releases/tag/server-deploy-20261102).
- The release manifest was verified locally and again on the VPS by its
  root-owned verifier before installation. `xider-server-ops update` applied
  the bundle transactionally and created
  `/var/backups/xider/xider-20261101T225820Z-566173.tar.gz`.
- Post-update: `xider-bot.service` is `enabled` and `active/running`,
  `NRestarts=0`; its PID changed from `4012877` to `567824`. The live
  `TG-BOT-SERVER/bot.py` normalized SHA-256 matches the checked-out source
  (`fc9a7b1e…b114715b`). `/etc/xider/bot.env` remains present with mode `0640`.
  These are process and file-integrity checks, not a Telegram→MQTT round-trip.
- The sanitized device registry did not change: Windows `DESKTOP-Q46JPA2` is
  still on `4.3.15`, online, with update mode `frozen`; `MacBook-Air.local` is
  on `4.2.1` and offline. Neither device currently reports Guardian/Safari/Siri
  versions. The VPS source update does not update endpoint agents.

## Combined owner preview build · 2026-11-02

- Workflow run `41358849463` on commit `d690fd6fa4c5bb56c25afc9bc4f6b775156ce4a8`
  completed successfully. Full CI runs `41358844118` (`main`) and
  `41358849529` (preview branch) also passed on that exact commit.
- The unsigned Windows artifact `XGENT-WDS-Windows` is 153,385,794 bytes,
  SHA-256 `fc4d8c0cf211b38b5a8c3aad2931e58aeaf5d1b35302bd8948bad6fca5531308`.
- The unsigned macOS artifact `XGENT-MCS-macOS` is 113,464,362 bytes,
  SHA-256 `0067dcf5f7359fdf7e2b4864e59897977bc60137825300c65be307d327cd09b4`.
- The repaired `pack-components` job ran despite the expected skipped branch
  release gate and uploaded `XIDER-Components`: 204,996,876 bytes,
  SHA-256 `20b257aae9b00c13464f86ffbf17d55709dd15541a6df8970ec6d1424a240222`.
  The workflow's archive checks passed for Agent, Guard Keeper, macOS Layers,
  X-STAB server, and source packages. The preview did not publish a GitHub
  Release or signed protected build inventory. It is not an Apple-signed or
  notarized macOS release; physical installation/update/rollback remains open.

## Signed Windows preview release and macOS owner build · 2026-11-02

- Release [v4.3.21](https://github.com/invinby/XIDER/releases/tag/v4.3.21) is
  published as a GitHub prerelease from exact source commit
  `382a46150cb179569dbbf9a02672b116f333e5ff`. The earlier v4.3.20 tag was
  not moved; its release gate failed on stale Windows PE and bilingual Pages
  version metadata, so v4.3.21 superseded it.
- Release workflow `41363443612` completed successfully, including the
  Python 3.11/3.12/3.14 CI matrix, Unix installer fixtures, Windows build and
  publication. The signed release manifest and protected build manifest both
  verified with the pinned Ed25519 key; all 14 release asset sizes and
  SHA-256 hashes matched GitHub's release asset metadata.
- The Windows Agent and Guard Keeper were PyObfus 0.11.1 transformed and
  Nuitka 4.3.1 compiled. The protected manifest reports 50 source files and
  2,875 transformed names. It does not claim Windows Authenticode signing.
- Windows and macOS Pages installer endpoints returned HTTP 200 and select
  v4.3.21. RU/EN pages show the same source version. These are hosted
  publication checks, not proof of installation on an endpoint.
- Owner preview workflow `41363558411` built macOS apps for Agent,
  Guard Keeper, and shared Layers runtime used by Safari/Siri. Artifact
  `XGENT-MCS-macOS` is 113,464,729 bytes with SHA-256
  `5cf07174e6f4490d1589b661b984a5012a656899a5269caab9af260956a98407`.
  It is explicitly unsigned and unnotarized. The published v4.3.21 protected
  manifest therefore continues to report native macOS packages as
  `not_built`; the preview is not a distributable release.
- Owner-preview workflow `41363558411` later completed both platform builds
  and uploaded `XIDER-Components` (205,000,195 bytes; GitHub artifact digest
  `sha256:e0dab742799a18febbb112d00cc3685eb83ee32669984cf62b43cb4f4933d6bd`).
  Its Windows artifact is 153,385,748 bytes, SHA-256
  `decc3ee7f37087b09b18275c5cd6fab0c48862ca156d737f0584be479135c45b`.
  The macOS artifact and hash are recorded above. Preview publication and a
  signed preview inventory were skipped by design. The signed v4.3.21 release
  remains the official prerelease and contains its own Windows compiled assets.
- Physical Windows/Mac installation, update, rollback, permission flows,
  actual feature effects, and Telegram→MQTT→device result round trips remain
  unverified. No CI or hosted build result is counted as physical acceptance.

## Windows console-flash attribution · 2026-11-02

- `console-blink-capture-20261101-021829.jsonl` records a `powershell.exe`
  whose parent is `agy.exe` from Antigravity IDE. Its command invokes the
  user's `adb.exe`, writes a VNC launcher script, pushes it to Android, and
  starts a VNC server. The saved process record identifies this captured
  console activity as Antigravity work, not an XIDER child process.
- Across the saved console snapshots there are repeated records of
  `powershell.exe` under `agy.exe`; these are capture records, not a count of
  distinct launches. No captured PowerShell/cmd child has an XIDER Agent or
  Guard Keeper parent. Current XIDER Scheduled Tasks run the packaged Agent
  and Guard Keeper executables directly.
- The local `install-report.json` records the earlier Windows install as
  Agent/Guard Keeper `4.3.15`, with bot acknowledgement `not_verified`. It
  predates the v4.3.21 release and is not proof of installation or MQTT
  freshness for v4.3.21.
- This is strong attribution for the captured window only. If another flash
  occurs after Antigravity's adb/VNC action is closed, a fresh parent-process
  capture is still needed before attributing that event.

## CI regression follow-up · 2026-11-02

- Commit `e5865a959ff9fa090d01c301860bab0ef6b5148e` was pushed to `main` and
  triggered CI run `41368331139`. Unix installer/documentation checks passed;
  Python 3.14 found two old UI/check fixtures that still expected cached Agent
  versions without a fresh heartbeat, plus a Windows-runner assumption in the
  POSIX macOS wallpaper-path fixture. This run is **failed**, not a release
  gate pass.
- Corrected those fixtures and changed wallpaper validation to require the
  absolute POSIX path form used by macOS. Fresh focused Python 3.12 checks pass:
  bot regressions 4 passed and macOS wallpaper regression 1 passed. Full main
  CI must pass on the follow-up commit before a new signed preview is prepared.

## Follow-up CI and v4.3.22 source candidate · 2026-11-02

- Commit `417aeb3cc5ec181f4c34c62609b911a6a6e71f22` passed main CI run
  `41369298611` on Python 3.11, 3.12, and 3.14, including Unix installer/docs
  validation. Python 3.12 recorded: Telegram 1,814 passed / 4 skipped;
  Windows 556 passed; macOS 671 passed / 1 skipped; operations 30 passed;
  release/build tools 209 passed / 6 skipped / 2 subtests; deployment
  archive/signature checks 27 passed / 27 skipped; Windows install/bootstrap/
  rollback fixtures passed. This is hosted CI evidence, not physical-device
  acceptance.
- `main` now carries synchronized source metadata `4.3.22` and a bilingual
  release note. This is only a source candidate: no `v4.3.22` tag or release
  exists yet. The published preview remains v4.3.21 until exact-source
  release CI, signed inventories, hosted assets, and Pages checks complete.
- The source candidate contains the fresh-heartbeat version gate and truthful
  update display, partial Stop All details, unknown-platform prank disabling,
  and original macOS wallpaper backup restoration. Its release note keeps
  Apple codesign/notarization limitations explicit.
- Commit `420d575b7571d560f72463b7a6609b4bbe415a05` published the updated
  Pages source snapshot; workflow `41370317059` passed and all four live
  endpoints returned HTTP 200. English/Russian landing pages show source
  `4.3.22`; `win.txt` still correctly selects the latest published `v4.3.21`.
- Candidate CI run `41370317091` failed all three Python matrices on the same
  Windows PE metadata assertion: the text versions had moved to `4.3.22.0`,
  but the fixed numeric `filevers`/`prodvers` tuples were still `4.3.21.0`.
  Both numeric tuples are now corrected to `(4, 3, 22, 0)`; no v4.3.22 tag
  exists until the follow-up exact-source CI passes.
