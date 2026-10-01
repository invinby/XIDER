# XIDER server deployment

The paid `xider` VPS is the bot host. The `invinby` Always Free VPS stays available as a standby/monitoring host. The current runtime uses the existing TLS EMQX broker; no plaintext MQTT listener is opened on either VPS.

## Windows bootstrap and VPS updates

Короткая команда для скачивания и запуска bootstrap:

```powershell
irm https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.ps1 | iex
```

Эта прямая команда использует подвижную ветку `main` и предназначена для
разработки. В GitHub Release планируется прикладывать `XIDER-QUICKSTART.txt`
с командами, закреплёнными на полном commit ID релиза; для обеих платформ
bootstrap и исходный архив тогда запрашиваются с одного commit, а не с ветки.
Команды quickstart проверяют SHA-256 скачанного bootstrap до исполнения и
закрепляют архив на том же commit. GitHub Release включает bootstrap-скрипты и
quickstart в подписанный manifest, чтобы их байты можно было проверить вместе
с остальными assets. Но сама one-line команда подпись manifest до запуска не
проверяет; первый шаг всё ещё доверяет HTTPS и GitHub. Подписанная проверка в
самом bootstrap остаётся отдельным release gate.

Bootstrap не переносит `TG-BOT-SERVER\.env` и BOT_TOKEN на Windows. В полном
сценарии `bootstrap.ps1` → `setup-all.ps1` для чтения конфигурации агента,
деплоя бота и возможного отката используется SSH-ключ: по умолчанию
`%USERPROFILE%\.ssh\xider`, либо путь из `$env:XIDER_SSH_KEY`. Параметр
`-KeyPath` переопределяет оба варианта. Это не парольный fallback: uploader
работает с явно выбранным ключом в non-interactive SSH-режиме. Host key сервера
должен уже быть проверен и закреплён в `%USERPROFILE%\.ssh\known_hosts`;
используется `StrictHostKeyChecking=yes` (`-KnownHostsPath` или
`$env:XIDER_SSH_KNOWN_HOSTS` задаёт другой файл). Первое автоматическое
подтверждение неизвестного SSH-хоста отключено. Отдельный
`bootstrap-agent.ps1` для установки только агента использует штатный порядок
OpenSSH с ключом/agent и интерактивным fallback.
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

Uploader sends a signed source bundle and release manifest over SSH; it never
replaces `/etc/xider/bot.env` and never uploads the private signing key. The VPS
must already have an active `xider-bot.service`. Over that same authenticated
SSH channel, the owner uploader seeds the root-owned updater, verifier, pinned
key module, safe extractor, and narrow command wrapper. The server verifies the
manifest signature and archive digest before backup, extraction, or service
changes. It then backs up current source and the systemd unit, validates archive
paths/symlinks/size, compiles Python, and checks the restarted service is active
with the same `MainPID` for three consecutive checks; failure restores source
and unit. This process-level check does not prove Telegram or MQTT round-trip
health. Dependency changes are rejected until their upgrade/rollback path is
supported. The complete Windows setup records the exact server backup path; if
subsequent local Agent/Guardian registration fails, it asks the updater to
restore that same snapshot. Manual `rollback` without a path still selects the
newest snapshot; a supplied path is checked to remain under
`/var/backups/xider/`. A GitHub push is not required for checkout-based
deployment. The ordinary short bootstrap still downloads a moving branch; the
release quickstart pins it to a full commit ID, but neither path verifies the
bootstrap with the publisher key.

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

The direct installer also performs a read-only config preflight before changing
ACLs or Scheduled Tasks: it requires TLS, payload encryption, broker credentials,
a valid topic prefix and port, and rejects duplicate security settings.

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
the archive, TLS, payload-encryption setting, ACL credentials, topic prefix, and
port without printing their values. The macOS bootstrap applies the same
fail-closed configuration checks. `-SourceArchive` accepts a local ZIP for
offline tests; normal installation still downloads the selected GitHub branch.
An existing installed `.env` takes precedence over older Desktop copies.

## macOS quick install

```bash
curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/main/deploy/bootstrap.sh | bash
```

For a test branch, set `XIDER_BRANCH` before running the same installer. The installer stages the archive and preserves the local agent `.env` and previous install for rollback. `XIDER_REF` can instead pin the archive to a full 40- or 64-character commit ID. The branch selector affects this bootstrap archive only; agent self-updates use the release protocol:

```bash
XIDER_BRANCH='branch-name' bash -c "$(curl -fsSL https://raw.githubusercontent.com/invinby/XIDER/branch-name/deploy/bootstrap.sh)"
```

The command above fetches the bootstrap itself from a moving GitHub branch.
Release `XIDER-QUICKSTART.txt` commands pin both bootstrap and archive to the
same full commit ID, but this is an HTTPS/GitHub trust pin, not verification by
the release signing key. Do not treat either path as a signed bootstrap until
the bootstrap verifier and pinned trust anchor are independently tested.

`XIDER_SOURCE_ARCHIVE=/path/to/xider.zip` may be set for an offline test.
The installer still validates the expected agent files and exercises the same
activation/rollback path. A failed activation restores the prior checkout and
removes the copied `.env` from the quarantined failed directory. The automated
fixture uses a stub `launchctl`; only a real Mac can validate permissions,
TCC prompts, sleep/wake, and actual LaunchAgent behavior.

The macOS bootstrap downloads the repository and starts the agent. If a local
sidecar `.env` is absent, it connects to the configured XIDER VPS
(`XIDER_SERVER_HOST`, default `141.145.152.174`) over SSH. OpenSSH tries its
normal key/agent authentication first and keeps interactive password fallback;
the bootstrap also recognizes `~/.ssh/xider`, or an explicit `XIDER_SSH_KEY`.
It retrieves only the agent configuration keys from
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

`update-server.sh` performs signature verification → backup → safe extraction/compile → install → restart → health check; a failed health check restores the backup. It refuses an absent manifest, an unsigned bundle, or a bundle whose size/hash differs from the signed inventory. The Server panel never downloads arbitrary URLs.

The release verifier, pinned key ring, archive extractor, updater, and narrow
`xider-server-ops` wrapper are installed root-owned under
`/usr/local/libexec/xider` and `/usr/local/sbin`. The bot account cannot edit
the code it invokes through sudo. `server-install.sh` provisions this layout;
the Windows owner uploader also bootstraps the helper files over the existing
authenticated SSH connection before it sends a signed package. The first SSH
bootstrap is the trust handoff; later bot-triggered updates require the
Ed25519 manifest. `upload-and-install.ps1` signs the local bundle with the
owner-held key under `%LOCALAPPDATA%\XIDER\release-signing` (or the explicit
`-SigningKeyPath`) and never uploads or prints the private key. A missing,
unpinned, or repository-local key fails before source is uploaded.

The public-key verifier uses the OS `python3-cryptography` package and pins the
same release key as the agent verifier. Do not change the pinned key by editing
the writable checkout: rotate it only through a package signed by the currently
trusted key, or an explicit owner-authenticated SSH bootstrap. A successful
update consumes the staged archive and manifest; rollback restores a server
backup and does not need a release signing key.

The release manifest records exact asset sizes and SHA-256 values and is now
required to carry an Ed25519 publisher signature over the canonical inventory.
The macOS source-agent updater checks the signature against its pinned key ring,
then checks the selected source archive's size and SHA-256 before extraction.
The Windows source agent imports the shared verifier, and the PyInstaller spec
includes its module path. The Windows EXE was rebuilt locally on 2026-10-01 with
Python 3.12.10 and PyInstaller 6.22.2; archive inspection confirms
`release_signature`, Ed25519, and `config` are packaged. The Windows build
workflow now fails if either module is absent from the EXE. The executable was
not launched. Its remote updater remains disabled until the Windows transaction
installer is complete.
The current local source pins release key ID `521c56c0c89f0ddd`; the matching
private key is stored outside the checkout and is never included in source,
archives, or either VPS. The protected GitHub environment secret and a signed
release have not been configured, so no installable signed release exists yet.
The vault-recovery and SSH keys must not be reused for release signing.
Before replacing files, the macOS updater now flushes a backup and durable
transaction journal. Each file replacement is atomic; a caught error rolls back
immediately, and an interrupted swap is restored before the compatible agent
loads its config. The new process gets one health attempt: if it reaches runtime
but fails to connect to MQTT within 120 seconds it restores the prior files; if
it crashes during import, the next supervisor-driven start rolls back before
loading config. No rollback can execute until the OS starts a process again.
This is still not an A/B slot: a mixed tree can exist until recovery runs.
The GitHub release job now requires `XIDER_RELEASE_PRIVATE_KEY_B64`, checks that
the derived key ID is present in the pinned public-key ring, and targets the
`xider-release-signing` environment with contents-write permission limited to
that job. The environment's reviewer rules and secret have not been configured
or independently verified. The PyInstaller macOS bundle and Windows agent
updater remain fail-closed until their own transaction installers exist; the
source updater refuses to mutate a frozen bundle. Direct short bootstraps still
default to a moving branch. A release quickstart can pin bootstrap and source to
one full commit, but that installation path is not authenticated by the pinned
release key. macOS post-restart health
confirmation and automatic rollback cover this source-agent path only; they
have not been tested on a physical Mac.

`rotate-runtime-secrets.sh` rotates `BOT_TOKEN`, `SHARED_KEY`, or `MQTT_PASSWORD` from environment variables, backs up `/etc/xider/bot.env`, restarts the service, and restores the backup on failure. It never prints secret values. Keep private keys outside the source tree; deployment temporary files are removed in the PowerShell `finally` block.

## Scheduled X-VAULT backup

The encrypted primary-to-standby sender, bounded forced-command SSH receiver,
and systemd timers are prepared but have not been installed on either VPS. Read
[`docs/X-VAULT.md`](../docs/X-VAULT.md) before setup: the recovery private key
must remain owner-held, the 1-GiB per-upload and 2-GiB queue limits need to be
checked against actual backup size, and a copied archive must be verified
offline before it is treated as recoverable. Timer failures currently go to
the systemd journal; Telegram alerting and a live SSH/sshd rehearsal are still
outstanding.
