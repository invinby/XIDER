# X-DOCK enrollment design

Status: proposed; no enrollment endpoint or certificate has been deployed.
Decision date: 2026-10-01.

## Goal

Make the Windows and macOS first-install command short while keeping device
credentials out of the command, source tree, chat, and shared `.env`. The owner
approves each new device in Telegram. Reinstalling an existing device must not
silently replace or widen its access.

## Transport without a domain

A domain is not inherently required. Let's Encrypt now issues publicly trusted
certificates for public IPv4 and IPv6 addresses, but IP certificates use its
short-lived profile (160 hours, just over six days). The official Certbot guide
documents `--ip-address`; its March 2026 instructions require renewal automation
and a deploy hook to reload the certificate, because the web-server installer
plugins did not then support IP certificates. Recheck the current Certbot
support before implementing the VPS setup.

Candidate transport: HTTPS on the VPS's reserved public IP, with normal
certificate validation on Windows and macOS. Let's Encrypt IP validation uses
HTTP-01 or TLS-ALPN-01, not DNS-01. The VPS therefore needs a stable public IP,
reachable challenge traffic when issuing/renewing, and a tested certificate
renewal/reload timer. If those conditions cannot be met, stop and choose a
domain or another explicitly pinned trust model; never silently disable TLS
validation or fall back to the public MQTT broker for enrollment secrets.

Sources:

- [Let's Encrypt: 6-day and IP Address Certificates are Generally Available](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
- [Let's Encrypt: Six-Day and IP Address Certificates Available in Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [Certbot 5.8 manual](https://eff-certbot.readthedocs.io/en/stable/man/certbot.html)

## Enrollment sequence

1. A version-pinned, release-verified bootstrap downloads only the matching
   signed release. The root-of-trust for the first bootstrap remains a separate
   release gate; HTTPS alone must not be described as a release signature.
2. The device generates a unique key pair locally. The private key stays on the
   device in an OS-protected store (Windows DPAPI/Credential Manager; macOS
   Keychain), never in installer arguments or logs.
3. The device opens a TLS-verified request containing a public key, random
   request ID, supported platform/version, and proof of possession. The request
   expires quickly, is rate-limited, and creates only a pending record.
4. The bot notifies the owner with a readable device label and public-key
   fingerprint. Approve and deny are explicit, owner-only actions. Approval is
   single-use and atomically bound to that exact request and public key.
5. The server issues a unique, revocable device credential with least-privilege
   ACLs. The credential/config response is encrypted to the device public key,
   delivered once, and protected against replay. No shared bot `.env`, SSH key,
   or reusable enrollment token is sent to the device.
6. The agent stores the credential in the OS store, starts the service, and
   reports a correlated health proof. If setup or health validation fails, the
   installer restores the prior version and leaves the prior credential intact.

The broker must support per-device identities/ACLs (or a controlled credential
issuer that provisions them) before step 5 can be considered implemented. The
current shared MQTT credential is not an acceptable X-DOCK target.

## Required failure and security tests

- Expired, replayed, malformed, rate-limited, denied, and duplicate requests
  cannot issue credentials.
- An approval is bound to one request and one public key; concurrent approval
  and denial has exactly one result.
- A response cannot be retrieved with a different device key, request ID, or
  nonce. Credentials and private material never appear in logs, test output, or
  the command line.
- Reinstall preserves existing identity/config; explicit re-pairing and
  revocation are separate audited operations.
- MQTT ACL tests prove that a device can publish/subscribe only to its own
  topics and permitted shared status topics.
- Windows and macOS integration fixtures prove rollback on enrollment,
  credential-storage, service-start, and health-check failure.
- Live rollout is staged only after stable-IP, port reachability, TLS renewal,
  broker ACL, Telegram approval, and backup/recovery have all been verified.

## Current evidence and non-claims

The last recorded live server snapshot said only SSH was listening. That snapshot
is historical and was not rechecked for this design decision. No HTTPS endpoint,
ACME certificate, enrollment API, per-device broker credential, or live pairing
has been created or tested. The design removes a domain purchase as a hard
prerequisite, not the need to verify VPS networking and automate six-day renewal.
