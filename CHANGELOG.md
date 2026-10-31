# XIDER release history

## X4.3.13-LOCK-TRACE+20261031 — Windows install lock diagnostics

### English

- Windows updates now retry the staged directory switch and report the exact process and parent process holding files if activation remains blocked.
- Interrupted source archive downloads are retried and validated before the active version is touched.
- macOS remains a signed source-preview installer; its runtime is not Apple-signed or notarized.

### Русский

- При обновлении Windows повторяется переключение подготовленной папки; если его блокируют открытые файлы, установщик показывает удерживающий процесс и его родителя.
- Оборванная загрузка исходников повторяется и проверяется до изменения активной версии.
- macOS остаётся подписанным source-preview установщиком; runtime не подписан Apple и не нотарифицирован.

## X4.3.12-RECOVERY+20261031 — verified rollback and replay bounds

### English

- Failed Windows updates now report the requested version separately from the active version. A rollback is reported as recovered only when fresh Agent and Guardian health, distinct live managed PIDs, and running scheduled tasks are verified; otherwise the report is partial.
- Windows install reports derive Agent and Guardian status separately from their own version, process, and fresh health evidence.
- Server, Windows, and macOS replay caches now stay bounded without evicting still-valid nonces. If the cache reaches capacity, new messages fail closed until expired entries can be removed.
- Bot status freshness uses server receipt time after signature and replay validation, so delayed valid packets do not make a live device appear stale.
- Windows is a publisher-signed preview. macOS is a publisher-signed source preview; the runtime is not Apple-signed or notarized, and this is not the full compiled Mac app release.

### Русский

- После сбоя обновления Windows отчёт отдельно показывает запрошенную и активную версии. Откат считается восстановленным только после проверки свежего здоровья Agent и Guardian, разных живых PID управляемых процессов и запущенных задач; иначе результат — `partial`.
- Статусы Agent и Guardian в отчёте установки Windows вычисляются раздельно по версии, процессу и свежему health-ответу каждого компонента.
- Кэши защиты от повторов на сервере, Windows и macOS ограничены по размеру и не выкидывают ещё действующие nonce. При заполнении новые сообщения отклоняются до освобождения места просроченными записями.
- Свежесть статуса бота считается по времени приёма сервером после проверки подписи и повтора: задержанный, но допустимый пакет больше не помечает подключённое устройство устаревшим.
- Windows — подписанный издателем preview. macOS — подписанный издателем source-preview; runtime не подписан Apple и не нотарифицирован, это не полный собранный Mac-релиз.

## X4.3.11-TARPED+20261031 — device identity and verified component health

### English

- The bot rejects delayed MQTT Last Will messages from retired device sessions and ignores retained historical status packets.
- Windows and macOS publish replaceable status with QoS 0; command responses keep bounded retry queues and are freshly signed when resent.
- macOS installation reports now match each Agent, Guard Keeper, Safari, and Siri health record to the active launchd process, version, and fresh timestamp. A running Agent no longer implies the layers are installed.
- Windows update diagnostics include the process holding the install directory, and interrupted source downloads retry before the active version is changed.
- This release is a signed Windows package and a signed macOS source-preview installer. The macOS runtime is not Apple-signed or notarized.

### Русский

- Бот отклоняет запоздавший MQTT Last Will от старой сессии устройства и игнорирует сохранённые брокером исторические статусы.
- Windows и macOS отправляют заменяемые статусы с QoS 0; ответы на команды сохраняются в ограниченной очереди и при повторной отправке подписываются заново.
- Отчёт macOS сверяет Agent, Guard Keeper, Safari и Siri с активным процессом launchd, версией и свежей записью здоровья. Работающий Agent больше не считается доказательством установки слоёв.
- Диагностика Windows показывает процесс, удерживающий папку установки; оборванная загрузка исходников повторяется до переключения активной версии.
- Это подписанный Windows-пакет и подписанный установщик macOS source-preview. Runtime macOS не подписан Apple и не нотарифицирован.

## X4.3.9-TARPED+20261031 — Windows fresh status delivery

### English

- Windows Agent status heartbeats now use QoS 0, matching macOS. QoS 1 could replay an old signed heartbeat after reconnect; the server rejects stale signatures and the next fresh heartbeat is authoritative.
- Command replies keep QoS 1 delivery and the bounded macOS response outbox. This change affects replaceable status telemetry only.
- macOS remains a source-preview installer, not an Apple-signed or notarized application release.

### Русский

- Статусы Windows Agent теперь отправляются с QoS 0, как на macOS. QoS 1 мог повторно доставить старый подписанный heartbeat после переподключения; сервер отклоняет его, а следующий свежий статус становится актуальным.
- Ответы на команды сохраняют QoS 1 и ограниченную очередь macOS. Изменение касается только заменяемых статусных сообщений.
- macOS остаётся source-preview установщиком, а не подписанным Apple и нотариализованным приложением.

## X4.3.8-TARPED+20261031 — MQTT delivery retry and fresh heartbeat

### English

- Agent and Guard Keeper now keep command replies in a bounded, expiring in-memory outbox while MQTT is disconnected. Replies are signed when resent, so the timestamp and nonce are fresh; missing broker acknowledgments leave the reply queued and are recorded in logs.
- Replaceable Agent and Guard Keeper status heartbeats use QoS 0 and are refreshed after reconnect. This avoids replaying an old QoS 1 signed status after a network interruption; the server continues to reject any stale signed packet.
- Guard Keeper now sends a status heartbeat every 60 seconds. `mqtt_outbox.py` is included in agent update and Guardian recovery packages.
- macOS remains a source-preview installer. No Apple code-signing/notarization credentials are available, so this release does not claim a notarized XIDER.app or compiled Safari/Siri apps.

### Русский

- Agent и Guard Keeper сохраняют ответы команд в ограниченной очереди с TTL, пока MQTT отключён. При повторной отправке ответ подписывается заново с актуальными timestamp и nonce; при отсутствии подтверждения брокера ответ остаётся в очереди, а состояние отражается в логе.
- Заменяемые heartbeat-статусы Agent и Guard Keeper отправляются с QoS 0 и обновляются после переподключения. Так старый подписанный статус QoS 1 не воспроизводится после сетевого обрыва; сервер по-прежнему отклоняет любой устаревший пакет.
- Guard Keeper отправляет статус раз в 60 секунд. `mqtt_outbox.py` входит в пакеты обновления агента и восстановления Guardian.
- macOS остаётся source-preview установщиком. Сертификата Apple и данных нотариации нет, поэтому релиз не заявляет нотариализованный XIDER.app или скомпилированные приложения Safari/Siri.

## X4.3.7-TARPED+20261031 — Windows update diagnostics and macOS source preview

### English

- Windows update failures now report the blocking process PID, name, executable path, and start time; the signed bootstrap retries interrupted GitHub archive downloads before the active version is changed.
- macOS Pages installer now selects the v4.3.7 source preview, pinned to the release commit. Its XIDER bootstrap and installer signatures are verified; the runtime is explicitly not Apple-signed or notarized.
- macOS install status reports Agent, Guard Keeper, Safari, and Siri separately with process/version and local health evidence; disabled layers are reported as skipped.
- Windows and macOS agents attach a session ID to signed status and Last Will messages, publish heartbeats at QoS 1 only while connected, and retry status on the next heartbeat.
- Telegram status timestamps now use the signed message timestamp. A delayed offline Last Will is accepted only for the currently known MQTT session.

### Русский

- При ошибке обновления Windows теперь выводятся PID, имя, путь к исполняемому файлу и время запуска процесса, удерживающего папку установки; прерванная загрузка архива GitHub повторяется до переключения активной версии.
- Установщик macOS в Pages теперь выбирает source-preview v4.3.7, закреплённый за commit релиза. Подписи XIDER-пакетов проверяются; сама runtime-сборка не имеет подписи Apple и нотариации, это явно указано.
- Отчёт установки macOS отдельно показывает Agent, Guard Keeper, Safari и Siri, их процессы/версии и локальное состояние; выключенные владельцем слои отмечаются как пропущенные.
- Windows- и macOS-агенты добавляют идентификатор сессии в подписанные статусы и Last Will, отправляют heartbeat с QoS 1 только при активном соединении и повторяют статус на следующем heartbeat.
- Бот использует timestamp из подписанного сообщения. Запоздалый offline Last Will принимается только для текущей известной MQTT-сессии.

## X4.3.4-TARPED+20261030 — Windows preview release verification

### English

- Permit the installer to verify a prerelease only when the exact release tag is pinned; automatic latest-release selection still ignores previews.
- Keep package download gated by the signed manifest and its asset hashes.

### Русский

- Разрешена проверка preview-релиза только по явно закреплённому тегу; автоматический поиск последней версии по-прежнему пропускает preview.
- Скачивание пакета по-прежнему проходит только после проверки подписанного manifest и хешей assets.

## X4.3.3-TARPED+20261030 — Windows Python bootstrap recovery

### English

- Treat a missing Python version from `py.exe` / `python.exe` as a normal probe miss under Windows PowerShell 5.1, then install the pinned official runtime.
- Keep the signed Windows Agent + Guard Keeper enrollment flow unchanged.

### Русский

- Ошибку «подходящий Python не найден» от `py.exe` / `python.exe` считаем обычным промахом поиска и продолжаем установку закреплённого Python под Windows PowerShell 5.1.
- Подписанный поток установки Agent + Guard Keeper и подключение через X-Dock сохранены.

## X4.3.2-TARPED+20261029 — X-Dock install diagnostics

### English

- Windows one-line installer now saves the elevated install transcript and prints the last 80 lines if setup fails.
- Windows executable file/product metadata now matches agent version 4.3.2.
- Windows Agent and Guard Keeper enroll through owner-approved X-Dock; VPS SSH credentials are not requested.

### Русский

- Установщик Windows сохраняет журнал elevated-установки и показывает последние 80 строк при ошибке.
- Метаданные Windows-бинарника синхронизированы с версией агента 4.3.2.
- Агент и Guard Keeper подключаются через подтверждение X-Dock; SSH-пароль VPS не нужен.

## X4.3.1-TARPED+20261029 — X-DOCK: Windows quick enrollment

### English

- Ship the current signed Windows Agent and Guard Keeper bundle from a tag-pinned source snapshot.
- Enroll a fresh Windows installation through X-Dock without asking for VPS SSH credentials.
- Keep the short PowerShell launcher pinned to the signed Windows release; macOS remains on its separately signed path.

### Русский

- Добавлен подписанный комплект Windows Agent и Guard Keeper из закреплённого снимка исходников.
- Новая установка Windows регистрируется через X-Dock без запроса SSH-данных VPS.
- Короткая команда PowerShell закрепляется за подписанным Windows-релизом; macOS пока остаётся на отдельном пути подписи.

## X4.3.0-TARPED+20261012 — X-PANE: Mac-разрешения по команде владельца

### English

- Added X-PANE: the owner can request a capability check, then open the matching
  macOS Privacy & Security pane on the selected Mac from Telegram.
- The agent accepts only six fixed permission identifiers and launches a fixed
  System Settings destination; it cannot grant a permission or open an arbitrary
  URL. The owner grants access locally, then refreshes the report.
- The settings buttons are shown only for unresolved or unverified permissions
  when the selected Mac reports support for the command. Old agents remain
  compatible and receive no unsupported command.
- Aligned bot and Windows/macOS source versions at 4.3.0.

### Русский

- Добавлен X-PANE: владелец может проверить возможности и открыть нужный раздел
  «Конфиденциальность и безопасность» macOS на выбранном Mac прямо из Telegram.
- Агент принимает только шесть фиксированных ID разрешений и открывает только
  заранее заданный раздел настроек. Он не выдаёт права и не открывает произвольные
  ссылки: доступ вручную подтверждает владелец на Mac, после чего проверку можно
  обновить.
- Кнопки появляются только для невыданных или непроверенных разрешений и только
  если выбранный Mac объявил поддержку команды. Старые агенты не получают
  неподдерживаемую команду.
- Версии исходников бота, Windows- и macOS-агентов синхронизированы на 4.3.0.

Офлайн-тесты не заменяют проверку на физическом Mac и публикацию подписанного
релиза.

## X4.2.1-TARPED+20261007 — X-LATCH: безопасное переключение агента

### English

- Added an owner-only bootstrap action to legacy-agent release details. It
  provides the latest signed one-line installer and clearly distinguishes that
  recovery step from installing the selected historical release; the command
  must be run locally on the target device.
- Added a direct install action beside each compatible agent release in
  X-LEDGER; the selected tag is still confirmed by the owner and verified by
  the agent before installation.
- Windows staged installation now waits for and, when necessary, stops only
  processes whose executable or script belongs to the managed agent directory.
  Activation and rollback use same-volume directory renames instead of
  recursive folder moves; the old checkout remains as a backup.
- Aligned bot, Windows, and macOS source/build metadata at 4.2.1.
- Documentation now distinguishes source version and release notes from
  publication, live services, and physical-device acceptance.

### Русский

- В карточке релиза старого агента добавлена доступная только владельцу кнопка
  восстановления: она показывает короткую команду последнего подписанного
  установщика для локального запуска на целевом устройстве и отдельно поясняет,
  что это не установка выбранного старого тега.
- Рядом с каждым совместимым релизом агента в X-LEDGER добавлена кнопка
  установки. Бот просит подтвердить выбранный тег, а агент сверяет подпись до
  установки.
- Windows-установщик теперь дожидается завершения процессов агента и при
  необходимости останавливает только процессы из управляемого каталога XIDER.
  Переключение и откат используют переименование папок на том же диске вместо
  рекурсивного переноса; прежняя версия сохраняется резервной копией.
- Версии исходников бота, Windows- и macOS-агентов согласованы на 4.2.1.
- Документация разделяет версию исходников и страницу релизов с публикацией,
  действующими сервисами и приёмкой на физических устройствах.

Офлайн-проверки не заменяют проверку на физическом устройстве или Telegram.

## X4.2.0-TARPED+20261007 — X-ROUTE: ссылке — точный маршрут

### English

- Added X-ROUTE: a selected Windows/macOS agent opens validated HTTP(S) URLs;
  the owner chooses 1, 2, 3, or 5 launches, and the default browser controls
  whether these become tabs or windows. Owners can keep named URL favorites.
- The Telegram handbook now renders chapter titles and content in the selected
  X-LEX voice. X-PIKMI's handbook and URL copy now use a deliberately girly,
  playful voice while retaining the same technical facts and permissions.
- Kept the X-LEDGER exact GitHub release-tag picker, explicit confirmation,
  pinned-key trust check, and signed-package verification as the only remote
  agent update route.
- Aligned bot/agent release identity at 4.2.0 and corrected Windows PE and
  macOS app bundle version metadata. Added a complete Russian README alongside
  the English project README.

### Русский

- Добавлен X-ROUTE: выбранный агент Windows/macOS открывает проверенные ссылки
  HTTP(S). Владелец выбирает 1, 2, 3 или 5 запусков; вкладками или окнами
  управляет браузер по умолчанию. Владелец может сохранять именованные ссылки.
- Справочник Telegram теперь меняет заголовки и содержимое глав вместе с
  выбранным голосом X-LEX. В X-PIKMI обновлены справка и тексты ссылок — стиль
  стал более девчачьим и игривым, но технические факты и права не меняются.
- Сохранён точный выбор GitHub-тега через X-LEDGER, подтверждение владельца,
  проверка закреплённого ключа и подписи пакета как единственный путь удалённого
  обновления агента.
- Версии бота и агентов синхронизированы на 4.2.0; исправлены метаданные PE для
  Windows и Info.plist macOS. Добавлен полный русский README к английскому.

Офлайн-тесты проверяют логику; они не являются приёмкой на физическом Mac,
Windows-устройстве или действующем VPS.

## X4.1.6-TARPED+20261007 — X-STAB: one card, one command

### English

- Serialize long-running actions by Telegram user, chat, and card so different
  buttons cannot race to overwrite the same progress/result message.
- Keep the owner-only one-line Windows/macOS installation picker in Devices;
  the commands remain copy-and-run actions, not remote installs.
- Align bot and agent release identity at 4.1.6 for the signed full release set.

### Русский

- Долгие команды теперь сериализуются по пользователю, чату и карточке Telegram:
  разные кнопки не перетирают прогресс и результат друг друга.
- В разделе «Устройства» остаётся кнопка владельца с короткой командой установки
  Windows/macOS; это копирование команды, а не удалённая установка.
- Версия бота и агентов синхронизирована на 4.1.6 для полного подписанного релиза.

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
