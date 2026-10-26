"""Short-lived X-DOCK client used by the Windows and macOS installers.

The signing/encryption private keys exist only in this process' memory. The
installer receives MQTT settings only after owner approval over verified HTTPS;
secrets are written directly to the staged .env and never printed.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import platform
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_ENV_KEYS = frozenset({
    "SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS",
    "MQTT_USERNAME", "MQTT_PASSWORD", "ENCRYPT_PAYLOAD",
    "XIDER_COMMAND_VERIFY_KEYSET_B64",
})


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str, length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > 65536 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid enrollment response encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if length is not None and len(raw) != length:
        raise ValueError("invalid enrollment response length")
    return raw


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _https_endpoint(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("X-DOCK requires a clean HTTPS base URL")
    return base_url.rstrip("/"), parsed.hostname.lower()


def _post_json(url: str, expected_host: str, payload: dict, *, timeout: float = 15) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname is None or final.hostname.lower() != expected_host:
                raise ValueError("X-DOCK endpoint redirected outside its HTTPS origin")
            if response.status != 200 and response.status != 202:
                raise ValueError("X-DOCK returned an unexpected HTTP status")
            body = response.read(65537)
    except urllib.error.HTTPError as exc:
        # Do not forward server response bodies; they may contain internal details.
        raise ValueError(f"X-DOCK request failed with HTTP {exc.code}") from None
    if len(body) > 65536:
        raise ValueError("X-DOCK response is too large")
    result = json.loads(body.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("X-DOCK returned an invalid response")
    return result


def _device_identity() -> tuple[str, str]:
    config = Path.home() / ".xgent" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    device_id = data.get("device_id") if isinstance(data, dict) else None
    label = data.get("name") if isinstance(data, dict) else None
    if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{12}", device_id):
        device_id = uuid.uuid4().hex[:12]
    if not isinstance(label, str) or not re.fullmatch(r"[\w .()_-]{1,64}", label, re.UNICODE):
        label = re.sub(r"[^\w .()_-]", "_", platform.node(), flags=re.UNICODE)[:64] or "XIDER device"
    temporary = config.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"device_id": device_id, "name": label}, ensure_ascii=False), encoding="utf-8")
    if os.name == "posix":
        os.chmod(temporary, 0o600)
    os.replace(temporary, config)
    return device_id, label


def _write_env(path: Path, settings: dict) -> None:
    if not isinstance(settings, dict) or not {"SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS", "MQTT_USERNAME", "MQTT_PASSWORD", "ENCRYPT_PAYLOAD"}.issubset(settings):
        raise ValueError("X-DOCK settings are incomplete")
    if set(settings) - _ENV_KEYS:
        raise ValueError("X-DOCK returned an unsupported setting")
    if settings["MQTT_TLS"] != "true" or settings["ENCRYPT_PAYLOAD"] != "true":
        raise ValueError("X-DOCK refused an unencrypted agent configuration")
    if not re.fullmatch(r"[0-9]{1,5}", str(settings["MQTT_PORT"])) or not 1 <= int(settings["MQTT_PORT"]) <= 65535:
        raise ValueError("X-DOCK returned an invalid MQTT port")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", str(settings["MQTT_PREFIX"])):
        raise ValueError("X-DOCK returned an invalid MQTT prefix")
    for name in _ENV_KEYS & settings.keys():
        value = str(settings[name])
        if "\n" in value or "\r" in value or not value:
            raise ValueError("X-DOCK returned an invalid configuration value")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{name}={settings[name]}\n" for name in sorted(settings))
    fd, temporary_name = tempfile.mkstemp(prefix=".xider-env-", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        if os.name == "nt":
            sid = subprocess.check_output(
                ["whoami", "/user", "/fo", "csv"], text=True, stderr=subprocess.DEVNULL
            ).splitlines()
            rows = list(csv.reader(sid))
            if len(rows) < 2 or len(rows[1]) < 2 or not rows[1][1].startswith("S-"):
                raise OSError("could not resolve the current Windows user SID")
            sid = rows[1][1]
            result = subprocess.run(
                ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if result.returncode:
                path.unlink(missing_ok=True)
                raise OSError("could not restrict the staged configuration ACL")
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def enroll(base_url: str, output: Path, *, timeout_seconds: int = 600, poll_seconds: float = 2.0) -> None:
    endpoint, host = _https_endpoint(base_url)
    device_id, label = _device_identity()
    signing = Ed25519PrivateKey.generate()
    encryption = X25519PrivateKey.generate()
    request_id = secrets.token_hex(16)
    signing_public = signing.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    encryption_public = encryption.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    payload = {
        "request_id": request_id,
        "device_id": device_id,
        "platform": "windows" if os.name == "nt" else "macos",
        "label": label,
        "signing_key": _encode(signing_public),
        "encryption_key": _encode(encryption_public),
    }
    payload["proof"] = _encode(signing.sign(_canonical(payload)))
    submitted = _post_json(endpoint + "/x-dock/v1/request", host, payload)
    if submitted.get("state") != "pending" or submitted.get("request_id") != request_id:
        raise ValueError("X-DOCK did not accept the pairing request")
    print(f"X-DOCK: запрос {request_id[:8]} отправлен. Подтверди устройство в Telegram.", flush=True)
    deadline = time.monotonic() + timeout_seconds
    last_state = "pending"
    while time.monotonic() < deadline:
        nonce = secrets.token_hex(16)
        poll_payload = {"request_id": request_id, "nonce": nonce}
        poll_payload["signature"] = _encode(signing.sign(_canonical(poll_payload)))
        try:
            result = _post_json(endpoint + "/x-dock/v1/poll", host, poll_payload)
        except (TimeoutError, OSError):
            time.sleep(poll_seconds)
            continue
        state = result.get("state")
        if state != last_state:
            print(f"X-DOCK: {state}.", flush=True)
            last_state = str(state)
        if state in {"pending", "approved"}:
            time.sleep(poll_seconds)
            continue
        if state in {"denied", "expired", "delivered"}:
            raise ValueError(f"X-DOCK pairing ended: {state}")
        if state != "ready" or not isinstance(result.get("sealed"), dict):
            raise ValueError("X-DOCK returned an invalid pairing state")
        sealed = result["sealed"]
        if set(sealed) != {"ephemeral_key", "nonce", "ciphertext"}:
            raise ValueError("X-DOCK returned an invalid encrypted configuration")
        ephemeral = X25519PublicKey.from_public_bytes(_decode(sealed["ephemeral_key"], 32))
        shared = encryption.exchange(ephemeral)
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=request_id.encode("ascii"),
            info=b"X-DOCK-response-v1",
        ).derive(shared)
        fingerprint = hashlib.sha256(signing_public).hexdigest()[:16]
        aad = f"{request_id}:{fingerprint}".encode("ascii")
        plaintext = AESGCM(key).decrypt(_decode(sealed["nonce"], 12), _decode(sealed["ciphertext"]), aad)
        settings = json.loads(plaintext.decode("utf-8"))
        _write_env(output, settings)
        print("X-DOCK: конфигурация установлена в staging. Секреты не выводились.", flush=True)
        return
    raise TimeoutError("истекло время ожидания подтверждения X-DOCK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Owner-approved X-DOCK enrollment helper")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--env-output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    try:
        enroll(args.base_url, args.env_output, timeout_seconds=args.timeout)
        return 0
    except Exception as exc:
        print(f"X-DOCK: установка не завершена: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
