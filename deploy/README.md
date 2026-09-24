# XIDER server deployment

The paid `xider` VPS is the bot host. The `invinby` Always Free VPS stays available as a standby/monitoring host. The current runtime uses the existing TLS EMQX broker; no plaintext MQTT listener is opened on either VPS.

## One-time install from Windows

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
