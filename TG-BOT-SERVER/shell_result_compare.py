"""Owner-selected comparison of short-lived, already-redacted shell results."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import difflib
import html
import threading

import access_store
import shell_result_pager


MAX_SELECTIONS = 128
MAX_DIFF_CHARS = 2_800
MAX_ESCAPED_DIFF_CHARS = 3_000
_TRUNCATION_MARKER = "[… diff сокращён]"
_LOCK = threading.RLock()


@dataclass(frozen=True)
class SelectionResult:
    state: str
    text: str = ""


@dataclass(frozen=True)
class _Selection:
    owner_id: int
    token: str
    text: str
    expires_at: int


_SELECTIONS: OrderedDict[int, _Selection] = OrderedDict()


def _prune(now: int) -> None:
    for owner_id, selection in list(_SELECTIONS.items()):
        if now >= selection.expires_at:
            del _SELECTIONS[owner_id]


def _diff(first: str, second: str) -> str:
    first_safe = access_store.redact_shell_output_with_summary(first)[0]
    second_safe = access_store.redact_shell_output_with_summary(second)[0]
    if first_safe == second_safe:
        return "Результаты совпадают после очистки чувствительных строк."
    lines = list(difflib.unified_diff(
        first_safe.splitlines(), second_safe.splitlines(),
        fromfile="Выбор 1", tofile="Выбор 2", lineterm="",
    ))
    selected: list[str] = []
    raw_size = 0
    escaped_size = 0
    truncated = False
    for line in lines:
        line_raw_size = len(line) + 1
        line_escaped_size = len(html.escape(line)) + 1
        if (raw_size + line_raw_size > MAX_DIFF_CHARS
                or escaped_size + line_escaped_size > MAX_ESCAPED_DIFF_CHARS - len(_TRUNCATION_MARKER) - 1):
            truncated = True
            break
        selected.append(line)
        raw_size += line_raw_size
        escaped_size += line_escaped_size
    output = "\n".join(selected)
    if truncated:
        output += "\n" + _TRUNCATION_MARKER
    return output or "Результаты совпадают после очистки чувствительных строк."


def select(token: str, *, owner_id: int, now: int) -> SelectionResult:
    """Select one result; the next distinct fresh result consumes the selection."""
    if type(owner_id) is not int or owner_id <= 0 or type(now) is not int or now < 0:
        return SelectionResult("unavailable")
    snapshot = shell_result_pager.get_snapshot(token, owner_id=owner_id, now=now)
    if snapshot is None:
        return SelectionResult("unavailable")
    safe_text = access_store.redact_shell_output_with_summary(snapshot.text)[0]
    if not safe_text or len(safe_text) > shell_result_pager.MAX_RESULT_CHARS:
        return SelectionResult("unavailable")
    with _LOCK:
        _prune(now)
        selected = _SELECTIONS.get(owner_id)
        if selected is None:
            if len(_SELECTIONS) >= MAX_SELECTIONS:
                return SelectionResult("unavailable")
            _SELECTIONS[owner_id] = _Selection(owner_id, token, safe_text, snapshot.expires_at)
            return SelectionResult("waiting_for_second", "Первый результат выбран. Нажми сравнение на второй карточке в течение 5 минут.")
        if selected.token == token:
            return SelectionResult("same_result", "Это тот же результат. Выбери другой, чтобы сравнить.")
        del _SELECTIONS[owner_id]
    return SelectionResult("compared", _diff(selected.text, safe_text))
