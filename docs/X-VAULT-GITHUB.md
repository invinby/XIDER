# Encrypted VPS snapshots in the private GitHub repository

The `xider-vault-github-backup.timer` runs once per day. It creates an
X-VAULT/2 recipient-encrypted archive on the VPS and pushes it to the dedicated
`xider-vault-backups` branch of `invinby/XIDER-PRIVATE-BACKUP`. The branch is
separate from the source mirror on `main`; it is rewritten as one snapshot
commit and retains the newest 14 archives.

The encrypted archive contains `/opt/xider`, a consistent online SQLite
snapshot of `/var/lib/xider`, `/etc/xider` configuration and signing material,
the XIDER systemd units, and the XIDER nginx configuration. It excludes the
GitHub deploy identity and the public recipient key. The owner-held
`vault-recipient-private.pem` is required to decrypt or restore archives and
must stay outside the VPS and GitHub repository.

Each run shares `/run/xider/update.lock` with deployment. If a deploy is in
progress, backup exits without racing it; the persistent systemd timer retries
on its next daily tick. Each successful run logs the archive name, byte count,
SHA-256, SQLite database count and retained archive count to the systemd
journal. A failed upload leaves no success marker.

The backup is recovery material, not an automatic failover. To validate a
downloaded snapshot, use the offline recovery key with `ops/x_vault.py verify`
and stage it with `ops/x_vault.py restore-stage` before restoring production.
