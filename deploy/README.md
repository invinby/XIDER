# XIDER server deployment

The paid `xider` VPS is the bot host. The `invinby` Always Free VPS stays available as a standby/monitoring host. The current runtime uses the existing TLS EMQX broker; no plaintext MQTT listener is opened on either VPS.

## Windows bootstrap and VPS updates

Короткая команда для скачивания и запуска bootstrap:

```powershell
irm https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.ps1 | iex
```

Bootstrap требует существующие локальные `TG-BOT-SERVER\.env` и
`XGENT-WDS\.env` в checkout XIDER (или `XIDER_ENV_ROOT`, указывающий на его
родительский каталог). Значения секретов не печатаются.

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
supported. A GitHub push is not required for checkout-based deployment, but
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

## macOS quick install

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

For a test branch, set `XIDER_BRANCH` before running the same installer. The installer stages the archive, preserves the local agent `.env`, keeps the previous install for rollback, and pins agent self-updates to that branch:

```bash
XIDER_BRANCH='branch-name' bash -c "$(curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/branch-name/deploy/bootstrap.sh)"
```

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

`rotate-runtime-secrets.sh` rotates `BOT_TOKEN`, `SHARED_KEY`, or `MQTT_PASSWORD` from environment variables, backs up `/etc/xider/bot.env`, restarts the service, and restores the backup on failure. It never prints secret values. Keep private keys outside the source tree; deployment temporary files are removed in the PowerShell `finally` block.
