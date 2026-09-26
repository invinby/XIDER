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

Guardian is not a hidden process and does not bypass macOS controls. Camera,
microphone, screen recording, and location remain permission-gated by macOS.
If the Mac is powered off or discharged, the VPS can only report its last
heartbeat and notify the owner.

### One-command install/update

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

The bootstrap preserves the local `.env`, downloads the current source, fixes
script permissions, starts the worker, and registers Guardian. Do not paste the
shell prompt or surrounding backticks into Terminal.

### Manual status

```bash
cd "$HOME/XIDER/git-ver/XGENT-MCS"
bash ./start_agent.sh --status
bash ./start_guardian.sh --status
```

Open **Device → Power & Protection → Guardian** in Telegram for status, start,
stop, restart, and automatic recovery.

## Русский

`XGENT-MCS` — агент macOS для управления собственными устройствами через
подписанный MQTT-канал и Telegram-бот владельца.

`XIDER Guardian` — отдельный видимый supervisor через `launchd`. Он оставляет
канал восстановления доступным, когда рабочий агент остановлен, и умеет
показывать статус, запускать, останавливать и перезапускать агент, а также
включать или отключать автовосстановление.

Guardian не скрывается в системе и не обходит контроль macOS. Камера,
микрофон, запись экрана и геолокация доступны только после разрешения
пользователя. Если Mac выключен или разряжен, VPS покажет последний heartbeat,
но не сможет включить ноутбук программно.

### Установка или обновление одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

Bootstrap сохраняет локальный `.env`, скачивает свежий код, исправляет права
скриптов, запускает агент и регистрирует Guardian.

Открой в Telegram: **Устройство → Питание & Защита → Guardian**.
