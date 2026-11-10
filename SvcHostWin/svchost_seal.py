"""Machine-bound sealing for the private .env configuration.

The agent still reads normal KEY=VALUE pairs, but the file itself is stored as
``SVC1:<base64>`` bound to this machine (Windows MachineGuid / macOS
IOPlatformUUID). A copied file is useless elsewhere and a plain string scan of
the install tree never sees broker credentials. Best effort: any failure falls
back to the ordinary plaintext .env path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
from pathlib import Path

MAGIC = "SVC1:"
_SALT = b"svchost-config-v1\x00"
_TAG_BYTES = 16
_NONCE_BYTES = 16


def _machine_id() -> bytes:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value).encode("utf-8", "replace")
        except OSError:
            pass
    else:
        try:
            import subprocess

            output = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout
            for line in output.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2].encode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    try:
        host = os.uname().nodename
    except AttributeError:
        host = os.environ.get("COMPUTERNAME") or "localhost"
    return str(host).encode("utf-8", "replace")


def _key() -> bytes:
    return hashlib.sha256(_SALT + _machine_id()).digest()


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks = bytearray()
    counter = 0
    while len(chunks) < length:
        chunks += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(chunks[:length])


def seal(text: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    data = text.encode("utf-8")
    encrypted = bytes(a ^ b for a, b in zip(data, _stream(_key(), nonce, len(data))))
    tag = hmac.new(_key(), nonce + encrypted, hashlib.sha256).digest()[:_TAG_BYTES]
    return MAGIC + base64.b64encode(nonce + tag + encrypted).decode("ascii")


def unseal(blob: str) -> str:
    raw = base64.b64decode(blob[len(MAGIC):].strip(), validate=True)
    nonce = raw[:_NONCE_BYTES]
    tag = raw[_NONCE_BYTES:_NONCE_BYTES + _TAG_BYTES]
    encrypted = raw[_NONCE_BYTES + _TAG_BYTES:]
    expected = hmac.new(_key(), nonce + encrypted, hashlib.sha256).digest()[:_TAG_BYTES]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("sealed environment failed its integrity check")
    return bytes(a ^ b for a, b in zip(encrypted, _stream(_key(), nonce, len(encrypted)))).decode("utf-8")


def _apply(text: str) -> None:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def load(path: Path) -> bool:
    """Load ``path``, sealing a plaintext copy in place. Never raises."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    if raw.startswith(MAGIC):
        try:
            _apply(unseal(raw.strip()))
            return True
        except (ValueError, UnicodeError):
            return False
    try:
        from dotenv import load_dotenv

        try:
            load_dotenv(path, encoding="utf-8-sig")
        except TypeError:
            load_dotenv(path)
    except Exception:  # noqa: BLE001 - optional dependency at seal time
        _apply(raw)
    try:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(seal(raw) + "\n", encoding="ascii")
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        pass
    return True
