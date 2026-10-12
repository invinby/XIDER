# TARPED Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` after owner approval. Each checkbox below is an execution gate; do not begin code before approval.

**Goal:** Довести XIDER до цельного и проверяемого обновления по одобренной спецификации TARPED, не меняя X-LEX.

**Architecture:** Реализовать пять независимо проверяемых подсистем: bot/RBAC, возможности агентов, owner-confirmed command flow, релизно-серверный контур, RU/EN docs/Pages. Интегрировать их по зависимостям, затем отдельно принимать устройства, Telegram, VPS и GitHub publication.

**Tech Stack:** Python, aiogram, MQTT, pytest, PowerShell/bash deployment fixtures, GitHub Actions/Pages, HTML/CSS.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`.

## Global Constraints

- Source of truth: always re-check official `origin/main` before work; the post-PR-#19 snapshot was `9794199001d653e0bc710964e08e4b19d79a1406`. Tag `v4.2.1` is a historical comparison point, not a reset target. Do not merge divergent `Desktop\XIDER`.
- Do not change any X-LEX dictionary, authored text, selection logic, runtime behavior, or golden test.
- Keep one editable Telegram navigation card except separately delivered security alerts.
- Enforce authorization in callback/FSM/action execution, not only by hiding buttons.
- No bypass of TCC/UAC, no hidden/anti-owner components, no arbitrary VPS shell.
- The owner retains documented local stop/uninstall for each companion; no component silently reinstalls itself after removal.
- Live device/Telegram/VPS/GitHub claims require actual acceptance evidence.
- Preserve untracked/user files, including `TG-BOT-SERVER/audit.log.1`.
- No push, release, GitHub metadata/Pages publish, or VPS deploy without separate owner approval.

## Review Focus

1. Owner/user/guest/blocked role × device × action × stale-FSM input — rejected or allowed exactly as granted; tested in the bot RBAC plan.
2. Missing, denied, or stale capability and permission response — never rendered as granted; tested in the agent capabilities plan.
3. Replayed/expired/tampered shell confirmation — never executes; tested in the command approval plan.
4. Malicious release archive, bad signature, failed health check, rollback timeout — does not falsely report success; tested in release/server plan.
5. RU/EN content drift, invalid Mermaid, reduced-motion preference — discovered by docs/site checks before publication.

---

**Дата:** 2026-10-08  
**Статус:** локальная реализация и Pages-публикация выполнены; Task 6 остаётся открытым до реальной приёмки физической Windows/macOS, Telegram и VPS.
**Спецификация:** [TARPED product redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**База аудита:** `origin/main` `984204a8eba1fd42b52c9d344a9e0dbf469eb779`; рабочая ветка `codex/tarped-implementation-2026-10-08`. Снимок перед финальной fix wave (2026-10-11): HEAD `9b6b3ceded06e265a72b679b36e52ffa3c3589d0`, 52 commit ahead / 0 behind; это историческая точка ревью, а не текущий HEAD. Статусные правки и allowlist pinned Actions закреплены этим коммитом. Точный SHA после исправлений и актуальная проверка записываются в локальном `.superpowers/sdd/2026-10-08-tarped-implementation-index/progress.md` и `fix-wave-report.md`. Незатреканные `TG-BOT-SERVER/audit.log.1` и `.2` сохраняются без чтения и изменений; `tools/node_modules/` — локальная зависимость Mermaid parser, не включать в коммит.
**Проверка upstream refs (2026-10-08):** `git ls-remote origin refs/heads/main` подтвердил `984204a8eba1fd42b52c9d344a9e0dbf469eb779`; удалённый tag `v4.2.1` указывает на commit `d7d77a698b763f169327ec99e350ff73f5e34f16`, то есть старше текущего `main`. Поэтому реализационная ветка создаётся от актуального `origin/main`; v4.2.1 остаётся точкой сравнения, а не основанием для отката main.  
**Инвариант:** не менять X-LEX — ни данные, ни стиль-тексты, ни логику выбора стиля, ни связанные тесты.

**Сверка выполнения (снимок 2026-10-11, до отдельной Pages-публикации):** владелец одобрил выполнение implementation plan. Bot/RBAC Tasks 1–4, Agent Capabilities Tasks 1–4, Command Approval local Tasks 1–4 и Release/Server Tasks 1–7 реализованы локально и имеют свои тесты. Docs/Pages Tasks 1–3 локально реализованы; README/RU-EN проверки прошли (**23 passed, 2 subtests**), Mermaid CLI валиден для обоих README, сайт прошёл проверки, а Pages workflow/source tests зелёные. Полный `python tools/run_tests.py` завершился успешно: bot **821 passed, 1 skipped**; Windows agent **194 passed**; macOS agent **300 passed, 1 skipped**; operations **28 passed, 1 skipped**; release tools **105 passed, 2 subtests**; archive/server signatures **24 passed, 27 skipped**; Windows deployment preflight/rollback fixtures прошли. `compileall` завершился с кодом 0. X-LEX Git blob совпадает с `origin/main`; целевые golden-тесты — **15 passed**. На момент этого снимка оставались Pages/repository metadata publication, macOS frozen artifact/live acceptance, физический Windows/macOS smoke, Telegram→MQTT→device smoke и VPS health/update/rollback acceptance. Последний статус Pages заменён актуальной сверкой ниже; никакая VPS-доставка или физическое устройство тем снимком не подтверждались.

**Снимок GitHub до PR #19 (2026-10-12):** `origin/main` был `05113f9839f3e591003522ea9b2f5fbb0512f39b`. Владелец отдельно подтвердил URL и разрешил публикацию; Pages переведён на GitHub Actions (`build_type=workflow`, `status=built`), опубликованные `/`, `/ru.html` и `/styles.css` отвечают HTTP 200, homepage репозитория указывает на этот адрес, двуязычное описание сохранено. Фокусная проверка permission UI на том `main`: bot `tests/test_capabilities.py` — **35 passed**, Windows agent — **2 passed**, macOS agent — **7 passed**; X-LEX blob совпадает с upstream. Кнопка находится в карточке устройства: **Система → Возможности**. Это подтверждает код и тесты, но не фактическую версию работающего Telegram-сервиса или агента. На момент снимка в GitHub были environments `github-pages` и `xider-release-signing`; `xider-macos-signing` и семь необходимых Apple signing/notarization credentials отсутствовали. Значения секретов не читались. Скачанный `v4.2.1` macOS ZIP совпал по SHA-256 с записью manifest и прошёл ZIP CRC, но содержал только `XGENT-MCS`, `.env.example`, `start_agent.sh`, `stop_agent.sh` — `XIDER.app` внутри нет. TCP/22 до известных VPS-адресов с Windows-хоста не устанавливался за 3 секунды; SSH-аутентификация и изменения не выполнялись, что не доказывает выключенное состояние VPS. Физические Mac/Windows, live Telegram→MQTT→устройство и VPS health/update/rollback оставались непроверенными.

**Свежая локальная приёмка до PR #19 (2026-10-12, ветка `docs/update-tarped-acceptance`, база `05113f9839f3e591003522ea9b2f5fbb0512f39b`):** `python tools/run_tests.py` завершился с кодом 0: Telegram bot **854 passed, 1 skipped**; Windows agent **197 passed**; macOS agent **305 passed, 1 skipped**; operations **28 passed, 1 skipped**; release tools **111 passed, 2 subtests passed**; archive/server signatures **24 passed, 27 skipped**; все Windows bootstrap preflight, installer selection, staged install/rollback и full bootstrap transaction fixtures прошли. `python -m compileall -q TG-BOT-SERVER XGENT-WDS XGENT-MCS ops tools deploy` и `python tools/check_site.py` завершились с кодом 0. Фокусный shell approval прогон: bot approval/signing/UI **374 passed**, MQTT callback seam **2 passed**. Свежая проверка цепочки разрешений/location/screenshot: bot **67 passed**, Windows agent **30 passed**, macOS agent **49 passed**; `bash -n XGENT-MCS/start_agent.sh XGENT-MCS/stop_agent.sh` завершился с кодом 0. Исходники связывают owner-only кнопку бота с allowlisted `permission_settings`; обе платформы открывают только фиксированные системные панели, неизвестный target отвергается. На Mac `start_agent.sh --setup` передаёт запрос в подписанный bundle для видимого `--location-setup`; физический consent и открытие панели остаются непроверенными. X-LEX diff пустой; blob `TG-BOT-SERVER/xlex.py` совпадает с базой (`bd97c4bdf337e129adcb7092bf423dce102fbcb2`). `tools/node_modules/` — локальная незатреканная зависимость Mermaid parser, не включать в коммит. TCP-only пробы до трёх VPS-хостов не получили ответа на TCP/22; SSH-аутентификация и серверные изменения не выполнялись. В checkout отсутствовали локальные `.env`, поэтому live Telegram API/device smoke не запускался. Локальный Windows candidate прошёл frozen verifier и public ZIP verifier, оба WinRT-модуля присутствовали в EXE; этот false-green packaging gate был исправлен PR #19. Кандидат остался в `%TEMP%`, не опубликован и не установлен на физическую Windows-машину. На момент этого снимка у ветки ещё не было PR; позднее PR #19 прошёл CI и был squash-merged в `main` как `9794199001d653e0bc710964e08e4b19d79a1406`.

**Удалённая сверка macOS-скриншотов (2026-10-12):** PR #16 `fix(screenshot): resolve capture and delivery failures` уже merged в `main` (merge-коммит `6e5aef6` присутствует в `origin/main` `05113f9`); CI `38178405385` успешен. Это подтверждает код и CI, не проверку захвата/доставки на физическом Mac или через живого Telegram-бота.

**Свежая проверка маршрута разрешений (2026-10-12):** `python -m pytest TG-BOT-SERVER/tests/test_capabilities.py TG-BOT-SERVER/tests/test_bot_ui.py -q` → **354 passed**. В коде путь: карточка устройства → `Система` → `Возможности`; код callback — `cmd:capabilities`. Это не подтверждает, что уже запущенный Telegram-сервис обновлён.

**Приёмка после PR #19 (2026-10-12):** merge commit `9794199001d653e0bc710964e08e4b19d79a1406`; main CI run `38231800352` успешно завершился — Python 3.11, Python 3.12 и Unix installers прошли. В исходниках `CHANGELOG.md` уже описывает X4.3.0-TARPED/X-PANE, но GitHub Releases всё ещё показывает только `v4.2.1`; tag `v4.3.0` не опубликован. Релиз нельзя считать готовым до подписанного/notarized macOS ZIP и физических/сервисных acceptance gates. Environment `xider-macos-signing` отсутствует; repo-level secrets list пуст, значения secrets не запрашивались и не читались.

## Цель и способ разбиения

Это не один огромный патч. План разбит на пять самостоятельных частей, каждая из которых должна закончиться зелёными локальными тестами и понятным review-диффом. Общая интеграция идёт в указанном порядке; физический Mac/Windows, живой Telegram, VPS и публикация GitHub проверяются отдельными слоями и не объявляются готовыми по результатам unit-тестов.

1. [Бот, меню и RBAC](2026-10-08-tarped-plan-bot-rbac.md)
2. [Возможности и диагностика Windows/macOS](2026-10-08-tarped-plan-agent-capabilities.md)
3. [Одноразовое подтверждение команд агента](2026-10-08-tarped-plan-command-approval.md)
4. [Релизы, обновления, canary и сервер](2026-10-08-tarped-plan-release-server.md)
5. [RU/EN-документация и GitHub Pages](2026-10-08-tarped-plan-docs-pages.md)

## Покрытие спецификации планами

| Раздел спеки | Где реализуется и проверяется |
| --- | --- |
| §4–5: единая карточка, новая навигация, категории и версии устройств/companions | [Бот/RBAC](2026-10-08-tarped-plan-bot-rbac.md): Tasks 1, 2, 4; [релизы/сервер](2026-10-08-tarped-plan-release-server.md): Tasks 1–3, 5 |
| §6: владелец/пользователь/гость/blocked, grants, уведомления и аудит | [Бот/RBAC](2026-10-08-tarped-plan-bot-rbac.md): Tasks 1–3 |
| §7–9: capability states, разрешения и настройки, system location/IP fallback, screenshots | [Возможности агентов](2026-10-08-tarped-plan-agent-capabilities.md): Tasks 1–3 |
| §10: точное одноразовое owner approval для произвольной команды | [Подтверждение команд](2026-10-08-tarped-plan-command-approval.md): Tasks 1–4 |
| §11: существующий open-site flow, схемы URL, количество окон и deduplication | [Возможности агентов](2026-10-08-tarped-plan-agent-capabilities.md): Task 4 |
| §12: health, логи, VPS allowlist, версии, canary, per-action freshness и sanitized diagnostics export | [Релизы/сервер](2026-10-08-tarped-plan-release-server.md): Tasks 3–7; [бот/RBAC](2026-10-08-tarped-plan-bot-rbac.md): Task 4 |
| §13: RU/EN parity, Mermaid, landing page, accessibility, guarded Pages | [Документация/Pages](2026-10-08-tarped-plan-docs-pages.md): Tasks 1–4 |
| §14–16: baseline, X-LEX invariant, layered acceptance and exclusions | global gates below and Task 6; all subsystem acceptance sections |

## Порядок выполнения и контрольные ворота

### Этап 0 — чистая база и защитные инварианты

- Зафиксировать исходный SHA `origin/main` и результаты компонентных тестов непосредственно перед кодовыми изменениями.
- Сохранить/сравнить X-LEX-данные и golden-поведение до и после; не редактировать X-LEX-файлы.
- Работать только в XIDER-TARPED; локальную расходящуюся `Desktop\XIDER` не сливать.
- Сохранить незатреканный пользовательский `TG-BOT-SERVER/audit.log.1`: не читать без необходимости, не менять, не удалять, не добавлять в коммит.
- Рабочую ветку/worktree для реализации создать от зафиксированной базы `origin/main`. Перенести в неё согласованные spec/plan-документы и только явно перечисленные ниже изменения; не делать широкую cherry-pick/копию локальных docs-коммитов.
- Явно сохранить уже запрошенную и проверенную Mermaid-правку `README.md`/`README.ru.md` и `tools/test_readme_mermaid.py`: если новая ветка от `origin/main` их не содержит, перенести именно эти hunks/файл и повторно прогнать Mermaid-тест.
- Остальные рабочие/незатреканные материалы, включая `TG-BOT-SERVER/audit.log.1` и `docs/research/`, не включать, не удалять и не менять без отдельного разбора владельца.

**Выход:** воспроизводимый baseline, чистый целевой diff и зафиксированный X-LEX-инвариант.

### Свежий baseline перед кодовыми изменениями

Запущено `python tools/run_tests.py` на Windows checkout 2026-10-08 после Mermaid-only правки README и добавления regression test; product source files не менялись. Результат: Telegram bot **248 passed**; Windows agent **73 passed**; macOS agent **146 passed, 1 skipped**; operations **26 passed, 1 skipped**; release tools **57 passed, 2 subtests**; deployment archive/server signatures **24 passed, 10 skipped**. Все пять включённых Windows deployment fixtures также прошли: preflight, installer selection, staged install/rollback и full bootstrap transaction. Итог скрипта — `PASS: 6 isolated component suites and platform deployment fixtures`.

Это только автоматический Windows-host прогон; Linux/macOS shell fixtures, физический Mac, Telegram live и VPS этим запуском не проверялись.

Свежий контроль X-LEX перед реализацией: `TG-BOT-SERVER/xlex.py` совпадает с `origin/main` по Git blob (`bd97c4bdf337e129adcb7092bf423dce102fbcb2`) и имеет SHA-256 `1D5D9A179DCC7CC94234B907C0DE6069DC3AC9B0688A1705EB281119E9A87B23` в этом Windows checkout; целевые тесты `python -m pytest -q TG-BOT-SERVER/tests/test_tarped.py TG-BOT-SERVER/tests/test_bot_ui.py -k xlex` — **15 passed, 178 deselected**. После реализации повторить сравнение Git blob, хэш и тот же набор тестов.

В продолжении 2026-10-08 отдельно прогнаны `tools/test_readme_mermaid.py` (**1 passed**) и `@mermaid-js/mermaid-cli` **12.0.0**: каждый README извлёк и отрендерил по одной Mermaid-диаграмме без ошибки. Это локальная проверка Mermaid-парсера, не подтверждение опубликованного GitHub-рендера.

### Этап 1 — пользовательский интерфейс и права

Выполнить план бота/RBAC. Сначала инвентаризировать клавиатуры и callbacks, затем унифицировать отображение действий с серверной проверкой. Только после зелёной матрицы ролей переходить к shell approval.

**Ворота:** полный inventory callback → keyboard → handler → permission; owner/user/guest/blocked тесты; отозванное право не продолжает старую FSM-форму.

### Этап 2 — возможности устройств

Выполнить план agent capabilities: единый контракт статусов, конкретная диагностика screenshot, системная геолокация с явно указанным источником и проверенная ссылка на настройки. Существующий «Открыть сайт» улучшать на месте.

**Ворота:** обе компонентные тест-сборки зелёные; macOS `.app` проходит проверку non-ad-hoc подписи, bundle ID и `NSLocationUsageDescription`; если signing identity или реального Mac/Windows нет, релиз/устройство остаются явно непринятыми.

### Этап 3 — подтверждение произвольной команды

Выполнить план command approval после этапа 1. Ни строка команды, ни сменённая команда/устройство не могут исполниться без одноразового актуального подтверждения владельца.

**Ворота:** replay, expiry, подмена команды/устройства, отказ не-владельца и гонка двух подтверждений покрыты тестами. Без надёжной проверки доверенного MQTT-источника — не включать новый поток и не считать его безопасным.

### Этап 4 — версии, обновления и сервер

Выполнить план release/server после контрактов UI и capabilities. Сначала проверить имеющийся подписанный manifest и транзакционный updater, затем связать каталог версий, canary и отдельную серверную карточку. Не менять live-сервис в рамках unit-этапа.

**Ворота:** плохая подпись/архив/health-check/rollback покрыты тестами; опасные server-действия подтверждаются; диагностический экспорт ограничен и не содержит секретов, координат, файлов или raw shell; секреты отсутствуют в ответах и логах.

### Этап 5 — документация и публичная страница

Выполнить план docs/Pages после стабилизации видимого функционала. GitHub repo description/homepage и реальную публикацию менять только в отдельном разрешённом publish-шаге после проверки адресов.

**Ворота:** RU/EN-паритет, валидные ссылки/diagram build, сборка статического сайта и reduced-motion сценарий.

### Этап 6 — интеграционная приёмка

1. Полный локальный тест-набор всех компонентов.
2. Статические проверки: `python -m compileall -q TG-BOT-SERVER XGENT-WDS XGENT-MCS ops tools deploy`; CI также проходит `bash -n` и Windows PowerShell parser fixtures.
3. Сборка и проверка подписанных артефактов; отдельный тест отклонения неподписанного/изменённого артефакта.
4. Физический smoke-test Windows и macOS с разрешениями, которые владелец выдаёт сам.
5. Сквозной Telegram-сценарий ролей, одной карточки, уведомлений и подтверждения shell.
6. VPS health/log/update/rollback только при доступном сервере и отдельном разрешении на deployment.
7. GitHub Releases/Pages — только после проверки адресов и отдельного разрешения на публикацию.

Для каждого слоя записывать фактическую команду/устройство, результат и дату. Непроверенный слой явно остаётся открытым.

## Общие команды проверки

Из корня репозитория:

```powershell
python tools/run_tests.py
python -m compileall -q TG-BOT-SERVER XGENT-WDS XGENT-MCS ops tools deploy
```

Для точечной обратной связи выполнять команды из соответствующего под-плана. Проверено по текущему `tools/run_tests.py`: скрипт не имеет режима `--help` — он игнорирует аргументы и запускает все изолированные pytest suites плюс fixtures текущей платформы. Поэтому выполнять ровно `python tools/run_tests.py`; интеграционный прогон обязателен после последнего кода и не заменяется прежним baseline.

## Ограничения изменений

- Никакого скрытия процессов, anti-uninstall/anti-owner, обхода TCC/UAC или бессогласного старта камеры/микрофона/локации.
- Никакой произвольной команды VPS: серверный терминал остаётся allowlist-командами.
- Не включать у пользователя гостя доступ к геолокации, скриншотам, файлам или логам по умолчанию.
- Не отправлять уведомления «установлено/обновлено» до подтверждения агентом и health-check.
- Не пушить, не создавать/публиковать релиз, не обновлять GitHub metadata/Pages и не деплоить на VPS без соответствующего отдельного подтверждения владельца.

## Пять review-focus вопросов перед началом реализации

1. **X-LEX:** доказано ли diff-ом, что его данные, тексты, маршрутизация и golden-поведение не изменились?
2. **Авторизация:** совпадают ли UI grants, callback handler, FSM continuation и фактическая публикация команды?
3. **Доверие команд:** подтверждает ли бот точный хэш команды/цели и нельзя ли replay/подменить подтверждение или источник MQTT?
4. **Устройство/обновление:** показывают ли статусы источник и время; валидируются ли подпись, целевая версия, backup, health-check и rollback; подтверждена ли non-ad-hoc подпись macOS `.app`, нужная для Core Location?
5. **Честность готовности:** отделены ли CI, пакет/подпись, Telegram, VPS и физические устройства; не заявлено ли непроведённое как успешное?

## Самопроверка плана

- Все крупные области одобренной спеки имеют отдельный под-план выше.
- Самые рискованные изменения (RBAC и shell approval) идут до UI-полировки/публикации.
- Версия на устройстве и версия бота/VPS не смешиваются.
- Внешние изменения и реальные устройства вынесены в явный acceptance gate.
- Владелец одобрил локальную реализацию плана; внешние действия по-прежнему требуют отдельного явного подтверждения. Статус реализации и критерии каждого слоя обновляются по фактическим отчётам, а не по старым ожиданиям этого документа.

## Выполнение по шагам

- [x] Task 0: зафиксировать текущую ревизию и baseline; сохранить X-LEX hash/golden output; сверить, что target diff не включает user files.
- [x] Task 1: выполнить bot/RBAC plan и пройти его тесты; только после review его callback matrix переходить к shell approval.
- [x] Task 2: выполнить agent-capabilities plan и обновить общий bot/protocol contract.
- [x] Task 3: выполнить command-approval plan поверх принятой RBAC-модели; локальная реализация и тесты закрыты, macOS artifact/live acceptance остаются в Task 6.
- [x] Task 4: выполнить release/server plan поверх принятого status/capability contract; локальные Tasks 1–7 закрыты.
- [x] Task 5: Docs/Pages Tasks 1–4 завершены; canonical URL подтверждён, публикация разрешена и проверена вживую 2026-10-12 (см. актуальную сверку выше).
- [ ] Task 6: локальные полные тесты, `compileall`, Mermaid/site checks и X-LEX invariant прошли (свежие результаты выше). 2026-10-13 официальный `origin/main` `09d75d466c3c25e85266a12733e690c2d2516f94` подписанно развёрнут на основной VPS: post-deploy `xider-bot.service` active/enabled, `NRestarts=0`, live `bot.py` SHA-256 совпадает с `origin/main`; backup создан и архив проверен без `.env`/ключей. Pages уже на Actions и live endpoints отвечают 200. Не проверены: live rollback, физические Windows/macOS smoke, Telegram→MQTT→device. Новый GitHub Release не создан; последним остаётся `v4.2.1` от 2026-10-07, а `xider-macos-signing` отсутствует, поэтому signed/notarized macOS artifact и Core Location acceptance остаются открытыми.
