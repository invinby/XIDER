# XGENT-MCS — macOS agent + XIDER Guardian

## English

`XGENT-MCS` is the macOS endpoint agent for authorized personal device
management. It reports status and telemetry through the signed MQTT channel and
is controlled from the owner-only Telegram bot.

### Guardian

`XIDER Guardian` is a separate, visible `launchd` supervisor. It keeps the
recovery channel alive while the worker agent is stopped and supports status,
start, stop, restart, and opt-in automatic recovery. It writes a clear local
`guardian.log`.

An intentional worker stop is persisted as owner intent, so Guardian does not
immediately relaunch it. Automatic startup at the next login remains controlled
separately.

Guardian is not a hidden process and does not bypass macOS controls. Camera,
microphone, screen recording, and location remain permission-gated by macOS.
If the Mac is powered off or discharged, the VPS can only report its last
heartbeat and notify the owner.

### Recovery chain

The visible recovery layers are one-way: **Siri → Safari → Guard Keeper →
Agent**. Siri checks Safari's fresh health record. Safari checks both Guard
Keeper and the Agent, and asks Guard Keeper to recover the Agent when needed.
Each layer honors the owner's saved stop state; an intentional stop is not
treated as a crash. The bot reports each component's own version/state and
collects bounded, rotating local logs on request. The owner can stop or remove
each LaunchAgent from the device.

### One-command install/update

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

The bootstrap preserves the local `.env`, downloads the current source, fixes
script permissions, starts the worker, and registers the recovery chain. Do not paste the
shell prompt or surrounding backticks into Terminal.
This convenience bootstrap follows the moving `main` branch and does not verify
a signature before execution; this direct command is for development installs.
A tagged release quickstart verifies an OpenSSH signature over the bootstrap
digest, platform, release tag, and full commit before execution, then pins the
source to that commit. The quickstart itself is not independently signed before
it runs and still relies on the initial HTTPS/GitHub download.

### Manual status

```bash
cd "$HOME/XIDER/git-ver/XGENT-MCS"
bash ./start_agent.sh --status
bash ./start_guardian.sh --status
bash ./start_safari.sh --status
bash ./start_siri.sh --status
```

Stop Safari or Siri locally with the matching script's `--stop` option. Stopping
Guard Keeper with `bash ./start_guardian.sh --stop` records the owner's intent;
Safari will not relaunch it until the owner starts it again from Telegram.

Open **Device → Power & Protection → Guardian** in Telegram for status, start,
stop, restart, and automatic recovery.

## Русский

`XGENT-MCS` — агент macOS для управления собственными устройствами через
подписанный MQTT-канал и Telegram-бот владельца.

`XIDER Guardian` — отдельный видимый supervisor через `launchd`. Он оставляет
канал восстановления доступным, когда рабочий агент остановлен, и умеет
показывать статус, запускать, останавливать и перезапускать агент, а также
включать или отключать автовосстановление.

Намеренная остановка агента записывается как состояние владельца: Guardian не
поднимает его тут же повторно. При следующем входе агент запустится только если
автозапуск оставлен включённым.

Guardian не скрывается в системе и не обходит контроль macOS. Камера,
микрофон, запись экрана и геолокация доступны только после разрешения
пользователя. Если Mac выключен или разряжен, VPS покажет последний heartbeat,
но не сможет включить ноутбук программно.

### Цепочка восстановления

Видимая цепочка работает в одну сторону: **Siri → Safari → Guard Keeper →
Agent**. Siri проверяет свежий статус Safari. Safari следит отдельно за Guard
Keeper и агентом и просит Keeper восстановить агента, если тот упал. Каждый
уровень уважает сохранённую команду владельца на остановку и не принимает её
за сбой. Бот показывает собственные версии и состояния компонентов, а журналы
хранятся с ограничением размера и передаются только по запросу. Владелец может
остановить или удалить каждый LaunchAgent с самого Mac.

### Установка или обновление одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

Bootstrap сохраняет локальный `.env`, скачивает свежий код, исправляет права
скриптов, запускает агент и регистрирует цепочку восстановления.
Эта прямая команда для разработки: она берёт изменяемую ветку `main` и не
проверяет подпись bootstrap до запуска. Tagged release quickstart закрепляет
скрипт и архив на одном полном commit ID и проверяет OpenSSH-подпись хэша,
платформы, tag и commit. Подпись quickstart отдельно до запуска не
проверяется, поэтому начальная загрузка всё ещё опирается на HTTPS/GitHub.
До публикации успешного подписанного релиза не считай release-команду готовой.

Открой в Telegram: **Устройство → Питание & Защита → Guardian**.
