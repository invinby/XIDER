# TARPED Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` after owner approval. Each checkbox below is an execution gate; do not begin code before approval.

**Goal:** Довести XIDER до цельного и проверяемого обновления по одобренной спецификации TARPED, не меняя X-LEX.

**Architecture:** Реализовать пять независимо проверяемых подсистем: bot/RBAC, возможности агентов, owner-confirmed command flow, релизно-серверный контур, RU/EN docs/Pages. Интегрировать их по зависимостям, затем отдельно принимать устройства, Telegram, VPS и GitHub publication.

**Tech Stack:** Python, aiogram, MQTT, pytest, PowerShell/bash deployment fixtures, GitHub Actions/Pages, HTML/CSS.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`.

## Global Constraints

- Source of truth: `origin/main` `984204a8eba1fd42b52c9d344a9e0dbf469eb779`; do not merge divergent `Desktop\XIDER`.
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
**Статус:** подготовлен на базе одобренной спеки; ждёт отдельного одобрения владельца  
**Спецификация:** [TARPED product redesign](../specs/2026-10-08-tarped-product-redesign-design.md)  
**База аудита:** `origin/main` `984204a8eba1fd42b52c9d344a9e0dbf469eb779`; bot/agent/ops/deploy source paths сверены с этой базой и не расходятся. Пять локальных коммитов поверх upstream — документация. В working tree есть локальная Mermaid-правка двух README и regression test; `TG-BOT-SERVER/audit.log.1` сохранён без изменений.  
**Проверка upstream refs (2026-10-08):** `git ls-remote origin refs/heads/main` подтвердил `984204a8eba1fd42b52c9d344a9e0dbf469eb779`; удалённый tag `v4.2.1` указывает на commit `d7d77a698b763f169327ec99e350ff73f5e34f16`, то есть старше текущего `main`. Поэтому реализационная ветка создаётся от актуального `origin/main`; v4.2.1 остаётся точкой сравнения, а не основанием для отката main.  
**Инвариант:** не менять X-LEX — ни данные, ни стиль-тексты, ни логику выбора стиля, ни связанные тесты.

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
- План ещё не разрешает менять исходники. Нужна отдельная явная команда владельца на выполнение плана и выбор способа реализации.

## Выполнение по шагам

- [ ] Task 0: зафиксировать текущую ревизию и baseline; сохранить X-LEX hash/golden output; сверить, что target diff не включает user files.
- [ ] Task 1: выполнить bot/RBAC plan и пройти его тесты; только после review его callback matrix переходить к shell approval.
- [ ] Task 2: выполнить agent-capabilities plan и обновить общий bot/protocol contract.
- [ ] Task 3: выполнить command-approval plan поверх принятой RBAC-модели.
- [ ] Task 4: выполнить release/server plan поверх принятого status/capability contract.
- [ ] Task 5: выполнить docs/Pages plan; адреса публикации остаются неактивными до подтверждения.
- [ ] Task 6: выполнить полный `python tools/run_tests.py`, затем отдельную физическую/Telegram/VPS/GitHub приёмку с журналом фактических результатов.
