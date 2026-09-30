# XIDER server deployment

The paid `xider` VPS is the bot host. The `invinby` Always Free VPS stays available as a standby/monitoring host. The current runtime uses the existing TLS EMQX broker; no plaintext MQTT listener is opened on either VPS.

## Windows bootstrap and VPS updates

Короткая команда для скачивания и запуска bootstrap:

```powershell
irm https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.ps1 | iex
```

Bootstrap не переносит `TG-BOT-SERVER\.env` и BOT_TOKEN на Windows. Для агента
он использует `XGENT-WDS\.env` из `XIDER_ENV_ROOT`, если он есть; иначе
`setup-all.ps1` получает только настройки агента с активного VPS по SSH-ключу
`%USERPROFILE%\.ssh\xider` (путь можно переопределить параметром `-KeyPath`).
Настройки остаются в защищённом sidecar `.env`; секреты не печатаются и не
включаются в EXE. Перед SSH-записью локальный preflight требует MQTT TLS,
шифрование payload и непустые broker credentials, затем собирает EXE. Если
preflight, сборка или проверка наличия Windows Task Scheduler/Guardian не
проходят, uploader не вызывается и VPS не меняется. Проверка установки
Guardian выполняется в режиме без изменений: ACL, задачи и процессы не
трогаются. Установленные Agent и Guardian регистрируются как видимые задачи
Task Scheduler; скрытого режима установки нет.

При обновлении bootstrap сначала сохраняет конфигурацию и старый checkout.
Если общая установка не проходит, новая папка изолируется без её копии `.env`
и прежняя версия возвращается. Первый неудачный запуск не выдаётся за готовую
установку. Этот путь проверяется локальным фикстурным ZIP в PowerShell 5 и 7;
проверка не равна установке на реальном ноутбуке.

Для обновления уже установленного бота из checkout:

```powershell
cd C:\Users\rog\Desktop\XIDER\git-ver
powershell -ExecutionPolicy Bypass -File .\deploy\upload-and-install.ps1
```

Uploader sends source only and never replaces `/etc/xider/bot.env`. The VPS
must already have an active `xider-bot.service`. It transfers the updater and
bounded ZIP extractor alongside the source bundle, so the first run does not
depend on an older updater already installed on the VPS. The updater backs up
current source and the systemd unit, validates archive paths/symlinks/size,
compiles Python, then checks the restarted service is active with the same
`MainPID` for three consecutive checks; failure restores the source and unit.
This process-level check does not prove Telegram or MQTT round-trip health.
Dependency changes are rejected until their upgrade/rollback path is
supported. The complete Windows setup records the exact server backup path; if
subsequent local Agent/Guardian registration fails, it asks the updater to
restore that same snapshot. Manual `rollback` without a path still selects the
newest snapshot; a supplied path is checked to remain under
`/var/backups/xider/`. A GitHub push is not required for checkout-based deployment, but
the short bootstrap downloads only a branch already published on GitHub.

The updater smoke test runs against temporary directories and mocked
`systemctl`; it verifies successful update, health-check rollback, `.env`
preservation, and path-traversal rejection. This is not a live VPS, Telegram,
MQTT, or full application health check.

## Verify on the VPS

```bash
sudo systemctl status xider-bot --no-pager
sudo journalctl -u xider-bot -n 80 --no-pager
sudo systemctl restart xider-bot
```

The bot process runs headless under the dedicated `xider` user and restarts after a crash or reboot. Secrets stay in `/etc/xider/bot.env`; do not commit them or embed them in an EXE.

The owner's Server panel also provides VPS load snapshots, a PNG history chart,
recent logs, and a small allow-list of diagnostic commands (`uptime`, `memory`,
`disk`, `processes`, `service`, `logs`). The installer deploys the root helper
`xider-server-ops` through a narrow sudoers rule; Telegram never receives a
free-form root shell or any secret values.

## Windows agent background install

Put `XGENT-WDS.exe` and a filled sidecar `.env` in one folder, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_agent.ps1
```

The installer registers a per-user Scheduled Task named `XIDER Agent`, starts it immediately, and keeps the `.env` readable only by the current Windows account. The task is visible in Task Scheduler; `start_agent.bat` starts it and `stop_agent.bat` stops it.

For the complete local flow (VPS bot + Windows build + visible agent), run `deploy\setup-all.ps1`. It uses the ignored root `XGENT-WDS\.env` as the sidecar source and never prints its values.

The agent-only Windows bootstrap installs its downloaded Python source explicitly,
even when an older `XGENT-WDS.exe` remains in the existing checkout. A direct
`install_agent.ps1` run keeps its existing executable-first behavior unless
`-PreferPython` is specified.
It now prepares the downloaded source, `.env` copy, virtual environment, and
dependencies in a separate directory. Only after those checks does it stop
the old Scheduled Tasks, switch the checkout, register the new tasks, and
verify both are running from the new Python paths. Failure restores the old
checkout and task definitions/running state where possible; a failed new
checkout is retained without its copied `.env`. The previous checkout is kept
as a recoverable backup after success. This verifies local process state, not
Telegram/MQTT round-trip or Windows hardware functions.

For an agent-only validation without changing the installed files or Scheduled
Tasks, run `deploy\bootstrap-agent.ps1 -PreflightOnly` from a checkout. It checks
the downloaded archive and the required agent configuration keys without
printing their values. `-SourceArchive` accepts a local ZIP for offline tests;
normal installation still downloads the selected GitHub branch. An existing
installed `.env` takes precedence over older Desktop copies.

## macOS quick install

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

For a test branch, set `XIDER_BRANCH` before running the same installer. The installer stages the archive, preserves the local agent `.env`, keeps the previous install for rollback, and pins agent self-updates to that branch:

```bash
XIDER_BRANCH='branch-name' bash -c "$(curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/branch-name/deploy/bootstrap.sh)"
```

`XIDER_SOURCE_ARCHIVE=/path/to/xider.zip` may be set for an offline test.
The installer still validates the expected agent files and exercises the same
activation/rollback path. A failed activation restores the prior checkout and
removes the copied `.env` from the quarantined failed directory. The automated
fixture uses a stub `launchctl`; only a real Mac can validate permissions,
TCC prompts, sleep/wake, and actual LaunchAgent behavior.

The macOS bootstrap downloads the repository and starts the agent. If a local
sidecar `.env` is absent, it logs in to the configured XIDER VPS
(`XIDER_SERVER_HOST`, default `141.145.152.174`) over SSH, asks for the VPS
password, and retrieves only the agent configuration keys from
`/etc/xider/bot.env`; `BOT_TOKEN` is never copied. The generated local file is
mode `0600`. Set `XIDER_ENV_ROOT` to use an existing local `.env`, or set
`XIDER_SERVER_HOST`/`XIDER_SERVER_USER` for another VPS. This does not deploy
the VPS bot; that remains the Windows/server deployment flow. The bootstrap also
registers the visible macOS `XIDER Guardian` LaunchAgent.

### XIDER Guardian

Guardian is a transparent macOS supervisor. It keeps a small control channel
available while the worker is stopped and exposes status, start, stop, restart,
and opt-in recovery through Telegram. It does not hide itself or grant privacy
permissions. If the Mac is powered off, the VPS reports the last heartbeat.

## Updates, rollback, and secret rotation

`update-server.sh` performs backup → package validation/compile → install → restart → health check; a failed health check restores the backup. For an owner-approved update, place a trusted source archive at `/opt/xider/incoming/xider-source.zip` and use the Server panel. The panel never downloads arbitrary URLs and refuses an absent package.

Tagged GitHub Releases also include `XIDER-source.zip`; `release-manifest.json`
records its exact size and SHA-256 alongside the platform packages. The manifest
is an integrity inventory, not a digital signature. Current branch-based
bootstraps and agent self-updaters do not consume this asset yet; do not treat
the existence of the asset or hash as authenticated installation.

`rotate-runtime-secrets.sh` rotates `BOT_TOKEN`, `SHARED_KEY`, or `MQTT_PASSWORD` from environment variables, backs up `/etc/xider/bot.env`, restarts the service, and restores the backup on failure. It never prints secret values. Keep private keys outside the source tree; deployment temporary files are removed in the PowerShell `finally` block.
