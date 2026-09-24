# XIDER release history

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
