# XIDER private source backup

`invinby/XIDER-PRIVATE-BACKUP` is a private mirror of the public source
repository. Its Actions workflow pulls `main` and version tags from the public
repository hourly. The first mirror is seeded from the release commit; future
syncs run on the hourly schedule or by manually dispatching
`Mirror source to the private XIDER backup` from the private repository's
Actions tab.

The mirror contains repository history, project files, and version tags. It
does not contain VPS runtime data, `.env` files, signing keys, MQTT credentials,
or device credentials. Runtime backups remain handled by the separate X-VAULT
encrypted backup flow.
