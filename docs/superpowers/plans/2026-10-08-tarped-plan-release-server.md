# Release, Canary Update, and Server Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` after owner approval. Keep updater, bot/VPS, and device version states separate.

**Goal:** Проверять доступные подписанные версии, выполнять backup/install/health/rollback и показывать честный статус устройств и VPS.

**Architecture:** Сначала расширить проверяемые release manifests и существующие transactional updaters, затем добавить bot update coordinator и отдельную серверную карточку. Deployment и device acceptance выполняются отдельно от CI.

**Tech Stack:** Python, pytest, Ed25519 release manifests, PowerShell/bash deployment fixtures, MQTT operation IDs, aiogram.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`, разделы 5.3, 12, 14.

## Global Constraints

- Verify signed manifest and checksum before extracting or starting an artifact.
- Distinguish bot/VPS, Windows/macOS agent, and companion component versions.
- Announce installation only after agent acknowledgment and health check.
- Canary health must pass before any broader rollout.
- VPS commands remain fixed allowlist; secrets never enter bot messages/logs.
- Live VPS and physical updater checks are not implied by unit or fixture tests.

## Review Focus

1. Manifest or archive tampered — reject before extraction.
2. Update process dies midway — preserve last working version/config and recover deterministically.
3. Device goes offline after command publish — show unknown/pending, not installed.
4. Canary fails health check — block all remaining targets and attempt tested rollback.
5. Guest requests server view — redact details and expose no mutation controls.

---

**Дата:** 2026-10-08  
**Статус:** ждёт одобрения реализации  
**Спецификация:** разделы 5.3, 12 и 14 [TARPED redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**Зависимости:** UI/RBAC и capability contract; shell approval — только для отдельного произвольного shell, не для server allowlist.

## Цель

Разнести три разные сущности: версия Telegram-бота/VPS, версия Windows/macOS агента, версии companion-компонентов. Источником доступных версий должны быть подписанные GitHub Releases/manifest; версию считать установленной только после ответа агента и health-check. Существующие backup/swap/rollback механизмы сначала тестировать и расширять, не заменять вслепую.

## Этапы TDD

### Implementation tasks

#### Task 1: Signed release catalog and explicit version state

**Files:** Extend `TG-BOT-SERVER/release_catalog.py` and version callbacks/views in `TG-BOT-SERVER/bot.py`; test `TG-BOT-SERVER/tests/test_tarped.py` or focused `test_release_catalog.py` plus version UI tests; preserve `tools/build_release_manifest.py` contract.

**Interface:** Reuse `Release` and `Catalog`; add a pure `verify_release_metadata(release: Release, manifest: dict) -> bool` only if the existing catalog does not already provide equivalent signature/checksum validation. Keep `installed`, `available`, `unsupported`, and `unknown` separate in UI payload.

- [ ] Add tests for valid signed manifest, tampered hash, bad signature, missing asset, prerelease ordering, OS/component mismatch, stale/unknown tag callback and selecting exactly the displayed signed release.
- [ ] Render current/available versions, changelog, OS, signature state and release date separately; test selection of one exact prior signed tag for rollback, with callback revalidation of tag/component/OS/device/role/grant.
- [ ] Run `python -m pytest tests/test_tarped.py -q` from `TG-BOT-SERVER`; expected: newly added invalid-release cases fail closed.
- [ ] Implement only missing validation; do not duplicate manifest cryptography already present in `tools`.
- [ ] Run bot catalog tests and `python -m pytest tools/test_build_release_manifest.py tools/test_sign_server_bundle.py -q` from repo root; expected: valid artifacts pass and tampered ones reject.
- [ ] Commit catalog/test changes.

#### Task 2: Transactional updater behavior and rollback

**Files:** Modify existing `XGENT-MCS/update_package.py`, Windows/Mac agent update handlers, `deploy` scripts only where a failing test exposes a gap; reuse current platform updater tests.

**Interface:** Every update reports an operation id and one of `queued`, `downloading`, `verifying`, `installing`, `reconnecting`, `health_check`, `rolling_back`, `succeeded`, `rolled_back`, `failed`, `unknown`; never infer success from publish acceptance. `unknown` is reconciled only by fresh evidence for the same operation id.

- [ ] Add/extend tests for bad signature, corrupt archive, interruption after backup, health timeout, rollback success and rollback failure while preserving `.env`/user config.
- [ ] Test that rollback only selects a compatible previously signed release and that temporary/partial files are cleaned after success or rollback.
- [ ] Run `python -m pytest tests/test_agent_update.py -q` in `XGENT-WDS`, and `python -m pytest tests/test_agent_update.py tests/test_update_package.py -q` in `XGENT-MCS`; expected: every covered failure state has a deterministic result.
- [ ] Implement missing state mapping by reusing existing atomic swap/backup functions.
- [ ] Run platform-appropriate `deploy/tests/test_bootstrap_transaction.ps1` or `test_bootstrap_macos_transaction.sh`, plus `test_safe_extract.py` and `test_verify_server_bundle.py`.
- [ ] Commit transaction fixes separately per platform if their rollback behavior differs.

#### Task 3: Canary coordinator

**Files:** Create `TG-BOT-SERVER/update_coordinator.py`, `TG-BOT-SERVER/update_store.py` and their tests `TG-BOT-SERVER/tests/test_update_coordinator.py`, `TG-BOT-SERVER/tests/test_update_store.py`; wire into `bot.py` after tests pass.

**Interface:** `UpdateOperation(operation_id: str, batch_id: str, device_id: str, release_tag: str, component: str, state: str, changed_at: int, detail: str)`. `transition(operation: UpdateOperation, event: str, now: int) -> UpdateOperation` is pure. `UpdateStore.save(operation)`, `get(operation_id)`, and `list_active(batch_id)` persist sanitized operation state atomically in the existing JSON-store style; no credentials or package contents are stored. Bot restart reloads pending operations and reconciles them against fresh device heartbeat/operation responses before advancing a batch.

- [ ] Add tests for `queued → downloading → verifying → installing → reconnecting → health_check → succeeded`, rollback transitions, duplicate event, stale heartbeat, timeout to `unknown`, fresh-operation reconciliation after bot restart, corrupt persisted state failing closed, canary failure, and no mass rollout before canary success.
- [ ] Run `python -m pytest tests/test_update_coordinator.py tests/test_update_store.py -q`; expected: failures before the state machine/store exist.
- [ ] Implement the transition table; active states may time out to `unknown`; only a fresh matching operation response can reconcile `unknown` to a concrete state. Show measured stages/timestamps, no fabricated percentage.
- [ ] Persist state through `UpdateStore`; test atomic save/reload and ensure the store cannot advance a batch without a fresh, successful canary health result. A missing/corrupt store must retain an `unknown`/blocked batch status, never reset to “no active update”.
- [ ] Wire callbacks with idempotency by `operation_id`; test repeat taps and offline device.
- [ ] Run coordinator, update store, bot UI and device store tests; expected: only a fresh canary health event unlocks next rollout, including after a bot restart.
- [ ] Commit coordinator and tests.

#### Task 4: Server health and safe operations

**Files:** Modify `TG-BOT-SERVER/server_ops.py`, server menu handlers in `bot.py`, and `deploy/xider-server-ops.sh`; test `TG-BOT-SERVER/tests/test_server_ops.py`.

**Interface:** Preserve `Result`, `status()`, `logs(lines=40)`, `restart()`, `update()`, `rollback()`, `metrics()` and `run_terminal(command)`; status fields must include `checked_at` and independent service/broker/VPS states.

- [ ] Add tests for stale metrics, unavailable host, bounded/sanitized logs, owner-only mutation callback, explicit confirm/cancel, expired/replayed confirmation and fixed allowlist argv.
- [ ] Assert separate process, Telegram polling/webhook, MQTT broker, CPU/RAM/disk freshness states; one successful ping must not render the whole stack healthy.
- [ ] Run `python -m pytest tests/test_server_ops.py -q` from `TG-BOT-SERVER`; expected: missing freshness/redaction cases fail.
- [ ] Implement timestamp/redaction/role gaps without adding arbitrary command execution.
- [ ] Run server ops tests and `deploy/tests/test_update_server.sh`; expected: only allowed operations run and failures return non-success status.
- [ ] Commit server-card/ops changes.

#### Task 5: Companion component status

**Files:** Read current companion update/status handlers in WDS/MCS; modify bot version presentation and add tests in `test_bot_ui.py` plus agent update suites.

**Interface:** Render each component as `installed`, `available`, `not_installed`, or `unknown` with its own version/tag; never auto-reinstall a removed component.

- [ ] Inventory the exact companion identifiers and status/update payloads for `Guard Keeper`, `Safari`, and `Siri` on Windows and macOS; a running agent must not be treated as proof that any companion is installed.
- [ ] Add tests for each named companion in installed, available, not-installed, unknown, unsupported-on-OS, stale/offline, and owner-removed states; keep `not_installed` distinct from `unsupported` and never infer a version from the agent version.
- [ ] Run relevant bot and platform update tests; expected: missing companion stays `not_installed`.
- [ ] Implement separate per-companion status mapping only; no anti-uninstall, hidden restart, or recovery-after-owner-delete behavior. Show supported local stop/uninstall paths in operator help.
- [ ] Rerun tests and commit component presentation.

#### Task 6: Per-action check freshness

**Files:** Create `TG-BOT-SERVER/check_store.py` and `tests/test_check_store.py`; show the safe summary in action/status cards in `bot.py`.

**Interface:** `CheckRecord(device_id: str, callback: str, platform: str, agent_version: str, checked_at: str, latency_ms: int | None, outcome: str, correlation_id: str | None)` where outcome is `succeeded`, `failed`, `unsupported`, `needs_permission`, `timed_out`, or `unknown`. Provide `record_check(record: CheckRecord) -> None` and `get_last_check(device_id: str, callback: str) -> CheckRecord | None`. Persist only the latest bounded sanitized result per device/action; never persist response payload, coordinates, file names/contents, screenshots or shell output.

- [ ] Add tests for a current check, missing record, old last-known record, unknown outcome, device separation, atomic save/reload and bounded store size; old records keep their timestamp and are not presented as current.
- [ ] Run `python -m pytest tests/test_check_store.py -q`; expected: missing store/model behavior fails.
- [ ] Save a check only after the action result arrives; MQTT publish acceptance alone records `unknown`, not `succeeded`.
- [ ] Add UI tests showing checked time, OS/version, outcome and latency to owner or a granted user; guest receives only the safe public summary.
- [ ] Rerun check-store and bot UI suites; commit the status model and display.

#### Task 7: Sanitized diagnostic export

**Files:** Create `TG-BOT-SERVER/diagnostics.py` and `tests/test_diagnostics.py`; wire an owner-only export callback in `bot.py`; consume only the status/capability/log contracts from prior tasks.

**Interface:** `build_diagnostic_snapshot(*, generated_at: str, versions: dict[str, str], statuses: dict[str, object], capabilities: list[dict], checks: list[dict], log_tail: list[str], correlation_ids: list[str]) -> bytes` returns schema-1 UTF-8 JSON capped at 256 KiB. Include versions, capability state/source/check time, per-action last-check summaries, service/VPS state, bounded sanitized log tail and correlation IDs; exclude credentials, private keys, coordinates, file contents and raw shell/stdout.

- [ ] Add tests `test_diagnostics_include_versions_capabilities_and_correlation_ids`, `test_diagnostics_redact_secrets_and_exclude_sensitive_payloads`, and `test_diagnostics_bundle_is_bounded`.
- [ ] Run `python -m pytest tests/test_diagnostics.py -q`; expected: the schema and redaction/bounds assertions fail before the builder exists.
- [ ] Implement a pure JSON builder using existing redaction helpers; reject malformed/unbounded inputs and do not persist the generated bundle.
- [ ] Add bot tests proving only the owner can request the export and it is delivered as one document without leaking a raw tool result into the chat.
- [ ] Rerun diagnostics and bot UI suites; commit the builder and owner export flow.

## Коммиты и приёмка

1. Signed release catalog and compatibility tests.
2. Agent transaction/health/rollback tests and fixes.
3. Canary coordinator and UI statuses.
4. Owner-only VPS operations and redacted diagnostics.
5. Companion version presentation.
6. Per-action freshness state and UI.
7. Sanitized diagnostic export.

Успешный CI — только кодовый слой. Реальный update/rollback одного Mac и одного Windows, а также VPS deployment — отдельная приемка; каждый неисполненный слой остаётся открытым.
