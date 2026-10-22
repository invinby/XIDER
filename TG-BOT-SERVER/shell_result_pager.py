"""Short-lived in-memory paging for already-redacted owner shell output."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
import secrets
import threading


PAGE_CHARS = 2_200
MAX_RESULT_CHARS = 16_384
MAX_FOOTER_CHARS = 512
MAX_ACTIVE_RESULTS = 128
TTL_SECONDS = 300
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_LOCK = threading.RLock()


@dataclass(frozen=True)
class ResultPage:
    token: str
    owner_id: int
    device_label: str
    page: int
    total_pages: int
    text: str
    footer: str
    view: str = "source"
    has_structured: bool = False


@dataclass(frozen=True)
class ResultSnapshot:
    owner_id: int
    device_label: str
    text: str
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class _Result:
    owner_id: int
    device_label: str
    text: str
    structured_text: str | None
    footer: str
    created_at: int
    expires_at: int
    total_pages: int
    structured_total_pages: int


_RESULTS: OrderedDict[str, _Result] = OrderedDict()


def _prune(now: int) -> None:
    for token in [key for key, value in _RESULTS.items() if now >= value.expires_at]:
        del _RESULTS[token]


def create(*, owner_id: int, device_label: str, text: str, footer: str, now: int,
           structured_text: str | None = None) -> ResultPage | None:
    """Store only bounded, already-redacted text in process memory for five minutes."""
    if type(owner_id) is not int or owner_id <= 0 or type(now) is not int or now < 0:
        return None
    if not isinstance(text, str) or not text or len(text) > MAX_RESULT_CHARS:
        return None
    if not isinstance(footer, str) or len(footer) > MAX_FOOTER_CHARS:
        return None
    if not isinstance(device_label, str) or not device_label.strip() or len(device_label) > 128:
        return None
    if structured_text is not None and (not isinstance(structured_text, str) or not structured_text
                                        or len(structured_text) > MAX_RESULT_CHARS):
        structured_text = None
    total_pages = (len(text) + PAGE_CHARS - 1) // PAGE_CHARS
    structured_total_pages = ((len(structured_text) + PAGE_CHARS - 1) // PAGE_CHARS
                              if structured_text else 0)
    with _LOCK:
        _prune(now)
        if len(_RESULTS) >= MAX_ACTIVE_RESULTS:
            return None
        token = secrets.token_urlsafe(18)
        while token in _RESULTS:
            token = secrets.token_urlsafe(18)
        _RESULTS[token] = _Result(owner_id, device_label, text, structured_text, footer, now,
                                  now + TTL_SECONDS, total_pages, structured_total_pages)
    return get(token, owner_id=owner_id, page=0, now=now)


def get(token: str, *, owner_id: int, page: int, now: int, view: str = "source") -> ResultPage | None:
    """Resolve one page only for its owner while the in-memory entry is fresh."""
    if (not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)
            or type(owner_id) is not int or owner_id <= 0
            or type(page) is not int or page < 0
            or type(now) is not int or now < 0 or view not in {"source", "structured"}):
        return None
    with _LOCK:
        _prune(now)
        item = _RESULTS.get(token)
        if item is None or item.owner_id != owner_id:
            return None
        if view == "structured":
            if item.structured_text is None or page >= item.structured_total_pages:
                return None
            text = item.structured_text
            total_pages = item.structured_total_pages
        else:
            if page >= item.total_pages:
                return None
            text = item.text
            total_pages = item.total_pages
        start = page * PAGE_CHARS
        return ResultPage(token, owner_id, item.device_label, page, total_pages,
                          text[start:start + PAGE_CHARS], item.footer, view,
                          item.structured_text is not None)


def get_snapshot(token: str, *, owner_id: int, now: int) -> ResultSnapshot | None:
    """Return a full, bounded, owner-scoped copy of cached redacted source text."""
    if (not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)
            or type(owner_id) is not int or owner_id <= 0
            or type(now) is not int or now < 0):
        return None
    with _LOCK:
        _prune(now)
        item = _RESULTS.get(token)
        if item is None or item.owner_id != owner_id:
            return None
        return ResultSnapshot(owner_id, item.device_label, item.text, item.created_at, item.expires_at)
