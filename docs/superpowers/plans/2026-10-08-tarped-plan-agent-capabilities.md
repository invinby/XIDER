# План: проверки разрешений и функции Windows/macOS

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` after owner approval; implement each task with the listed red/green test cycle.

**Goal:** Возвращать достоверный статус возможностей Windows/macOS и корректно диагностировать разрешения, местоположение, скриншоты и открытие сайта.

**Architecture:** Агент возвращает версионированный capability snapshot с состоянием, временем и источником; бот отображает это без догадок. Системные разрешения выдаёт владелец на устройстве; фоновой агент не имитирует видимый consent flow.

**Tech Stack:** Python, текущие aiogram/MQTT payloads, pytest, macOS Core Location/TCC, Windows `Windows.Devices.Geolocation`/Settings URI.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`, разделы 7–9, 11, 14.

## Global Constraints

- Capability states: `granted`, `denied`, `not_determined`, `unavailable`, `needs_live_test`, `unknown`.
- Geolocation includes source and uncertainty; IP result is approximate city/region, never GPS/coordinates.
- No agent may grant its own TCC/UAC/location permission or auto-consent.
- Existing open-URL quantity choices stay `1/2/3/5`; allow only `http` and `https`.
- Missing physical Mac/Windows means that OS remains unaccepted.
- Guardian/companions stay documented and visible as local services/login items; owner retains local stop and uninstall, and nothing silently restores itself after owner removal.

## Review Focus

1. A background agent tries to trigger a first-time OS consent prompt — show settings/help or an owner-visible local path; do not claim prompt was accepted.
2. Geolocation permission is granted but OS location service/accuracy is unavailable — report the distinct state and don't silently switch source.
3. Screenshot command returns zero but produces an empty file — classify as failed capture, not a valid image.
4. URL callback is replayed after device grant is revoked — reject it without opening any window.
5. An older agent omits the new schema fields — parse as unknown/needs-live-test, not granted.

---

**Дата:** 2026-10-08  
**Статус:** локальная реализация Tasks 1–4 завершена и проверена; physical-device/signing acceptance отдельно открыта
**Спецификация:** разделы 7–9, 11 и 14 [TARPED redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**Границы:** `TG-BOT-SERVER`, `XGENT-WDS`, `XGENT-MCS`; никаких скрытых разрешений или действий.

## Цель и общий контракт

Добавить точные состояния возможностей вместо «работает/не работает». Ответ агента должен включать версию схемы, ОС, capability id, state (`granted`, `denied`, `not_determined`, `unavailable`, `needs_live_test`, `unknown`), checked_at, безопасное объяснение и поддерживаемую ссылку на системные настройки. Старый агент, не знающий нового поля, не должен ломать старый бот.

## Этапы TDD

### Implementation tasks

#### Task 1: Versioned capability snapshot

**Files:** Modify `XGENT-MCS/xgent_mcs.py`, `XGENT-WDS/xgent_wds.py` and `TG-BOT-SERVER/bot.py`; create platform tests `tests/test_capabilities.py` and bot parser tests.

**Interface:** snapshot JSON is `{schema: 1, platform, checked_at, items: [{id, state, detail, settings_uri}]}`. Inventory at minimum screen recording, camera, microphone, location, accessibility/UI control and file access; annotate capabilities unsupported on an OS rather than omitting them. Each `state` is one of the six values listed above. Bot `_format_capabilities(result: dict) -> str` remains a pure formatter; unknown schema/status fails closed.

- [ ] Add tests for each state, unknown schema, unknown id, missing item, and a snapshot from an older agent.
- [ ] Add bot UI tests for recheck, precise permission instructions, state timestamp, and the supported settings route; accept only platform-specific settings targets, never an arbitrary URI supplied by an agent.
- [ ] Test the limited-diagnostic case explicitly: if permission cannot be queried directly, render `needs_live_test` and explain that a permitted trial action is the only confirmation.
- [ ] Run each platform `python -m pytest tests/test_capabilities.py -q`; expected: old result format is rejected or marked unknown, never granted.
- [ ] Implement the versioned snapshot and parser without changing MQTT command names used by older agents.
- [ ] Run capability tests in both agents and bot UI tests; expected: all states render honestly and unknown data cannot enable an action.
- [ ] Commit protocol and tests.

#### Task 2: System location and explicit IP fallback

**Files:** Modify `_do_geo_location` in `XGENT-WDS/xgent_wds.py` and `XGENT-MCS/xgent_mcs.py`; update `XGENT-MCS/XGENT-MCS.spec`, `XGENT-MCS/requirements.txt`, `.github/workflows/build-agents.yml`, and macOS LaunchAgent/app startup only after adapter integration tests; add `tests/test_location.py` per agent, `tools/verify_macos_bundle.py`, and `tools/test_verify_macos_bundle.py`.

**Interface:** pure formatter `format_location_result(result: dict) -> str` in the bot; agent response fields are `source`, `state`, `latitude`, `longitude`, `accuracy_m`, `label`, `checked_at`.

- [ ] Add failing tests for permission denied, global service disabled, timeout, unavailable sensor, valid system coordinate, and IP estimate with all coordinate fields null.
- [ ] Run location tests in both agent folders; expected: current IP-only response is classified `ip_estimate`, not system GPS.
- [ ] Implement platform adapter using Core Location on macOS and Windows Geolocator; request one current position only, no background tracking.
- [ ] Package macOS agent as a `.app` with the existing stable `CFBundleIdentifier=com.xider.agent` from `make_app.sh` and a non-empty `NSLocationUsageDescription` that truthfully explains a one-shot location check only after an explicit XIDER request; initialize `CLLocationManager` on a thread with an active Cocoa run loop and marshal MQTT-worker requests onto that loop. Keep the same signed app identity for the background worker; a shell/venv process outside the bundle is not the location-enabled runtime.
- [ ] Remove the ad-hoc-signing path for any artifact claimed to support Core Location. Use a proper non-ad-hoc Apple Development identity for local device acceptance; use Developer ID signing plus notarization for externally distributed release artifacts. These are distinct gates: Core Location's PyObjC binding requires proper non-ad-hoc signing, while Developer ID/notarization are the external-distribution path.
- [ ] Add `tools/verify_macos_bundle.py` and `tools/test_verify_macos_bundle.py`; test missing/incorrect `CFBundleIdentifier`, missing `NSLocationUsageDescription`, invalid `codesign --verify --deep --strict`, and ad-hoc identity. Run `python -m unittest discover -s tools -p "test_verify_macos_bundle.py" -v`. On a macOS runner, run the verifier against the built `.app` before packaging and against the extracted final ZIP artifact; inspect `codesign -dv --verbose=4` and `Info.plist`, and reject ad-hoc signatures. Do not modify app contents after signing; notarize/staple before final artifact verification.
- [ ] Update the macOS workflow to produce and verify the `.app`; keep signing certificates/private keys out of source and logs. If the required identity or notarization credentials are not configured, the workflow must not publish or call the artifact release-ready, and Core Location acceptance remains blocked rather than silently falling back to an ad-hoc build.
- [ ] Ensure first-run permission is requested only by a visible local UI flow. Background agents cannot pretend to present consent.
- [ ] Use Windows `RequestAccessAsync` only from a foreground UI thread. Otherwise open `ms-settings:privacy-location` and return `needs_live_test` until the owner grants it.
- [ ] Keep IP fallback opt-in and approximate; run the tests and inspect that audit logs contain no coordinates. Device acceptance requires the exact signed `.app` from the final ZIP, a clean macOS user account, the real consent prompt, and a successful authorized one-shot location read; source-mode or ad-hoc tests do not satisfy it.
- [ ] Commit location adapters and tests.

#### Task 3: Screenshot diagnostics

**Files:** Modify `_do_screenshot` in both agent files and bot screenshot result handling; create `tests/test_screenshot.py` per agent.

**Interface:** every result has `state`, `detail`, and optional image result; `state` distinguishes permission/session/command/file/transport failures.

- [ ] Add mocked tests for denied TCC, no GUI session, missing tool, non-zero exit, empty output, oversized file, timeout and successful capture.
- [ ] Run platform tests; expected: current generic Mac error test fails until individual states are returned.
- [ ] Implement the smallest distinguishable diagnostics; always unlink temporary files in `finally`.
- [ ] Add bot tests that map each state to a precise instruction without claiming permission is granted.
- [ ] Include the macOS Screen Recording settings path in the denied-state help, with wording that the exact label may vary by macOS version; verify the active LaunchAgent GUI session after lock, sleep/wake and user switching during physical Mac acceptance.
- [ ] Rerun agent and bot tests; expected: every failure has a distinct safe status and success requires a valid image.
- [ ] Commit screenshot diagnostics and tests.

#### Task 4: Existing open-site flow

**Files:** Modify `_validated_open_url`/`_do_open_url` in `XGENT-WDS/xgent_wds.py` and `XGENT-MCS/xgent_mcs.py` only where failing tests identify a gap; modify existing URL FSM handlers in `TG-BOT-SERVER/bot.py`; create `XGENT-WDS/tests/test_open_url.py` and `XGENT-MCS/tests/test_open_url.py`; extend `TG-BOT-SERVER/tests/test_bot_ui.py`.

**Interface:** retain URL limits and amount choices `1/2/3/5`; return an operation id and actual launched count when available.

- [ ] Add tests `test_accepts_http_and_https`, `test_rejects_file_javascript_data_and_unknown_schemes`, `test_rejects_invalid_window_count`, `test_revalidates_revoked_grant`, and `test_duplicate_operation_id_launches_once` across the agent and bot layers.
- [ ] Run `python -m pytest tests/test_open_url.py -q` in both agent directories and `python -m pytest tests/test_bot_ui.py -q` in `TG-BOT-SERVER`; expected: any invalid scheme or revoked grant produces zero publish/launch.
- [ ] Reuse the existing button/handler flow and add idempotency only if compatible with current MQTT request ids.
- [ ] Report actual result separately from MQTT publish acceptance.
- [ ] Rerun tests and commit URL-flow changes.

#### Task 5: Close screenshot runtime and Telegram-delivery gaps

**Files:** Modify `XGENT-MCS/xgent_mcs.py`, `XGENT-MCS/tests/test_screenshot.py`, `TG-BOT-SERVER/bot.py`, and `TG-BOT-SERVER/tests/test_screenshot.py` or `tests/test_bot_ui.py`.

**Evidence:** The capability probe accepts `/usr/sbin/screencapture` when it is not in `PATH`, but the capture handler only checks and executes the bare command name. Separately, `_replace_callback_with_media_unlocked` catches Telegram sender exceptions and returns the previous card, so the screenshot handler's `transport_failed` fallback is unreachable on actual photo-send errors. Screenshot captions also interpolate the device label into HTML without escaping.

- [x] Add a macOS-agent regression for a missing `PATH` entry with the absolute system binary available; assert capability detection and screenshot execution resolve the same executable.
- [x] Add a bot regression that runs the real media-card helper with a sender that fails; preserve the existing card and render the screenshot delivery diagnostic in that card.
- [x] Escape the device label in screenshot HTML caption and test a label containing `<`, `&`, and quotes.
- [x] Implement the smallest changes; keep other media-card callers' current recovery behavior unchanged.
- [x] Run focused MCS screenshot and bot screenshot/UI tests, then the full component test runner. Keep physical Mac and live Telegram acceptance explicitly unverified unless actually performed.
- [x] Commit the focused follow-up and obtain independent task review.

Task 5 verification (2026-10-12): mocked MCS screenshot **19 passed**, bot screenshot **26 passed**, final bot UI **319 passed**. Full `py -3.12 tools/run_tests.py` exited **0**: six isolated suites and Windows deployment fixtures passed. That full-run bot collection was **853 passed / 1 skipped** before the additional default-media-recovery test; the final 319-test UI run includes it. Physical Mac capture/TCC and live Telegram delivery remain **unverified**. Independent review is approved.

Task 5 independent review (2026-10-12): **approved** with no Critical, Important, or Minor findings. Reviewer confirmed screenshot-only sender-error propagation, default behavior for other media callers, HTML escaping, and guard/card-lock behavior. Physical Mac capture/TCC, live Telegram delivery, and deployment remain unverified.

## Общие команды

```powershell
python -m pytest tests -q
```

Выполнить отдельно из каждого каталога `TG-BOT-SERVER`, `XGENT-WDS`, `XGENT-MCS`. До изменений сохранить результат базового прогона; после — ни одного пропущенного из-за случайного отсутствия зависимости теста без пояснения.

## Коммиты и приёмка

1. Capability protocol + tests.
2. Platform permission adapters and location tests.
3. Screenshot diagnosis and temporary-file cleanup.
4. Existing URL flow and idempotency.
5. Bot display and settings instructions.

В конце провести полный компонентный набор; реально проверить Mac и Windows отдельно. Если физического устройства нет — оставить статус платформы как unaccepted, не заявлять GPS/screenshots готовыми.
