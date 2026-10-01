# XIDER release-signing key

The source updater accepts only a GitHub Release whose manifest has a valid
Ed25519 signature from a key pinned in `XGENT-MCS/release_signature.py`. The
publisher key is separate from the X-VAULT recovery passphrase, MQTT credentials,
and SSH keys. The private half must never enter Git, a source archive, either
VPS, an endpoint agent, or a chat.

Tagged releases also carry OpenSSH signatures for the exact Windows and macOS
bootstrap hashes. The signed message binds the platform, semantic release tag,
full source commit, and SHA-256 digest. `XIDER-QUICKSTART.txt` obtains the
bootstrap from that full commit, obtains its `.sig` from the matching release,
and verifies the signature with the public key pinned in
`release_signature.py` before executing the script. Client verification uses
the stock OpenSSH `ssh-keygen -Y verify` command. This authenticates the
bootstrap against a trusted quickstart; it does not independently sign the
quickstart or remove the need to trust its initial download over HTTPS/GitHub.

## One-time key creation

Use the checked-in helper from a trusted checkout. The final directory must be
new and outside the repository; the helper refuses an existing directory and
restricts its ACL (Windows) or permissions (macOS/Linux). It creates an
unencrypted PEM for the GitHub Actions secret and a base64 copy for that same
secret. Store these files in an owner-controlled encrypted vault/offline backup.
The helper prints only the public key and its ID.

Windows PowerShell:

```powershell
$keyDir = Join-Path $env:LOCALAPPDATA 'XIDER\release-signing'
New-Item -ItemType Directory -Path (Split-Path -Parent $keyDir) -Force | Out-Null
py -3.12 .\tools\provision_release_key.py --directory $keyDir
```

macOS:

```sh
key_dir="$HOME/Library/Application Support/XIDER/release-signing"
mkdir -p "$(dirname "$key_dir")"
python3 tools/provision_release_key.py --directory "$key_dir"
```

If the output directory already exists, stop and inspect it; do not delete or
overwrite it to rerun key generation.

## Pinning and publishing

The current local source pins publisher key ID `521c56c0c89f0ddd`. The
corresponding private files are outside the checkout at
`%LOCALAPPDATA%\XIDER\release-signing`; keep them in an encrypted owner-held
backup before using the key for a release. Never add either private file to
source, a release asset, a VPS, or chat.

For a future key rotation, review the generated public values and add only the
public bytes to `release_signature.py`:

```python
TRUSTED_RELEASE_KEYS = {
    "<key_id>": bytes.fromhex("<public_key_hex>"),
}
```

Run the release-signature tests and publish that reviewed change before making
a signed version tag. The `xider-release-signing` GitHub environment has been
created for `invinby/XIDER` and restricted to `v*` tags; add
`XIDER_RELEASE_PRIVATE_KEY_B64` there from the protected
`release-signing-key.b64` file. Do not paste the value into a terminal command,
commit it, or send it in chat. The tagged workflow rejects a missing secret or
a secret whose public key is not pinned in the tagged source.

The key has been generated and pinned locally. The bootstrap-signing step uses
the same protected GitHub environment secret and fails closed if the key is
missing or does not match the pinned public key. No signed release is considered
available until the secret is installed, the tagged workflow succeeds, and the
resulting signatures and manifest are published. The mutable-branch bootstrap
command remains a development path and is not made signed by this mechanism.
