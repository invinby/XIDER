"""Validation and small JSON-friendly storage helpers for owner URL shortcuts."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit


MAX_URL_LENGTH = 2048
MAX_FAVORITES = 20
MAX_NAME_LENGTH = 32
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def normalize_web_url(value: str) -> str:
    """Return a usable HTTP(S) URL; reject local file and external app schemes."""
    url = str(value or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValueError("URL is empty or too long")
    if any(char in url for char in "\r\n\t\x00"):
        raise ValueError("URL contains control characters")
    if not _SCHEME_RE.match(url):
        url = "https://" + url
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are allowed")
    if not parsed.hostname or not parsed.netloc:
        raise ValueError("URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not allowed")
    if any(char.isspace() for char in parsed.netloc):
        raise ValueError("Whitespace in URL host is not allowed")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port is invalid")
    return url


def normalize_favorites(value) -> list[dict[str, str]]:
    """Keep only valid, uniquely named shortcuts from persisted JSON data."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        favorite_id = str(row.get("id") or "").lower()
        try:
            url = normalize_web_url(row.get("url") or "")
        except ValueError:
            continue
        folded = name.casefold()
        if not name or len(name) > MAX_NAME_LENGTH or folded in seen_names:
            continue
        if not _ID_RE.fullmatch(favorite_id) or favorite_id in seen_ids:
            continue
        seen_ids.add(favorite_id)
        seen_names.add(folded)
        result.append({"id": favorite_id, "name": name, "url": url})
        if len(result) >= MAX_FAVORITES:
            break
    return result


def add_favorite(value, name: str, url: str) -> list[dict[str, str]]:
    favorites = normalize_favorites(value)
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > MAX_NAME_LENGTH:
        raise ValueError("Favorite name must be 1–32 characters")
    if any(row["name"].casefold() == clean_name.casefold() for row in favorites):
        raise ValueError("Favorite name already exists")
    if len(favorites) >= MAX_FAVORITES:
        raise ValueError("Favorite list is full")
    clean_url = normalize_web_url(url)
    favorites.append({"id": uuid.uuid4().hex[:12], "name": clean_name, "url": clean_url})
    return favorites


def remove_favorite(value, favorite_id: str) -> list[dict[str, str]]:
    return [row for row in normalize_favorites(value) if row["id"] != str(favorite_id).lower()]
