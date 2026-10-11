# One-Time Device Command Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` after owner approval. Do not enable shell execution until the trust-boundary task passes.

**Goal:** Требовать явное одноразовое подтверждение владельца в Telegram для точной shell-команды и точного устройства перед её запуском.

**Architecture:** Бот хранит ограниченный по времени pending request и публикует его только после повторной проверки владельца/цели/хэша. Агент принимает лишь протокольно авторизованное сообщение из доверенного MQTT-пути; allowlisted power/server actions остаются отдельными.

**Tech Stack:** Python, aiogram FSM/callbacks, MQTT ACL/signature contract, `xgencrypto.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`, раздел 10 и критерии 14.

## Global Constraints

- Exact command, target device, owner, nonce/hash and expiry are one immutable pending object.
- Default TTL is 120 seconds; consume is atomic and one-time.
- No raw command is published from the FSM before owner approval.
- Replay, altered payload, stale role, blocked requester or a shell message without a valid server-only signature never executes.
- Bound input, runtime and output; redact secrets in audit/logs.
- Never use this flow to turn VPS allowlist into arbitrary shell.

## Review Focus

1. Confirmation arrives after target or command changed — reject the mismatch.
2. Two identical confirmation callbacks race — exactly one consumes approval.
3. Approval is replayed after restart/expiry — reject it.
4. A non-owner opened the command flow — route for owner approval or deny; never self-approve.
5. MQTT message lacks trusted signature/ACL origin — agent must not invoke subprocess.

---

**Дата:** 2026-10-08  
**Статус:** локальная реализация Tasks 1–4 принята; macOS frozen artifact и отдельно разрешаемый live smoke остаются открытыми гейтами
**Спецификация:** раздел 10 и security-требования раздела 14 [TARPED redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**Зависимость:** план бота/RBAC; до его приёмки не подключать новый маршрут к кнопке.

## Цель

Убрать нынешний переход «ввёл текст — он сразу ушёл на устройство». До запуска владелец видит точную команду, target, запросившего, время и ограничения, затем явно подтверждает или отменяет. Ни confirmation UI, ни неподтверждённое сообщение не должны сами по себе считаться основанием для исполнения.

## Доверенная граница

Текущий HMAC использует общий `SHARED_KEY`, присутствующий в боте и агентах; он защищает целостность от постороннего ключа, но не доказывает, что отправитель — именно сервер. Поэтому shell approval получает отдельный серверный Ed25519-контракт; не переиспользовать ни `SHARED_KEY`, ни GitHub Release private key. Сервер хранит private key в `/etc/xider/command-signing-private.pem` с правами только для сервисного пользователя; агенты получают только public key через проверенный bootstrap/config flow. Существующие HMAC/шифрование/MQTT ACL остаются внешним слоем транспорта, но не заменяют эту проверку.

## Этапы TDD

### Implementation tasks

#### Task 1: Atomic pending-command model

**Files:** Create `TG-BOT-SERVER/command_approval.py` and `TG-BOT-SERVER/tests/test_command_approval.py`.

**Interface:** `PendingCommand(request_id: str, requester_id: int, owner_id: int, device_id: str, command_text: str, command_hash: str, created_at: int, expires_at: int, nonce: str)`. The object is immutable; `command_text` is temporary process-memory state, omitted from `repr`, public metadata, serialization, and audit. `create_pending(requester_id: int, owner_id: int, device_id: str, command: str, now: int) -> PendingCommand` stores the exact UTF-8 text and computes its hash. `consume_pending(request_id: str, approver_id: int, device_id: str, command_hash: str, now: int) -> PendingCommand | None` atomically removes/returns it once and checks the owner captured at creation. `cancel_pending(request_id: str, actor_id: int) -> bool` allows only the requester or captured owner. Binding `owner_id` is required so the model can distinguish owner cancellation from an arbitrary actor. Restart discards all pending requests, so old callback IDs fail closed.

- [x] Add tests for valid consume, unchanged command/hash, altered hash/device, expiry at 120 seconds, replay, cancel, restart invalidation, command absence from serialization/audit and concurrent double-consume.
- [x] Run `python -m pytest tests/test_command_approval.py -q` from `TG-BOT-SERVER`; the baseline failed as expected before the model existed.
- [x] Implement bounded state and cryptographic nonce; compute hash from exact UTF-8 command bytes.
- [x] Rerun; at most one consume succeeds and invalid state transitions are rejected.
- [x] Commit the model and tests (`9e6e632`), then add full-field immutability regression coverage (`6b1404d`, `ecda9f2`).

Task 1 review accepted: fresh review of the model found no Critical/Important issue; fresh review of `ecda9f2` confirmed all nine stored slots plus the public `command_text` property are covered by reassignment tests. Verification: focused 25 passed; full bot suite 471 passed; X-LEX golden suite 34 passed. No live Telegram/MQTT/VPS action was performed.

#### Task 2: Server-only signing and agent enforcement

**Files:** Create `TG-BOT-SERVER/command_signing.py` and `TG-BOT-SERVER/tests/test_command_signing.py`; create the canonical shared `XGENT-MCS/command_signing.py` and `tests/test_shell_approval.py` in each agent tree. Windows already imports shared release helpers from `XGENT-MCS` (source path and PyInstaller `pathex`), so do not maintain a second implementation under `XGENT-WDS`. Create `deploy/provision_command_signing_key.py` and `deploy/tests/test_command_signing_key_install.sh`. Modify bot `config.py`/`transport.py`, both agent `config.py` and message dispatchers, `deploy/server-install.sh`, `deploy/bootstrap-agent.ps1`, and `deploy/bootstrap.sh`. Carry the helper through the existing source-release allowlists and recovery paths: `XGENT-WDS/xgent_wds.py`, `XGENT-MCS/update_package.py` (including `_WINDOWS_SHARED_UPDATE_FILES` and `AGENT_UPDATE_FILES`), `XGENT-MCS/xgent_mcs.py`, `XGENT-MCS/guardian_recovery.py`; update their inventory tests in `XGENT-WDS/tests/test_agent_update.py`, `XGENT-MCS/tests/test_agent_update.py`, `XGENT-MCS/tests/test_update_package.py`, and `XGENT-MCS/tests/test_guardian_repair.py`. Update PyInstaller inclusion/assertions in `XGENT-WDS/XGENT-WDS.spec`, `XGENT-MCS/XGENT-MCS.spec`, and `.github/workflows/build-agents.yml`. Extend `deploy/tests/test_bootstrap_agent_preflight.ps1` and `deploy/tests/test_bootstrap_macos_transaction.sh`.

**Interface:** `sign_approved_command(*, request_id: str, requester_id: int, approver_id: int, device_id: str, command: str, issued_at: int, expires_at: int, nonce: str, key_id: str) -> dict` signs canonical schema-1 fields `{key_id, request_id, requester_id, approver_id, device_id, command_hash, issued_at, expires_at, nonce}` with the server-only Ed25519 private key; command text is carried separately and is not part of the signature envelope. `verify_approved_command(envelope: dict, *, command: str, expected_device_id: str, now: int) -> dict | None` recomputes SHA-256 over the exact command UTF-8 bytes and verifies the signature, key id, target, nonce and `0 < expires_at - issued_at <= 120`. `key_id` is the first 16 lowercase hex characters of SHA-256 over the raw Ed25519 public key; `XIDER_COMMAND_VERIFY_KEYSET_B64` is base64url-without-padding of canonical UTF-8 JSON mapping key ids to base64url public keys. `deploy/provision_command_signing_key.py` creates the key only when absent, preserves it through ordinary installs, and atomically updates the public keyset in bot.env. The private key path is `/etc/xider/command-signing-private.pem`, owned `root:xider` mode `0640` to match `deploy/xider-bot.service` (`User=xider`); it never enters agent `.env`, installers, logs, GitHub, or release artifacts. Agents atomically persist consumed request IDs at `%LOCALAPPDATA%\XIDER\command-replay.json` (Windows) and `~/Library/Application Support/XIDER/command-replay.json` (macOS), retaining each until expiry so replay is rejected after process restart. Missing/unknown public key disables only arbitrary shell and fails closed; no legacy unsigned-shell fallback.

- [x] Add signer tests for canonical encoding, tampered command, target, expiry, unknown key id, key rotation overlap and missing private key.
- [x] Add both-agent tests for HMAC-only/unsigned payload, invalid signature, wrong device, command/hash mismatch, not-yet-valid/expired approval, replay, replay after agent restart, missing public key, and a valid approval.
- [x] Add bootstrap/deploy tests proving private-key file permissions, durable key preservation across update/rollback, public-key-only delivery to agents, and no private key in package/log/export.
- [x] Add package-inventory tests and CI assertions for the trusted modules in source/update/recovery inventories and frozen archives. Windows resolves the canonical helper in source and frozen modes; Windows frozen executable was inspected and smoke-tested. macOS source/CI archive assertions exist; its built artifact acceptance is tracked separately below.
- [x] Update both PyInstaller specs/build assertions to include `command_signing` and the Ed25519 verifier; update source/update/recovery lists and their exact expected fixtures together. Keep `XIDER_COMMAND_VERIFY_KEYSET_B64` public-only in agent configuration; never bundle the server signing key in a Windows/macOS binary or Guard Keeper recovery package.
- [x] Run `bash deploy/tests/test_command_signing_key_install.sh` in a Linux/WSL-compatible temporary fixture; first install, idempotence, permissions and public-only material are covered.
- [x] Run `python -m pytest tests/test_command_signing.py -q` in `TG-BOT-SERVER`, then approval-gate tests in both agents; invalid envelopes fail before subprocess and valid approvals are accepted once.
- [x] Implement `deploy/provision_command_signing_key.py` using the existing `cryptography` dependency; key creation is absent-only, permissions are enforced, bot.env update is atomic, and unsafe/mismatched state fails closed.
- [x] Keep key rotation as a separate owner-approved migration with `key_id` and bounded old-public-key overlap; ordinary bot updates do not replace the key.
- [x] Add the device-side atomic replay ledger at the exact platform app-data path; reserve the request id before starting the subprocess and fail closed on corrupt/unwritable ledger.
- [x] Connect bootstrap so only `XIDER_COMMAND_VERIFY_KEYSET_B64` is supplied to newly installed/updated agents; reject shell from older agents until they report `command_approval_protocol >= 1`.
- [x] Include `command_approval_protocol: 1` in both agents' signed capability/status responses and test that the bot disables shell-request/approval for a missing or older protocol version.
- [x] Add/run a test at the actual MQTT runtime dispatch seam proving an HMAC-authenticated unsigned or invalidly signed shell payload is rejected before subprocess. `ops/tests/test_mqtt_shell_callback.py` drives both agents' real `_on_message` callbacks; the existing `ops/tests/test_mqtt_runtime_security.py` remains the separate TLS/ACL config test.
- [ ] Run/inspect macOS frozen `.app` archive and verify updater/recovery packaging there; Windows frozen artifact smoke passed, macOS CI artifact is not available locally.
- [x] Commit the server signer, device verification, key provisioning and tests as one reviewed protocol migration (implemented as reviewed 2A–2D slices/commits).

Task 2 local evidence audit (2026-10-11): code and slice reports show implementation/fixture acceptance for signer, both agent gates, replay ledgers, provisioning/bootstrap and Windows frozen bundle. The actual MQTT callback regression now passes for WDS and MCS; see `.superpowers/sdd/2026-10-08-tarped-plan-release-server/task-2-runtime-evidence-report.md`. Remaining gates are specifically (a) macOS frozen artifact build/smoke and (b) separately authorized live Telegram→MQTT→physical-device smoke; neither is claimed complete.

#### Task 3: Bot preview and owner approval

**Files:** Modify `TG-BOT-SERVER/bot.py:on_shell_input` and `TG-BOT-SERVER/transport.py` integration; add `cmd:shell-approve:<request_id>` and `cmd:shell-cancel:<request_id>` callbacks; test `TG-BOT-SERVER/tests/test_bot_ui.py`.

**Interface:** the owner may create a request directly; a user may create one only with an explicit `shell_request` grant for the selected device. Both create a `PendingCommand` preview routed to the owner. The user grant never approves execution. Approval consumes the pending object once, creates the Task 2 signed envelope with the approving owner's id, and is the only call path that may publish it.

- [x] Add a failing test that an owner input and a granted-user input each produce an exact owner preview and call no publish function.
- [x] Run `python -m pytest tests/test_bot_ui.py -q` from `TG-BOT-SERVER`; expected: direct publish behavior fails the assertion.
- [x] Implement the preview card and `cmd:shell-approve:<request_id>` / `cmd:shell-cancel:<request_id>` callbacks; re-fetch the object from the server-side pending store, never trust target/hash/requester from callback text.
- [x] Test that an ungranted user, guest or blocked user cannot create a request; a granted user cannot approve it; only the current owner may approve or cancel; and a user grant is rechecked at approval time.
- [x] Test changed target, altered command/hash, expiry, stale owner/requester role, revoked grant, duplicate callback and simultaneous approvals; each invalid path produces zero publish.
- [x] After Task 2, call `sign_approved_command(...)` only after atomic `consume_pending(...)`; publish one request to one device and surface signer/config failure without fallback to legacy HMAC-only shell.
- [x] Run `python -m pytest tests/test_bot_ui.py tests/test_access_store.py tests/test_command_approval.py tests/test_command_signing.py -q`; expected: exactly one signed publish for an explicit current-owner confirmation.
- [x] Commit bot preview, approval and signed transport integration. Accepted locally 2026-10-09 after independent review plus one scoped fix/re-review round; see `.superpowers/sdd/2026-10-08-tarped-plan-command-approval/task-3-report.md`.

#### Task 4: Limits, audit and redaction

**Files:** Modify bot and agent shell-result handlers and `TG-BOT-SERVER/access_store.py` only where tests require it; add redaction tests per component.

**Interface:** default execution timeout 20 seconds unless current stricter cap applies; each stdout/stderr stream is capped at 8 KiB before transport; audit stores request id/hash/status/requester/approver/target/time/exit code, not command text or raw output.

- [x] Add tests for timeout, oversized output and token/password/private-key redaction.
- [x] Run targeted shell approval tests; expected: unbounded output test fails.
- [x] Implement process timeout and output truncation; redact before Telegram and audit writes.
- [x] Rerun all approval suites; expected: no secret fixtures remain in responses or audit rows.
- [x] Commit bounded execution and audit changes.

Task 4 accepted locally 2026-10-09 after three scoped fix rounds; final review clean at `ea97cbe`. Aggregate, X-LEX golden and Windows frozen-bundle verification passed. Live Telegram/MQTT/device acceptance was not performed; WDS source-updater/canonical-helper path is tracked for release/server integration.

## Итоговая приёмка

- Подмена command/device/hash, TTL expiry, replay и два одновременных approval отклоняются.
- Только текущий владелец может исполнить запрос; blocked и stale user отклоняются.
- Отсутствует прямой путь исполнения сырого ввода из FSM.
- Агент не запускает команду до проверки approval, server-only Ed25519 signature, device/hash/TTL и durable replay ledger.
- Live Telegram/MQTT-тест и реальный shell command test выполнять только отдельным явным smoke-сценарием владельца.

## Коммиты

1. Approval model and tests.
2. Server-only signer, agent verification, replay ledger and key provisioning.
3. Bot request-only grant, preview/cancel/owner-approval FSM and signed publish integration.
4. Sanitized audit and limits.
