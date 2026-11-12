"""Validation and atomic file-write helpers used by transfer commands."""

from __future__ import annotations

import base64
import binascii
import os
import tempfile
from urllib.parse import urlsplit


def validated_open_url(payload: dict) -> tuple[str, int]:
    url = payload.get("url")
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("URL is empty or too long")
    if any(char in url for char in "\r\n\t\x00"):
        raise ValueError("URL contains control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
        raise ValueError("Only HTTP(S) URLs with a host are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not allowed")
    if any(char.isspace() for char in parsed.netloc):
        raise ValueError("Whitespace in URL host is not allowed")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port is invalid")
    count = payload.get("count", 1)
    if type(count) is not int or count not in {1, 2, 3, 5}:
        raise ValueError("Open count must be one of 1, 2, 3, or 5")
    return url, count


def decode_file_put_payload(payload: dict, *, max_bytes: int, max_b64_chars: int) -> bytes:
    """Decode a bounded, canonical base64 file payload before touching disk."""
    if "b64" in payload:
        encoded = payload["b64"]
    elif "data" in payload:
        encoded = payload["data"]
    else:
        raise ValueError("В запросе отсутствуют данные файла.")
    if not isinstance(encoded, str):
        raise ValueError("Данные файла должны быть строкой base64.")
    if len(encoded) > max_b64_chars:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Повреждены данные base64.") from exc
    if len(content) > max_bytes:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    return content


def write_file_put_atomic(path: str, content: bytes) -> None:
    """Replace the destination only after the complete temporary file is written."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".xider-upload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary_path, path)
        temporary_path = ""
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
