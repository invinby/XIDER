# XIDER security notes

This document describes controls visible in the repository source. It is not a
claim that a particular deployment, device, or release has passed live
security acceptance.

## Transport and message protection

The bot and agents support TLS-protected MQTT. Both agent configurations stop
startup when `MQTT_TLS` is disabled. Messages are signed with HMAC-SHA256 using
the shared `SHARED_KEY`; receivers validate signatures and apply timestamp and
nonce replay checks. Keep the shared secret private and consistent across the
server and authorized agents.

Payload encryption uses AES-256-GCM with a key derived from `SHARED_KEY` using
HKDF-SHA256. It is conditional on `ENCRYPT_PAYLOAD=true` in the participating
components; it is not guaranteed by every source-run configuration. The
Windows installer requires TLS and encrypted payload configuration before
changing the scheduled task. Configure each deployment deliberately and verify
its actual settings.

## Accounts and permissions

The protected owner is determined by server configuration. Other accounts can
have user, guest, or blocked state. User permissions are scoped to devices and
actions, and the bot checks those grants when handling device controls. Some
actions also require confirmation. Operating-system permissions remain under
local user control; bot authorization does not grant macOS or Windows system
permissions.

## Updates and audit

The agent update path selects a release tag, requires owner confirmation, and
checks the pinned publisher key and signed package before activation. The
transactional updater retains the prior version for rollback when activation
fails. The bot writes security-relevant events to its local audit log. Treat
logs and deployment credentials as sensitive operational data.

## Windows Defender alerts

Do not add Defender exclusions or disable protection to make XIDER run. Before
installing a package, verify its origin, release tag, publisher signature, and
signed manifest against the project’s published release information. If
Defender reports a detection, stop the install, preserve the alert details and
file hash, and investigate the exact artifact. Do not assume that a detection
is a false positive solely because the project uses PyInstaller; use
Microsoft’s reporting process or your organization’s security team when the
evidence is inconclusive.

## Reporting

For a suspected vulnerability, include the affected commit or release, the
relevant component, reproducible steps, and impact. Remove credentials,
personal data, message payloads, and private keys from any report or log sample.
