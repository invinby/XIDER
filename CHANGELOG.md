# XIDER release history

## X4.1.4-TARPED+20261006 — X-DOCK legacy recovery command

### English

- When an old or packaged agent cannot use the transactional in-bot updater,
  the update screen now shows the signed one-line installer for that device's OS.
- The bot still refuses to run an unsupported remote update; the owner chooses
  whether to copy and run the recovery command on the device.
- Kept Windows, macOS, and bot version identity synchronized at 4.1.4.

### Русский

- Если старый агент или EXE-сборка не поддерживает транзакционное обновление
  из бота, экран теперь сразу показывает подписанную команду установки для его ОС.
- Бот по-прежнему не запускает неподдерживаемое обновление сам: владелец решает,
  копировать ли команду и запускать её на устройстве.
- Версии бота, Windows- и macOS-агентов синхронизированы на 4.1.4.

## X4.1.3-TARPED+20261006 — XIDER LINK rejection diagnostics

### English

- The bot now distinguishes malformed MQTT envelopes, HMAC mismatches, stale
  timestamps, missing nonces, and replayed messages in its local logs.
- Diagnostic logs include no message payload, signature, or key material.
- Windows and macOS behavior is unchanged; all components share the 4.1.3
  release identity required by the device compatibility checks.
- The existing owner-only one-line Windows/macOS installer and signed VPS
  update/rollback flow remain unchanged.

### Русский

- Бот отдельно показывает в локальном журнале повреждённый MQTT-конверт,
  несовпадение HMAC, устаревшее время, отсутствие nonce и повтор сообщения.
- В диагностический журнал не попадают тело сообщения, подпись или ключи.
- Поведение агентов Windows и macOS не менялось; у компонентов общая версия 4.1.3,
  требуемая проверками совместимости устройств.
- Кнопка владельца с одной командой установки и подписанное обновление/откат
  VPS остаются без изменений.

## X4.1.2-TARPED+20261005 — X-STAB archive-root fix

### English

- Fixed production updates for the signed GitHub source archive, which contains
  its files under the `XIDER-source/` directory.
- Added a regression fixture that exercises that exact archive layout through
  backup, health check, automatic rollback, and manual rollback paths.
- Kept the bot and both agents on synchronized version 4.1.2.

### Русский

- Исправлено обновление VPS из подписанного GitHub-архива: файлы релиза лежат
  внутри папки `XIDER-source/`, и сервер теперь корректно распаковывает её.
- Регрессионный тест прогоняет именно эту структуру через резервную копию,
  проверку здоровья, автоматический и ручной откат.
- Версии бота и обоих агентов синхронизированы на 4.1.2.

## X4.1.1-TARPED+20261005 — X-DOCK manual setup

### English

- Added an owner-only manual setup button to the Telegram device list.
- The OS picker returns one copy-ready signed installer command for Windows or
  macOS and keeps the flow in the current bot card.
- Includes the callback privacy, updater recovery, and Guard Keeper stability
  fixes validated in the TARPED release candidate.
- Aligned the bot, Windows agent, and macOS agent version strings at 4.1.1.

### Русский

- В список устройств добавлена доступная только владельцу кнопка ручной установки.
- После выбора Windows или macOS бот показывает одну готовую к копированию
  команду подписанного установщика; переходы остаются в текущей карточке.
- В релиз включены проверенные исправления приватности callback-ов, восстановления
  обновлятора и стабильности Guard Keeper из кандидата TARPED.
- Версии бота и обоих агентов синхронизированы на 4.1.1.

## X4.0.0-TARPED+20260927 — TARPED foundation

Deployed to the XIDER VPS on 2026-09-27. Release artifacts are built from the
matching Git tag by GitHub Actions.

### English

- Started X-LEX with six named voices and a test for missing phrases.
- Added a chapter-based in-bot handbook and X-LEDGER's read-only GitHub
  Releases browser for agent, Guard Keeper and server components.
- Prepared future release packaging for Windows/macOS agents, Guard Keeper,
  the server and a SHA-256 asset inventory. Verified installation and A/B
  rollback are still outstanding.
- Added X-LOCK to prevent two updated Windows agents from creating duplicate
  tray icons. Existing running older copies are not stopped automatically.
- Improved Windows/macOS IP-location error handling, HTTPS fallback and
  approximate-location labeling. Removed decorative progress edits.
- Preserved an existing protected Windows env file during repeat bootstrap.

### Русский

- Начата X-LEX: шесть стилей текста и тест на неполный словарь.
- Добавлены главы «О XIDER» и X-LEDGER — просмотр GitHub-выпусков отдельно
  для агента, Guard Keeper и серверной части.
- Подготовлена сборка пакетов и список SHA-256 для будущих выпусков. Установка
  выбранной версии и проверенный откат A/B пока не реализованы.
- X-LOCK предотвращает запуск двух обновлённых Windows-агентов и дубли
  значков. Уже работающие старые копии автоматически не останавливаются.
- Геолокация по IP честно помечена как приблизительная; обработка ошибок
  улучшена, небезопасный HTTP-запасной источник удалён. Убран декоративный
  цикл редактирования сообщения при ожидании команды.
- Повторная установка Windows сохраняет существующий защищённый env-файл.

## X3.3.8+20260926 — XIDER Guardian supervisor

### English

- Added the visible macOS `XIDER Guardian` supervisor as a separate LaunchAgent.
- Added Telegram controls for Guardian status, agent start/stop/restart, and
  opt-in automatic recovery.
- Guardian keeps the recovery channel available while the worker agent is
  stopped; it does not collect camera, microphone, screen, or location data by
  itself and does not bypass local operating-system controls.
- Added a VPS-side heartbeat/LWT explanation and truthful offline behavior: a
  powered-off laptop can only report its last heartbeat.
- Legacy Mac agents can bootstrap the new Guardian through the existing shell
  update fallback.

### Русский

- Добавлен видимый supervisor `XIDER Guardian` для macOS через отдельный
  LaunchAgent.
- В Telegram появились кнопки статуса Guardian, запуска, остановки,
  перезапуска агента и опционального автовосстановления.
- Guardian сохраняет канал восстановления, пока рабочий агент остановлен, но
  сам не включает камеру, микрофон, запись экрана или геолокацию и не обходит
  локальный контроль macOS.
- VPS использует heartbeat/LWT и честно показывает последний момент связи,
  если ноутбук выключен или разряжен.
- Старый Mac-агент умеет получить Guardian через запасной shell-канал кнопки
  обновления.

## X3.3.5+20260925 — Agent status checks

- Added exact background status commands for Windows and macOS agent launchers.

## X3.3.4+20260925 — Custom copy and VPS control panel

- Added a third, owner-editable custom text mode for the bot.
- Added VPS load snapshots, PNG history charts and a restricted diagnostic command panel.
- Added a narrow root helper for service status, logs, restart, update and rollback.
- Callback results for common device actions now replace the existing card when Telegram allows it.

## X3.3.3+20260925 — Correlated command responses

- Legacy status, media, system, network and file-adjacent requests now carry
  a unique command id through the response wait path.
- Fast replies are retained briefly, so an agent response arriving before the
  Telegram handler starts waiting is still delivered to the correct request.
- Parallel users or repeated button presses no longer mix responses between
  commands of the same type.

## X3.3.0+20260925 — Server operations and controlled copy

- Owner-only server panel now exposes service status, recent logs, restart,
  prepared-package update and rollback, with explicit confirmation for
  destructive operations.
- Added editable owner-managed bot texts, while escaping user-provided copy
  before rendering it as Telegram HTML.
- File transfer now accepts the canonical `data`/`filename` schema and keeps
  compatibility aliases for existing Windows agents; uploads honor an
  explicit destination path on both agents.
- Added MQTT ACL configuration and a backup/health-check/rollback updater plus
  a secret-rotation helper. Runtime secrets remain in `.env` files only.

## X3.2.0+20260925 — Agent reliability and truthful capabilities

- Command responses now carry the MQTT correlation id through both agents,
  preventing one user's fast response from being shown for another command.
- Bot waits use tracked command ids and no longer reuse stale text after a
  timeout; MQTT failures are reported immediately.
- Windows and macOS geolocation reports use HTTPS and return a truthful
  failure status when the lookup is unavailable.
- macOS brightness/night-light/rotation now report unsupported dependencies
  instead of falsely claiming success; brightness uses the optional
  `brightness` utility when installed.
- Added the safe local launcher path and developer-contact settings while
  retaining the owner/access-control layer.

## X3.1.0+20260925 — Access control foundation

- Roles: protected owner, co-owner, user and guest.
- Owner-only administration panel: users, roles, blocks, per-device grants,
  per-button grants, direct owner-to-user messages and a privacy-aware audit log.
- New `/start` notifications to the owner. New accounts start as guests.
- Device selection is now isolated by Telegram account instead of one global
  selected target.
- Server section: MQTT health visibility and a switch for confirmation of new
  device registrations.
- Runtime reset scripts preserve `.env`, SSH keys and all credentials while
  backing up then clearing device/user/log state.

## Version format

`X<major>.<minor>.<patch>+<YYYYMMDD>`

- **major** — protocol or architecture break;
- **minor** — a delivered feature group;
- **patch** — compatible fixes;
- **build date** — exact release build date.

Tags use the compatible Git form `v<major>.<minor>.<patch>`, for example
`v3.1.0`. Existing tags and GitHub releases are never removed by this flow.
