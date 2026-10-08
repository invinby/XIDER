# Telegram Bot Navigation and RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` after owner approval. Steps use checkbox syntax; finish each task's failing-test/pass-test/review cycle before moving on.

**Goal:** Доказуемо согласовать Telegram-кнопки, callbacks, FSM-продолжения и владельческие grants, сохранив один редактируемый экран.

**Architecture:** Сначала построить callback coverage matrix, затем применять существующий `access_store.can_use_callback(...)` на отображении и выполнении, не заменяя handler filters. Админские потоки используют существующие storage API и `ui_cards.py`.

**Tech Stack:** Python, aiogram 3, pytest, JSON access store, Telegram edit-message API.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`, разделы 4–7, 12, 14.

## Global Constraints

- Keep roles owner/user/guest/blocked; owner stays bound to protected `ADMIN_ID`.
- Keep existing device and action grants; re-check device-scoped actions against the selected device.
- A user-side shell grant is request-only: it may create a pending approval for the owner, never publish or execute by itself; only the current owner can approve.
- Guests may view public Info, non-sensitive device summaries and the read-only server overview; no commands, precise location, images, files, raw shell output or detailed logs by default.
- One editable card is the normal UI; enumerated security alerts may be separate.
- Do not change X-LEX data, authored texts, style selection, runtime behavior, or golden tests.

## Review Focus

1. Direct callback invocation bypasses the keyboard — handler still denies unauthorized user.
2. Grant is revoked while an FSM form is open — continuation is cleared and no command is published.
3. User has a grant on one device but changes target — request to the other device is denied.
4. Telegram edit fails or callback repeats — no repeated menu-message flood.
5. Guest opens server/info — no sensitive location, screenshot, files, shell output, secrets, or detailed logs.

---

**Дата:** 2026-10-08  
**Статус:** ждёт одобрения реализации  
**Спецификация:** разделы 4–7, 12 и 14 [TARPED redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**Границы:** `TG-BOT-SERVER`; не менять X-LEX (`xlex.py`, его данные, стили и golden-тесты).

## Цель

Сделать один устойчивый интерфейс-карточку, в котором видимые кнопки, обработчики, роли, устройства и grants согласованы. Сохранить текущую модель owner/user/guest/blocked и безопасный обзор сервера для чтения; реальные чувствительные данные гостю не открывать.

## Зафиксированный каркас экранов

- Главная: `Устройства`, `Сервер`, `Админ`, `Инфо`; `Админ` виден только владельцу.
- Устройство: `Обзор и диагностика`, `Экран и мультимедиа`, `Файлы`, `Сеть и приложения`, `Команды и процессы`, `Питание`, `Версии и обновление`, `Разрешения`, `Дополнительные функции`.
- Порядок категорий одинаков для Windows/macOS; неподдерживаемые действия показываются с причиной или скрываются по единому правилу, но прямой callback всё равно серверно отклоняется.
- Верх device-card показывает имя, ОС, online/offline/unknown, время последнего heartbeat, версию агента, версии companion-компонентов и сводку capabilities; offline появляется только после документированного heartbeat threshold, иначе статус остаётся unknown.
- Гость может открыть публичную справку, безопасную сводку устройства и read-only обзор сервера; строки с координатами, скриншотами, файлами, результатами команд и подробными логами не выдаются. Пользователь получает только выданные device/action grants.
- Тексты и поведение X-LEX остаются побайтно/поведенчески неизменными; экран Инфо лишь показывает описание и не меняет выбор стиля.

## Source map and current coverage

- `bot.py`: screen builders `main_menu`, `devices_menu`, `device_menu`, category/admin/server/info builders; access visibility `_visible_device_items`, `_callback_visible_to_user`, `_action_button`; routes `cmd_start`, `on_menu_main`, `on_menu_devices`, `on_dev`, `on_back_to_device`, `on_admin_*`, `on_guest_*`; command gates `_start_authorized_input`, `_device_action_still_allowed`, `_simple_command_still_allowed`.
- `roles.py`: `OwnerFilter`, `AdminFilter`, `ReadOnlyFilter`, `AnyAccessFilter`; keep the protected `.env` `ADMIN_ID` as the only owner identity. The shell-request entry may use a user/device grant, but its approval callback stays owner-only; revalidate both at the command gate.
- `access_store.py`: sole role/grant authority through `get_role`, `list_users`, `set_role`, `set_blocked`, `toggle_callback`, `toggle_device`, `can_use_callback`, `append_audit`; do not add a second permission database or duplicate a second authorization policy.
- `ui_cards.py`: retain `get(chat_id: int, user_id: int) -> int | None` and `set_card(chat_id: int, user_id: int, message_id: int) -> None`; it stores the card id but Telegram edit/send lifecycle remains in `bot.py`.
- Existing tests to extend rather than duplicate: `test_rendered_menu_callbacks_match_a_registered_route`, `test_user_device_menu_only_shows_granted_devices_and_no_global_controls`, `test_single_action_grants_filter_categories_and_submenus`, `test_user_fsm_handlers_revalidate_before_device_side_effects`, `test_simple_command_does_not_publish_if_original_grant_is_revoked_during_answer`, `test_user_card_does_not_duplicate_on_transient_edit_error`, `test_callback_card_does_not_duplicate_on_transient_edit_error`, plus the role/grant cases in `test_access_store.py`. Replace the baseline test `test_shell_remains_owner_only_even_if_a_user_callback_is_granted` with assertions that a user grant permits only a pending request and never owner approval or direct publish.
- Add `action_catalog.py` only if inventory shows a concrete metadata gap that cannot be covered by those gates. If created, it is descriptive metadata (category, device scope, OS support, risk, result sensitivity); `access_store.can_use_callback(...)` remains the only authorization decision.

## TDD implementation tasks

### Task 1: Complete callback inventory and permission matrix

**Files:** Modify `TG-BOT-SERVER/tests/test_bot_ui.py` and `tests/test_access_store.py`; create a separate inventory test only if the current test cannot cleanly cover the whole builder/route set. No runtime changes in this task.

**Interface:** For every callback produced by every screen builder, record its handler route and one server-side access class: protected `OwnerFilter`, public `ReadOnlyFilter`, device/action grant through `access_store.can_use_callback(...)`, owner-approved request, or explicitly non-action. For each action record category, required permission, allowed roles/grants, OS support, timeout, result sensitivity and risk. Hidden keyboards are never the authorization boundary; sensitive data and publish paths recheck the matching authority.

- [ ] Extend `test_rendered_menu_callbacks_match_a_registered_route` to include every root, device, category, server, admin, info and confirmation keyboard builder; assert every emitted callback maps to a registered route or an explicit documented no-op.
- [ ] Add the companion metadata/access classification assertion; unknown action callbacks fail closed and no route is implicitly public because its button was hidden.
- [ ] Run `python -m pytest tests/test_bot_ui.py::test_rendered_menu_callbacks_match_a_registered_route tests/test_access_store.py -q`; record existing failures before production edits.
- [ ] Review the inventory for duplicated gates and stale callbacks; create no catalog unless a concrete metadata gap is found.

### Task 2: Rebuild screens and align button visibility with server gates

**Files:** Modify `TG-BOT-SERVER/bot.py` screen builders and only the required access helper; add/extend tests in `tests/test_bot_ui.py`. Keep `roles.py` filters authoritative.

**Interface:** Preserve callback-data for existing actions unless a route migration includes a compatibility test. For device/action controls, every non-owner button is rendered only when the same current user/device grant permits the handler; public read-only navigation and owner-only controls use their respective filters. Role checks continue at callback time and immediately before publish.

- [ ] Add failing rendered-screen tests for the approved hierarchy: main → devices/server/admin/info; device → overview/diagnostics, display/media, files, network/apps, commands/processes, power, versions/update, permissions and extra functions; all routes return/back/home inside the same card.
- [ ] Extend the role × navigation × callback × device × grant-revocation matrix to owner, granted user, ungranted user, guest and blocked across two devices; guest sees only public info, safe device summaries and read-only server overview, never sensitive results.
- [ ] Test that a guessed callback, a changed device id, unsupported OS action, blocked user and stale role/grant are denied even if a keyboard still contains the callback.
- [ ] Test that a granted user's shell grant remains request-only: the user cannot approve or publish; the positive owner preview/approve/cancel lifecycle is implemented and tested in the separate Command Approval Plan Task 3.
- [ ] Implement screen layout and visibility changes using `access_store.can_use_callback(...)`; do not blindly convert all `kb.button(...)` calls or alter action semantics.
- [ ] Run `python -m pytest tests/test_bot_ui.py tests/test_access_store.py -q`; expect the complete matrix to pass with no X-LEX edits.

### Task 3: Close admin/RBAC gaps and protect the audit

**Files:** Modify only required `on_admin_*` handlers in `TG-BOT-SERVER/bot.py`; reuse `access_store.py`; extend `tests/test_access_store.py` and `tests/test_bot_ui.py`.

- [ ] First identify uncovered cases (current suite already covers first-start guest defaults, device-scoped grants, legacy `coowner` downgrade, block revocation/redaction and FSM grant rechecks); add failing tests only for gaps: shell-request grant versus owner-only approval, unblock/re-grant behavior, owner self-demotion/self-block, `/start` notification dedupe, direct unauthorized/unknown-ID admin callback, guest/blocked data reads, and confirmation/cancel/expiry for privileged role/grant/unblock changes.
- [ ] Ensure the owner user list shows role, last activity/visit, granted devices and actions; every role change, block/unblock, per-device/action grant, text edit and outbound message flow is owner-only and sensitive changes require an explicit owner confirmation before commit.
- [ ] Verify audit redacts credentials and excludes coordinates, file contents, raw shell/stdout; assert guest/blocked cannot read admin/audit details.
- [ ] Run `python -m pytest tests/test_access_store.py tests/test_bot_ui.py -q`; expect all covered actions denied/allowed exactly by role and grant.

### Task 4: Keep routine navigation and command feedback in one card

**Files:** Modify `TG-BOT-SERVER/ui_cards.py` only if its storage contract is insufficient; update relevant screen/command handlers in `bot.py`; extend `tests/test_ui_cards.py`, `tests/test_lifecycle_copy.py`, `tests/test_bot_ui.py`.

- [ ] Add/extend transition tests for main → device → every category → operation progress/result → back/home, including repeated callbacks, missing card, Telegram edit failure and media-to-text transitions.
- [ ] Reuse the indexed card for routine navigation/results; the only separate messages are the explicitly enumerated owner security alerts (`/start`, dangerous-action confirmation and deduplicated online/offline transitions).
- [ ] Assert transient edit/send/delete errors never create a repeating message flood and the next callback can recover the current card id.
- [ ] Add a repeated identical heartbeat test; if current transition-dedup test already covers it, extend that test rather than adding a duplicate.
- [ ] Run `python -m pytest tests/test_ui_cards.py tests/test_lifecycle_copy.py tests/test_bot_ui.py -q`; expect one normal UI card per chat/user except listed security alerts.

## Приёмка

- Полный callback inventory закрыт или каждая исключённая запись объяснена.
- Роль/грант проверяются на UI, handler, FSM continuation и непосредственно перед командой.
- Все навигационные сценарии удерживают одну карточку, кроме перечисленных security alerts.
- X-LEX diff пуст; X-LEX golden tests проходят без обновления ожиданий.
- Реальный Telegram отдельно не принят, пока владелец не пройдёт smoke-test.

## Коммиты

Отдельно: inventory + контракт-тесты; RBAC/UI исправления; админ/аудит; lifecycle-card. Каждый коммит заканчивается указанными тестами и коротким diff review. Не включать `audit.log.1`.
