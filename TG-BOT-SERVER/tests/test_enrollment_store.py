import base64
import json
import secrets

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from enrollment_store import EnrollmentStore, _canonical, _decode, _encode, seal_response
from enrollment_provisioner import EnrollmentProvisioner


def request_pair():
    signing = Ed25519PrivateKey.generate()
    encryption = X25519PrivateKey.generate()
    request = {
        "request_id": secrets.token_hex(16),
        "device_id": secrets.token_hex(6),
        "platform": "macos",
        "label": "MacBook Air",
        "signing_key": _encode(signing.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        "encryption_key": _encode(encryption.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
    }
    request["proof"] = _encode(signing.sign(_canonical(request)))
    return request, signing, encryption


def poll(store, request, signing, *, nonce=None):
    nonce = nonce or secrets.token_hex(16)
    proof = _encode(signing.sign(_canonical({"request_id": request["request_id"], "nonce": nonce})))
    return store.poll(request["request_id"], nonce, proof)


def test_owner_approval_and_one_use_sealed_delivery(tmp_path):
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, signing, private = request_pair()
    submitted = store.submit(request, "a" * 64)
    assert submitted["fingerprint"] == store.pending()[0]["fingerprint"]
    assert poll(store, request, signing) == {"state": "pending"}
    assert not store.decide(request["request_id"], 2, 1, approve=True)
    assert store.decide(request["request_id"], 1, 1, approve=True)
    assert not store.decide(request["request_id"], 1, 1, approve=False)
    approved = store.claim_approved(request["request_id"])
    assert approved is not None
    settings = {"MQTT_USERNAME": "device-user", "MQTT_PASSWORD": "private-password"}
    sealed = seal_response(request["request_id"], approved["fingerprint"], approved["encryption_key"], settings)
    assert store.set_sealed_reply(request["request_id"], sealed)
    assert not store.set_sealed_reply(request["request_id"], sealed)
    assert "private-password" not in (tmp_path / "pairs.db").read_bytes().decode("latin1")

    first = poll(store, request, signing)
    assert first == {"state": "ready", "sealed": sealed}
    shared = private.exchange(X25519PublicKey.from_public_bytes(_decode(sealed["ephemeral_key"], 32)))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=request["request_id"].encode(), info=b"X-DOCK-response-v1").derive(shared)
    aad = f"{request['request_id']}:{approved['fingerprint']}".encode()
    ciphertext = base64.urlsafe_b64decode(sealed["ciphertext"] + "=" * (-len(sealed["ciphertext"]) % 4))
    decrypted = AESGCM(key).decrypt(_decode(sealed["nonce"], 12), ciphertext, aad)
    assert json.loads(decrypted) == settings
    assert poll(store, request, signing) == {"state": "delivered"}


def test_bad_request_proof_or_wrong_poll_key_cannot_receive(tmp_path):
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, signing, _ = request_pair()
    bad = dict(request, label="Altered")
    with pytest.raises(ValueError, match="possession"):
        store.submit(bad, "a" * 64)
    store.submit(request, "a" * 64)
    wrong = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="possession"):
        poll(store, request, wrong)
    assert poll(store, request, signing) == {"state": "pending"}


def test_denial_expiry_duplicate_and_rate_limit(tmp_path):
    now = [1000]
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: now[0])
    first, signing, _ = request_pair()
    store.submit(first, "a" * 64)
    with pytest.raises(ValueError, match="duplicate"):
        store.submit(first, "a" * 64)
    assert store.decide(first["request_id"], 1, 1, approve=False)
    assert poll(store, first, signing) == {"state": "denied"}
    for _ in range(4):
        request, _, _ = request_pair()
        store.submit(request, "a" * 64)
    request, _, _ = request_pair()
    with pytest.raises(ValueError, match="rate limit"):
        store.submit(request, "a" * 64)
    now[0] = 2000
    assert poll(store, first, signing) == {"state": "expired"}


def test_poll_nonce_replay_is_rejected(tmp_path):
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, signing, _ = request_pair()
    store.submit(request, "a" * 64)
    nonce = secrets.token_hex(16)
    assert poll(store, request, signing, nonce=nonce) == {"state": "pending"}
    with pytest.raises(ValueError, match="nonce reused"):
        poll(store, request, signing, nonce=nonce)


def test_provisioner_issues_isolated_broker_and_message_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, signing, _ = request_pair()
    store.submit(request, "a" * 64)
    assert store.decide(request["request_id"], 1, 1, approve=True)

    class Issuer:
        issued = []
        def issue(self, device_id, issuance_id):
            self.issued.append((device_id, issuance_id))
            return type("Credential", (), {"username": "device-user", "password": "unique-password"})()
        def revoke(self, device_id, issuance_id):
            raise AssertionError("successful issuance must not revoke")

    issuer = Issuer()
    provisioner = EnrollmentProvisioner(store, issuer, broker_host="broker.example.org", broker_port=8883, broker_prefix="xgent/v1")
    assert provisioner.fulfill(request["request_id"])
    assert issuer.issued == [(request["device_id"], request["request_id"])]
    assert (tmp_path / f"{request['device_id']}.key").is_file()
    assert poll(store, request, signing)["state"] == "ready"


def test_provisioner_revokes_broker_if_key_cannot_be_created(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, _, _ = request_pair()
    store.submit(request, "a" * 64)
    assert store.decide(request["request_id"], 1, 1, approve=True)
    (tmp_path / f"{request['device_id']}.key").write_text("existing" * 9)

    class Issuer:
        revoked = []
        def issue(self, device_id, issuance_id):
            return type("Credential", (), {"username": "device-user", "password": "unique-password"})()
        def revoke(self, device_id, issuance_id):
            self.revoked.append((device_id, issuance_id))

    issuer = Issuer()
    with pytest.raises(FileExistsError):
        EnrollmentProvisioner(store, issuer, broker_host="broker.example.org", broker_port=8883, broker_prefix="xgent/v1").fulfill(request["request_id"])
    assert issuer.revoked == [(request["device_id"], request["request_id"])]
    assert store.claim_approved(request["request_id"]) is not None


def test_only_one_concurrent_provisioning_claim_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    store = EnrollmentStore(tmp_path / "pairs.db", clock=lambda: 1000)
    request, _, _ = request_pair()
    store.submit(request, "a" * 64)
    assert store.decide(request["request_id"], 1, 1, approve=True)

    class Issuer:
        issued = []
        def issue(self, device_id, issuance_id):
            self.issued.append((device_id, issuance_id))
            return type("Credential", (), {"username": "device-user", "password": "unique-password"})()
        def revoke(self, device_id, issuance_id):
            raise AssertionError("successful issuance must not revoke")

    issuer = Issuer()
    provisioner = EnrollmentProvisioner(store, issuer, broker_host="broker.example.org", broker_port=8883, broker_prefix="xgent/v1")
    assert provisioner.fulfill(request["request_id"])
    assert not provisioner.fulfill(request["request_id"])
    assert issuer.issued == [(request["device_id"], request["request_id"])]
