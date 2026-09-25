# XIDER release history

## X3.3.0+20260925 — Server operations and controlled copy

- Owner-only server panel now exposes service status, recent logs, restart,
  prepared-package update and rollback, with explicit confirmation for
  destructive operations.
- Added editable owner-managed bot texts, while escaping user-provided copy
  before rendering it as Telegram HTML.
- File transfer now accepts the canonical `data`/`filename` schema and keeps
  compatibility aliases for existing Windows agents; uploads honor an
  explicit destination path on both agents.
- Added MQTT ACL configuration and a backup/health-check/rollback updater plus
  a secret-rotation helper. Runtime secrets remain in `.env` files only.

## X3.2.0+20260925 — Agent reliability and truthful capabilities

- Command responses now carry the MQTT correlation id through both agents,
  preventing one user's fast response from being shown for another command.
- Bot waits use tracked command ids and no longer reuse stale text after a
  timeout; MQTT failures are reported immediately.
- Windows and macOS geolocation reports use HTTPS and return a truthful
  failure status when the lookup is unavailable.
- macOS brightness/night-light/rotation now report unsupported dependencies
  instead of falsely claiming success; brightness uses the optional
  `brightness` utility when installed.
- Added the safe local launcher path and developer-contact settings while
  retaining the owner/access-control layer.

## X3.1.0+20260925 — Access control foundation

- Roles: protected owner, co-owner, user and guest.
- Owner-only administration panel: users, roles, blocks, per-device grants,
  per-button grants, direct owner-to-user messages and a privacy-aware audit log.
- New `/start` notifications to the owner. New accounts start as guests.
- Device selection is now isolated by Telegram account instead of one global
  selected target.
- Server section: MQTT health visibility and a switch for confirmation of new
  device registrations.
- Runtime reset scripts preserve `.env`, SSH keys and all credentials while
  backing up then clearing device/user/log state.

## Version format

`X<major>.<minor>.<patch>+<YYYYMMDD>`

- **major** — protocol or architecture break;
- **minor** — a delivered feature group;
- **patch** — compatible fixes;
- **build date** — exact release build date.

Tags use the compatible Git form `v<major>.<minor>.<patch>`, for example
`v3.1.0`. Existing tags and GitHub releases are never removed by this flow.
