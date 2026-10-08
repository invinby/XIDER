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
**Статус:** ждёт одобрения реализации  
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

**Interface:** `PendingCommand(request_id: str, requester_id: int, device_id: str, command_text: str, command_hash: str, created_at: int, expires_at: int, nonce: str)`. `command_text` is temporary process-memory state with `repr=False`, displayed verbatim only in the owner’s approval card, never serialized or written to audit. `create_pending(requester_id: int, device_id: str, command: str, now: int) -> PendingCommand` stores the exact UTF-8 text and computes its hash; `consume_pending(request_id: str, approver_id: int, device_id: str, command_hash: str, now: int) -> PendingCommand | None` atomically removes/returns it once; `cancel_pending(request_id: str, actor_id: int) -> bool`. Restart discards all pending requests, so old callback IDs fail closed.

- [ ] Add tests for valid consume, unchanged command/hash, altered hash/device, expiry at 120 seconds, replay, cancel, restart invalidation, command absence from serialization/audit and concurrent double-consume.
- [ ] Run `python -m pytest tests/test_command_approval.py -q` from `TG-BOT-SERVER`; expected: import/behavior failures.
- [ ] Implement bounded state and cryptographic nonce; compute hash from exact UTF-8 command bytes.
- [ ] Rerun; expected: one successful consume at most, all invalid state transitions rejected.
- [ ] Commit the model and tests.

#### Task 2: Server-only signing and agent enforcement

**Files:** Create `TG-BOT-SERVER/command_signing.py` and `TG-BOT-SERVER/tests/test_command_signing.py`; create the canonical shared `XGENT-MCS/command_signing.py` and `tests/test_shell_approval.py` in each agent tree. Windows already imports shared release helpers from `XGENT-MCS` (source path and PyInstaller `pathex`), so do not maintain a second implementation under `XGENT-WDS`. Create `deploy/provision_command_signing_key.py` and `deploy/tests/test_command_signing_key_install.sh`. Modify bot `config.py`/`transport.py`, both agent `config.py` and message dispatchers, `deploy/server-install.sh`, `deploy/bootstrap-agent.ps1`, and `deploy/bootstrap.sh`. Carry the helper through the existing source-release allowlists and recovery paths: `XGENT-WDS/xgent_wds.py`, `XGENT-MCS/update_package.py` (including `_WINDOWS_SHARED_UPDATE_FILES` and `AGENT_UPDATE_FILES`), `XGENT-MCS/xgent_mcs.py`, `XGENT-MCS/guardian_recovery.py`; update their inventory tests in `XGENT-WDS/tests/test_agent_update.py`, `XGENT-MCS/tests/test_agent_update.py`, `XGENT-MCS/tests/test_update_package.py`, and `XGENT-MCS/tests/test_guardian_repair.py`. Update PyInstaller inclusion/assertions in `XGENT-WDS/XGENT-WDS.spec`, `XGENT-MCS/XGENT-MCS.spec`, and `.github/workflows/build-agents.yml`. Extend `deploy/tests/test_bootstrap_agent_preflight.ps1` and `deploy/tests/test_bootstrap_macos_transaction.sh`.

**Interface:** `sign_approved_command(*, request_id: str, requester_id: int, approver_id: int, device_id: str, command: str, issued_at: int, expires_at: int, nonce: str, key_id: str) -> dict` signs canonical schema-1 fields `{key_id, request_id, requester_id, approver_id, device_id, command_hash, issued_at, expires_at, nonce}` with the server-only Ed25519 private key; command text is carried separately and is not part of the signature envelope. `verify_approved_command(envelope: dict, *, command: str, expected_device_id: str, now: int) -> dict | None` recomputes SHA-256 over the exact command UTF-8 bytes and verifies the signature, key id, target, nonce and `0 < expires_at - issued_at <= 120`. `key_id` is the first 16 lowercase hex characters of SHA-256 over the raw Ed25519 public key; `XIDER_COMMAND_VERIFY_KEYSET_B64` is base64url-without-padding of canonical UTF-8 JSON mapping key ids to base64url public keys. `deploy/provision_command_signing_key.py` creates the key only when absent, preserves it through ordinary installs, and atomically updates the public keyset in bot.env. The private key path is `/etc/xider/command-signing-private.pem`, owned `root:xider` mode `0640` to match `deploy/xider-bot.service` (`User=xider`); it never enters agent `.env`, installers, logs, GitHub, or release artifacts. Agents atomically persist consumed request IDs at `%LOCALAPPDATA%\XIDER\command-replay.json` (Windows) and `~/Library/Application Support/XIDER/command-replay.json` (macOS), retaining each until expiry so replay is rejected after process restart. Missing/unknown public key disables only arbitrary shell and fails closed; no legacy unsigned-shell fallback.

- [ ] Add signer tests for canonical encoding, tampered command, target, expiry, unknown key id, key rotation overlap and missing private key.
- [ ] Add both-agent tests for HMAC-only/unsigned payload, invalid signature, wrong device, command/hash mismatch, not-yet-valid/expired approval, replay, replay after agent restart, missing public key, and a valid approval.
- [ ] Add bootstrap/deploy tests proving private-key file permissions, durable key preservation across update/rollback, public-key-only delivery to agents, and no private key in package/log/export.
- [ ] Add package-inventory tests proving `command_signing.py` is present in the Windows shared-update allowlist and staged by the Windows updater, the macOS source-update/transaction/recovery lists, and both built PyInstaller archives. Windows must resolve the one shared helper from `XGENT-MCS` in source mode and from its bundled module in frozen mode.
- [ ] Update both PyInstaller specs/build assertions to include `command_signing` and the Ed25519 verifier; update source/update/recovery lists and their exact expected fixtures together. Keep `XIDER_COMMAND_VERIFY_KEYSET_B64` public-only in agent configuration; never bundle the server signing key in a Windows/macOS binary or Guard Keeper recovery package.
- [ ] Run `bash deploy/tests/test_command_signing_key_install.sh` in Linux CI or WSL; expected: first install creates one private/public pair, repeat install preserves its key id, and update/rollback never exports the private key.
- [ ] Run `python -m pytest tests/test_command_signing.py -q` in `TG-BOT-SERVER`, then `python -m pytest tests/test_shell_approval.py -q` in each agent; expected: all invalid envelopes fail before `_do_shell` and the valid envelope reaches it exactly once.
- [ ] Implement `deploy/provision_command_signing_key.py` using the existing `cryptography` dependency; create the private key only when absent, set `root:xider`/`0640`, atomically update bot.env with the derived public keyset, and fail closed on malformed or mismatched existing material.
- [ ] Keep key rotation as a separate owner-approved migration with `key_id` and bounded old-public-key overlap; never rotate or replace the key during ordinary bot updates.
- [ ] Add the device-side atomic replay ledger at the exact platform app-data path; reserve the request id before starting the subprocess and fail closed on corrupt/unwritable ledger.
- [ ] Connect bootstrap so only `XIDER_COMMAND_VERIFY_KEYSET_B64` is supplied to newly installed/updated agents; reject shell from older agents until they report `command_approval_protocol >= 1`.
- [ ] Include `command_approval_protocol: 1` in both agents' signed capability/status responses and test that the bot disables shell-request/approval for a missing or older protocol version.
- [ ] Run both agent suites, signer/approval tests, and `ops/tests/test_mqtt_runtime_security.py`; expected: a shared-HMAC-authenticated MQTT publish without the separate server signature never reaches subprocess.
- [ ] Run updater/recovery/package tests and inspect both generated PyInstaller archives; expected: the verifier survives build, update, rollback, and macOS Guard Keeper file restoration while the private key appears in none of those artifacts.
- [ ] Commit the server signer, device verification, key provisioning and tests as one reviewed protocol migration.

#### Task 3: Bot preview and owner approval

**Files:** Modify `TG-BOT-SERVER/bot.py:on_shell_input` and `TG-BOT-SERVER/transport.py` integration; add `cmd:shell-approve:<request_id>` and `cmd:shell-cancel:<request_id>` callbacks; test `TG-BOT-SERVER/tests/test_bot_ui.py`.

**Interface:** the owner may create a request directly; a user may create one only with an explicit `shell_request` grant for the selected device. Both create a `PendingCommand` preview routed to the owner. The user grant never approves execution. Approval consumes the pending object once, creates the Task 2 signed envelope with the approving owner's id, and is the only call path that may publish it.

- [ ] Add a failing test that an owner input and a granted-user input each produce an exact owner preview and call no publish function.
- [ ] Run `python -m pytest tests/test_bot_ui.py -q` from `TG-BOT-SERVER`; expected: direct publish behavior fails the assertion.
- [ ] Implement the preview card and `cmd:shell-approve:<request_id>` / `cmd:shell-cancel:<request_id>` callbacks; re-fetch the object from the server-side pending store, never trust target/hash/requester from callback text.
- [ ] Test that an ungranted user, guest or blocked user cannot create a request; a granted user cannot approve it; only the current owner may approve or cancel; and a user grant is rechecked at approval time.
- [ ] Test changed target, altered command/hash, expiry, stale owner/requester role, revoked grant, duplicate callback and simultaneous approvals; each invalid path produces zero publish.
- [ ] After Task 2, call `sign_approved_command(...)` only after atomic `consume_pending(...)`; publish one request to one device and surface signer/config failure without fallback to legacy HMAC-only shell.
- [ ] Run `python -m pytest tests/test_bot_ui.py tests/test_access_store.py tests/test_command_approval.py tests/test_command_signing.py -q`; expected: exactly one signed publish for an explicit current-owner confirmation.
- [ ] Commit bot preview, approval and signed transport integration.

#### Task 4: Limits, audit and redaction

**Files:** Modify bot and agent shell-result handlers and `TG-BOT-SERVER/access_store.py` only where tests require it; add redaction tests per component.

**Interface:** default execution timeout 20 seconds unless current stricter cap applies; each stdout/stderr stream is capped at 8 KiB before transport; audit stores request id/hash/status/requester/approver/target/time/exit code, not command text or raw output.

- [ ] Add tests for timeout, oversized output and token/password/private-key redaction.
- [ ] Run targeted shell approval tests; expected: unbounded output test fails.
- [ ] Implement process timeout and output truncation; redact before Telegram and audit writes.
- [ ] Rerun all approval suites; expected: no secret fixtures remain in responses or audit rows.
- [ ] Commit bounded execution and audit changes.

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
