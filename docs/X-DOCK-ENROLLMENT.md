# X-DOCK enrollment design

Status: in progress; no enrollment endpoint or certificate has been deployed.
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

## Broker compatibility gate

The current project configuration uses EMQX Cloud Serverless. Its current
official feature matrix lists built-in password and X.509 authentication, but
does not support extended password authentication, JWT authentication,
extended authorization, or HTTP authorization on Serverless deployments. The
EMQX Cloud API overview describes platform and deployment APIs, but the
documented deployment API does not expose CRUD for built-in MQTT authentication
users. Therefore the proposed HTTPS enrollment service cannot safely create
and revoke Serverless MQTT users through an assumed API, and the shared
credential must not be copied to newly installed devices as a shortcut.

For X-DOCK, select one broker path before implementing credential issuance:

1. Keep the managed broker only if its supported API can create, scope, and
   revoke per-device identities and ACLs for the actual deployment. Confirm
   this against the account's plan and the current API before coding the
   adapter.
2. Upgrade to a managed deployment that supports mTLS client-certificate
   mapping and an owner-controlled revocation path. EMQX Cloud documents a
   password-free mTLS flow and dynamic `${clientid}`/`${username}` ACL
   placeholders, but its certificate-mapping instructions currently specify
   Dedicated/Dedicated Flex, not Serverless. Serverless's feature matrix lists
   X.509 support, so confirm the actual account's available settings and
   revocation mechanism before selecting this path.
3. Move to a broker deployment that supports a controlled credential issuer
   and per-device ACLs, such as a self-hosted broker with an owner-controlled
   provisioning interface. The checked-in Mosquitto example is not sufficient
   as-is: it currently defines one shared account and a static ACL.

This is a release blocker for password-free first install, not a reason to
fall back to SSH, share the bot `.env`, or weaken TLS. The one-command
installer can only become end-to-end after this gate, the HTTPS endpoint, and
owner approval flow are deployed together.

References checked 2026-10-26:

- [EMQX Cloud authentication support by deployment](https://docs.emqx.com/en/cloud/latest/deployments/auth_overview.html)
- [EMQX Cloud HTTP authentication](https://docs.emqx.com/en/cloud/latest/deployments/http_auth.html)
- [EMQX Cloud JWT authentication](https://docs.emqx.com/en/cloud/latest/deployments/jwt_auth.html)
- [EMQX Cloud HTTP authorization](https://docs.emqx.com/en/cloud/latest/deployments/http_authz.html)
- [EMQX Cloud API overview](https://docs.emqx.com/en/cloud/latest/api/api_overview.html)
- [EMQX Cloud deployment management API](https://docs.emqx.com/en/cloud/latest/api/deployment_manage.html)
- [EMQX Cloud mTLS device-connection guide](https://docs.emqx.com/en/cloud/latest/deployments/mtls_best_practices.html)
- [EMQX Cloud default authorization and client-ID placeholders](https://docs.emqx.com/en/cloud/latest/deployments/default_authz.html)

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

## Backend progress (2026-10-27; not a releasable installer)

`broker/dynsec_issuer.py` now builds a separate Dynamic Security role and
credential for an approved device. It denies unmatched publish, subscribe, and
receive operations, permits that device's output topics and the two command
subscriptions used by Agent and Guardian, and can disable the identity.
`broker/dynsec_channel.py` provides a TLS-validated MQTT admin channel with
correlated control replies. Admin credentials must be supplied by the server,
not by the installer. The focused policy/channel tests run without a live broker.
The bot now accepts optional device-specific HMAC/AES keys from the absolute
`XIDER_DEVICE_KEYS_DIR` directory. A file named `<device-id>.key` contains one
ASCII key and must be readable only by the bot user (mode 0600 on Linux).
Devices without a file retain the legacy shared key. A malformed or unreadable
file rejects the message/command rather than downgrading to that shared key.

The current working branch now contains a loopback HTTP enrollment endpoint,
one-use SQLite request store, owner-only Telegram approve/deny/retry handlers,
and a provisioner that seals settings to the requesting device. Provisioning
claims an approval atomically and uses a request-scoped broker identity so a
duplicate callback cannot revoke an existing device's MQTT user. The Telegram
bot can route enrolled devices through a separate MQTT transport and uses the
matching per-device message key. These are source changes with focused offline
tests; they are not a live deployment.

`deploy/x_dock_client.py` now creates short-lived request keys in memory, uses
the signed approval/poll flow, decrypts the response, and writes the staged
agent `.env` without printing secrets. Both platform bootstraps can call it when
`XIDER_DOCK_URL` is configured. The release builder forwards the public HTTPS
URL from the GitHub Actions repository variable into the signed Windows and
macOS installer bodies; it does not embed enrollment or broker credentials.
This is not yet an end-to-end first install:
the current `broker/docker-compose.yml` still uses a static shared account and
does not enable Dynamic Security. There is no bot MQTT role bootstrap, HTTPS
reverse proxy, publicly trusted certificate, or automatic certificate-renewal/
reload job. `XIDER_DOCK_ENABLED` therefore stays off. No live pairing or
one-command no-SSH install is ready to publish.

`deploy/install-x-dock.sh` is a preparation script for an already-installed bot
VPS. It creates a separate Mosquitto instance, bootstraps Dynamic Security on
loopback, removes the demo identity, issues a short-lived IP certificate, and
configures the TLS listener plus HTTPS enrollment proxy and renewal hook. It
stores broker credentials only in the server's protected bot environment and
does not print them. It currently requires the bot's exact `MQTT_PREFIX=xgent/v1`
and opens host-firewall ports 80, 443 and 8884 when UFW is active; the cloud
security group still needs those inbound ports. The script is not live-verified:
it has not been run on a VPS, the provider security-group ports have not been
checked, and certificate renewal and device enrollment have not been exercised
end to end.

Next required sequence: review and run the VPS installer after SSH access and
provider ports are available; verify certificate issuance and renewal; test
owner-approved pairing, cancellation, and recovery when bootstrap exits;
install Agent and Guard Keeper transactionally; then verify cross-device ACLs,
replay, denial, enrollment, restart, and rollback before enabling it on a
server. A one-command installer and bot-driven install remain unverified until
that full sequence succeeds on Windows and macOS.

## Current evidence and non-claims

The last recorded live server snapshot said only SSH was listening. That snapshot
is historical and was not rechecked for this design decision. Local source now
has the enrollment API, credential issuer, and client helper, but there is no
HTTPS endpoint, ACME certificate, configured Dynamic Security broker, or live
pairing. The design removes a domain purchase as a hard prerequisite, not the
need to verify VPS networking and automate six-day renewal.
