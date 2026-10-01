# X-VAULT

X-VAULT creates an authenticated encrypted snapshot of an XIDER source tree
and optional external files. The entire archive—including `.env` files and
the manifest—is encrypted with AES-256-GCM. Existing `X-VAULT/1` archives use
a key derived from `XIDER_VAULT_PASSPHRASE` using Scrypt. New unattended
backups should use `X-VAULT/2` recipient encryption: an ephemeral X25519 key
and HKDF-SHA256 derive the AES-256-GCM key from a public recipient key. The
backup host needs only the public key; the private recovery key is never
required for backup creation.

This is an implementation of the X-VAULT component named in `TARPED.md`. A
manual production snapshot has been copied to the standby VPS and successfully
authenticated there. This proves that this archive can be decrypted and its
payload validated; it is not a full service failover or a scheduled backup
system.

The archive excludes virtual environments, Git internals, Python caches, and
the `incoming` update directory. Symlinks are skipped. Optional files are
stored as `external/<filename>` inside the encrypted payload. Do not include
two files with the same basename.

Each regular file is checked for changes while it is read. This is not a
transactional, whole-tree snapshot: do not run it during a release deployment.
If a future release moves mutable state to SQLite, add an online SQLite backup
step before treating the result as recovery-ready.

## Unattended backup encryption without a server-side secret

Generate the recovery pair on an owner-controlled computer, not on either
VPS, and choose a new protected directory outside the repository checkout:

```sh
python ops/x_vault.py keygen --directory /path/to/owner-only/xider-vault-key
```

The command prints paths and a public-key fingerprint, never private-key
contents. On Windows it removes inherited access and grants the current
account plus `SYSTEM` and local Administrators; on POSIX it uses mode `0700`
for the directory and `0600` for the private file. Store the private PEM on
encrypted owner-controlled storage and keep an independent recovery copy. Copy
only `vault-recipient-public.pem` to the primary server, for example
`/etc/xider/vault-recipient-public.pem` with mode `0644`. The fingerprint can
be compared out-of-band. Losing the private key makes `X-VAULT/2` archives
unrecoverable.

Create a new encrypted backup on the primary without putting a decrypting key
or passphrase there:

```sh
python3 ops/x_vault.py backup \
  --source /opt/xider \
  --include-file /etc/xider/bot.env \
  --include-file /etc/systemd/system/xider-bot.service \
  --vault /var/backups/xider-vault \
  --recipient-public-key /etc/xider/vault-recipient-public.pem
```

Only the public key is needed for this operation. To authenticate or stage a
restore, use the private-key file on the owner-controlled computer:

```sh
python ops/x_vault.py verify \
  --archive /path/to/xvault-....xvlt \
  --private-key /path/to/owner-only/xider-vault-key/vault-recipient-private.pem
```

The archive is authenticated; a wrong key or modified bytes are rejected. The
private-key PEM is currently unencrypted PKCS#8, so the protected directory,
encrypted owner storage, and a separately tested copy matter.

## Prepared two-server transfer (not deployed)

The repository includes a primary sender and a bounded standby receiver. The
primary timer creates an X-VAULT/2 archive, sends it as an 8-byte length-prefixed
SSH stream using pinned host keys and a dedicated upload identity, then removes
its temporary local copy. The standby account has no interactive shell or
forwarding: `sshd` forces one receiver command, and the public key is restricted
to that same command. The receiver accepts at most one 1-GiB archive per
transfer, allows at most 2 GiB queued in `/incoming`, rejects a declared
oversize before creating a file, and enforces a one-hour transfer deadline.
It accepts no client paths or shell commands. The receiver program is installed
in a separate root-owned directory readable only by the uploader's group.

A shared root-owned lock serializes receivers with the root promotion timer.
The promoter copies completed uploads into a separate root-only archive
directory without following symlinks, rejects oversized files, keeps at most
10 GiB of archives by default, and applies the 60-day retention policy. These
archive caps can be changed in the root-owned standby environment file. The
incoming queue bound is enforced before writing and does not depend on a
separate quota-backed filesystem.

Before setup, prepare these files out-of-band:

- On the owner-controlled computer, generate the X-VAULT recipient key pair;
  only `vault-recipient-public.pem` goes to the primary. Keep the private key
  on encrypted owner-controlled storage, not either VPS.
- Generate a separate, unencrypted SSH key pair for unattended restricted SSH
  upload.
  Install only its public key on the standby and the private key on the
  primary as `/etc/xider/vault-upload-key` with mode `0600`.
- Pin the standby's verified SSH host key in
  `/etc/xider/vault-known-hosts`; do not discover and trust a key inside the
  unattended backup job.
- Create `/etc/xider/x-vault-backup.env` as root-owned mode `0600`, containing
  exactly one `XIDER_VAULT_TARGET=xvault-upload@standby-host` and
  `XIDER_VAULT_REMOTE_DIR=/incoming` assignment.

The setup entry points are `deploy/xider-vault-primary-setup.sh` on the
primary and `deploy/xider-vault-standby-setup.sh /path/to/upload-key.pub` on
the standby. They install root-owned helpers and systemd timers, and the
standby script validates the effective SSH policy before reloading sshd. They
have not been run against either production VPS in this local preparation.
The sender timer is scheduled daily at 03:17 UTC; enabling it does not mean a
backup has already run. Failures currently appear in systemd journal only; a
Telegram health alert and live SSH/sshd integration test remain future work.
The standby checks the X-VAULT/2 header and size, not the encryption tag,
because its decrypting key must remain offline. Always run `verify` with the
owner-held private key before treating an uploaded archive as recoverable.
The receiver bounds each transfer and the total inbox before it writes, so the
previous unbounded-inbox concern is addressed in code and Linux fixtures. This
does not prove production disk headroom or live `sshd` behavior: setup has not
been run against the standby VPS, and the one-hour transfer deadline must be
checked against actual backup size and link speed before enabling the timer.

## Legacy passphrase backup and verification

For backward compatibility, passphrase mode remains available. Set
`XIDER_VAULT_PASSPHRASE` from a password manager or another secure secret store
(minimum 16 characters), then run. Keep it separately from both servers;
losing it makes these archives unrecoverable. For scheduled server backups,
prefer recipient encryption so the decrypting secret is not on the source VPS.

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

Verification checks the passphrase and authenticated archive contents; it does
not change the running bot. For recipient-encrypted archives, pass
`--private-key /offline/path/vault-recipient-private.pem` instead.

For an SSH automation, use `--passphrase-stdin` and send the key through the
process's standard input. This keeps it out of shell history and command-line
arguments. Do not enable shell tracing or print the input stream.

## Restore rehearsal

Restore only into a new or empty staging directory:

```sh
python3 ops/x_vault.py restore-stage \
  --archive /var/backups/xider-vault/xvault-....xvlt \
  --stage /tmp/xider-vault-restore-check
```

This command never writes over `/opt/xider`, `/etc/xider`, or a non-empty
staging directory. Inspect and test the staged files before any separately
approved production restore. For recipient-encrypted archives, supply
`--private-key`; keep that file outside both VPS instances.

The tool creates, verifies, and stage-restores archives. The first production
archive is stored on the primary and standby VPS with `root:root` ownership and
mode `0600`; matching SHA-256 values and a full authenticated archive check on
the standby were confirmed. A third encrypted copy is kept under
`%LOCALAPPDATA%\XIDER\backups`. The recovery key is not on either server or in
the repository and must be saved separately by the owner.

The production archive described above was a one-time manual copy. Recipient
encryption removes the need to store its decryption secret on the source host,
but scheduled transfer to the standby, retention, alerting, and a full service
failover rehearsal remain separate work and are not claimed as complete.

## Current verification

The X-VAULT test module passes on Windows (**13 passed, 1 skipped**), including
recipient key generation, CLI backup/verify/stage-restore, wrong-key rejection,
encryption-at-rest checks, and private-key output/checkout guards. A Linux
smoke test also passed on the second VPS using a disposable sample file: create,
authenticate/verify, restore to staging, compare, and cleanup. No production
source, `.env`, or user state was copied during that smoke test. Recipient-key
encryption has not yet been transferred to or verified on the live standby VPS.
