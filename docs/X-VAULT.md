# X-VAULT

X-VAULT creates an authenticated encrypted snapshot of an XIDER source tree
and optional external files. The entire archive—including `.env` files and
the manifest—is encrypted with AES-256-GCM. A key is derived from
`XIDER_VAULT_PASSPHRASE` using Scrypt. The passphrase is never accepted as a
command-line option or written into the archive.

This is an implementation of the X-VAULT component named in `TARPED.md`; it
does not yet sync to the second VPS or prove disaster recovery. The archive is
created locally on whichever host runs the command. Until a backup is copied
to the second VPS and a restore rehearsal passes there, it is only a local
snapshot.

The archive excludes virtual environments, Git internals, Python caches, and
the `incoming` update directory. Symlinks are skipped. Optional files are
stored as `external/<filename>` inside the encrypted payload. Do not include
two files with the same basename.

Each regular file is checked for changes while it is read. This is not a
transactional, whole-tree snapshot: do not run it during a release deployment.
If a future release moves mutable state to SQLite, add an online SQLite backup
step before treating the result as recovery-ready.

## Create and verify a backup

Set `XIDER_VAULT_PASSPHRASE` from a password manager or another secure secret
store (minimum 16 characters), then run. Keep it separately from both servers;
losing it makes these backups unrecoverable.

```sh
python3 ops/x_vault.py backup \
  --source /opt/xider \
  --include-file /etc/xider/bot.env \
  --include-file /etc/systemd/system/xider-bot.service \
  --vault /var/backups/xider-vault
```

The output path is unique; it does not overwrite an earlier backup. Verify a
saved archive with:

```sh
python3 ops/x_vault.py verify --archive /var/backups/xider-vault/xvault-....xvlt
```

Verification checks the passphrase and the authenticated archive contents; it
does not change the running bot.

## Restore rehearsal

Restore only into a new or empty staging directory:

```sh
python3 ops/x_vault.py restore-stage \
  --archive /var/backups/xider-vault/xvault-....xvlt \
  --stage /tmp/xider-vault-restore-check
```

This command never writes over `/opt/xider`, `/etc/xider`, or a non-empty
staging directory. Inspect and test the staged files before any separately
approved production restore. A wrong or lost passphrase makes the backup
unrecoverable, so keep it outside both VPS instances.

The tool currently creates, verifies, and stage-restores local archives.
Copying backups to the second VPS, automatic retention, and a full failover
rehearsal are separate steps and are not claimed as complete by this
implementation.

## Current verification

The local X-VAULT tests pass on Windows. A Linux smoke test also passed on the
second VPS using a disposable sample file: create, authenticate/verify, restore
to staging, compare, and cleanup. No production source, `.env`, or user state
was copied during that smoke test.
