# XIDER TARPED — product and release contract

TARPED is the name reserved for the next major XIDER release. This document is
the design contract, **not** evidence that TARPED is installed or complete.
The currently published bot version remains defined in
`TG-BOT-SERVER/version.py` until the release checklist is satisfied.

## Names with responsibilities

| Name | Responsibility |
| --- | --- |
| X-STAB | Telegram-facing pages, buttons, text, and replies |
| X-CORE | server-side users, rights, devices, and command orchestration |
| Guard Keeper | visible agent supervisor; one platform-specific implementation per OS |
| TwinShift | staged update, health confirmation, and rollback |
| X-EDGE-W | Windows agent |
| X-EDGE-M | macOS agent |
| XIDER LINK | message transport and shared protocol |
| X-DOCK | initial, repeatable installation and enrollment |
| X-LEDGER | release list and component compatibility |
| X-PROBE | post-start and post-update functional health checks |
| X-TRACE | operation and rollback history without credentials |
| X-VAULT | authenticated encrypted backups and staging restores |
| X-LEX | six presentation voices; never changes authorization or command semantics |
| X-GATE | access checks and explicit confirmations |
| X-MAP | declared capabilities and required OS permissions |
| X-LOCK | exactly one active agent installation per user/device |

X-STAB and X-CORE currently share one server process. TwinShift, X-LEDGER,
X-LEX, and the other names may be libraries inside the same repository. A name
does not imply a separate service or GitHub repository.

## Version discipline

Use `major.minor.patch`: major for incompatible protocol/architecture changes,
minor for a delivered feature group, patch for compatible fixes. The platform
release has a version and codename (for example, `4.0.0 TARPED`); each component
has its own version. Do not increment unrelated components merely to align
numbers. Dates belong in release metadata, not as random version jumps.

Every release, including a small fix, receives a short descriptive title and
notes stating changes, fixes, known limitations, compatibility, and rollback.
Existing Git tags and releases remain intact. Published GitHub Releases, not
moving `main` or an arbitrary source ZIP, are the version catalogue.

An installable component release requires an OS-specific asset, size and digest,
minimum compatible X-CORE/LINK version, and a tested rollback path. A tag with
no applicable asset is browseable but not installable. Earlier CI builds only
published agent assets. A new workflow is prepared to package Guard Keeper and
the server, but it has not yet run on a release tag or passed a live install.

## TwinShift target sequence

1. Resolve a trusted, immutable published release and check component/OS and
   protocol compatibility.
2. Download into an inactive A/B slot; verify digest and package contents.
3. Preserve local configuration and current working slot. Never place secrets
   in GitHub assets or print them in bot messages.
4. Start/activate the new slot. X-PROBE must confirm a functional response, not
   just a running process.
5. Mark the new slot active only after confirmation. On failure, restore the
   previous slot and record the reason in X-TRACE.

The Telegram polling bot may pause briefly during activation; “zero downtime”
must not be claimed without a live test. Guardian updates need an independent
recovery path and separate tests. No old release is offered for installation
merely because a Git tag exists.

## Six X-LEX voices

`xtexbo`, `xperson`, `xpikmi`, `xplain`, `xnoir`, and `xadam` (displayed as
TRUE ADAM). All six must cover navigation, results, errors, confirmations,
admin, and pranks. Dynamic values must be escaped before HTML rendering.
The wording of dangerous actions must remain unambiguous in every voice.
Legacy `technical`, `custom`, and `conversational` settings map to the new
voices without deleting owner-edited copy.

## TARPED acceptance checklist

- [ ] Exactly one Windows agent tray instance and one scheduled task; stale
      desktop and registry launches accounted for without losing `.env`.
- [ ] macOS LaunchAgent, Windows task, and Guard Keeper start/stop/status tested
      on real devices; no duplicate online/offline notifications.
- [ ] Six voices cover every visible button and response, validated by tests.
- [ ] X-MAP records OS support, permissions, and stability for every command.
- [ ] Geolocation returns truthful approximate IP location or a clear error on
      both platforms; it is never represented as precise GPS.
- [ ] X-LEDGER shows all published releases, exact applicable assets, full
      notes, and a component compatibility matrix.
- [ ] TwinShift installs a chosen release, verifies it on Windows/macOS/VPS,
      and automatically rolls back failed updates, including Guardian updates.
- [ ] X-VAULT backups are copied to the second VPS, authenticated, and restored
      into staging successfully before being counted as recovery-ready.
- [ ] X-DOCK repeat installation preserves secrets and does not delete a
      protected `.env` or leave a half-updated checkout.
- [ ] Bot pages edit an existing card where possible; duplicate messages and
      endless decorative progress loops are removed.
- [ ] Offline tests and live Telegram/MQTT/Windows/macOS/VPS evidence recorded;
      backup and rollback rehearsal complete before `4.0.0` is tagged.

## Current implementation status

The first TARPED source changes add X-LEX core copy and owner selection,
X-LEDGER's read-only GitHub Release catalogue, a per-device version browser,
and an in-bot handbook. They do **not** yet implement A/B installation,
complete six-voice coverage, or live-verified updates. Those items remain
unchecked above and must not be advertised as released.
