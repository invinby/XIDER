# XIDER server deployment

The paid `xider` VPS is the bot host. The `invinby` Always Free VPS stays available as a standby/monitoring host. The current runtime uses the existing TLS EMQX broker; no plaintext MQTT listener is opened on either VPS.

## One-time install from Windows

Короткая команда для скачивания и запуска bootstrap:

```powershell
irm https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.ps1 | iex
```

Если `.env` лежит не в каталоге XIDER, добавь `-EnvRoot` при обычном запуске
скрипта. Bootstrap не печатает значения секретов.

Run from the repository checkout after fixing the private-key ACL:

```powershell
cd C:\Users\rog\Desktop\XIDER\git-ver
icacls "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key" /inheritance:r
icacls "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key" /grant:r "$($env:USERNAME):R"
powershell -ExecutionPolicy Bypass -File .\deploy\upload-and-install.ps1
```

The script uploads the ignored `TG-BOT-SERVER\.env` and the current `git-ver` source without printing secrets, creates `/etc/xider/bot.env` with restricted permissions, installs dependencies, and enables `xider-bot.service`. It refuses to start unless MQTT TLS and payload encryption are enabled. A GitHub push is not required for this path.

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

The installer registers a hidden per-user Scheduled Task named `XIDER Agent`, starts it immediately, and keeps the `.env` readable only by the current Windows account. `start_agent.bat` starts the task; `stop_agent.bat` stops it.

For the complete local flow (VPS bot + Windows build + hidden agent), run `deploy\setup-all.ps1`. It uses the ignored root `XGENT-WDS\.env` as the sidecar source and never prints its values.

## macOS quick install

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

The macOS bootstrap downloads the repository, copies the sidecar `.env` from
`XIDER_ENV_ROOT` (or `~/XIDER`) and starts the agent. It does not deploy the
VPS bot; that remains the Windows/server deployment flow.

## Updates, rollback, and secret rotation

`update-server.sh` performs backup → package validation/compile → install → restart → health check; a failed health check restores the backup. For an owner-approved update, place a trusted source archive at `/opt/xider/incoming/xider-source.zip` and use the Server panel. The panel never downloads arbitrary URLs and refuses an absent package.

`rotate-runtime-secrets.sh` rotates `BOT_TOKEN`, `SHARED_KEY`, or `MQTT_PASSWORD` from environment variables, backs up `/etc/xider/bot.env`, restarts the service, and restores the backup on failure. It never prints secret values. Keep private keys outside the source tree; deployment temporary files are removed in the PowerShell `finally` block.
