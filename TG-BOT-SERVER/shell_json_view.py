"""Bounded field view for already-redacted JSON shell output."""

from __future__ import annotations

import json
import re
from typing import Any


MAX_INPUT_CHARS = 16_384
MAX_FIELDS = 128
MAX_DEPTH = 8
MAX_OUTPUT_CHARS = 16_384
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")


def _reject_non_json_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def format_json_fields(redacted_text: str) -> str | None:
    """Flatten a valid, already-redacted JSON document for a compact UI view.

    This function deliberately does not receive raw command output. Invalid,
    over-depth, or over-budget data falls back to the existing source view.
    """
    if not isinstance(redacted_text, str) or not redacted_text or len(redacted_text) > MAX_INPUT_CHARS:
        return None
    try:
        document = json.loads(redacted_text, parse_constant=_reject_non_json_constant)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None

    fields: list[str] = []

    def walk(value: Any, path: str, depth: int) -> bool:
        if depth > MAX_DEPTH or len(fields) >= MAX_FIELDS:
            return False
        if isinstance(value, dict):
            if not value:
                fields.append(f"{path or '$'} = {{}}")
            for key, child in value.items():
                segment = (key if not path else f".{key}") if _IDENTIFIER.fullmatch(key) else f"[{json.dumps(key, ensure_ascii=False)}]"
                if not walk(child, f"{path}{segment}", depth + 1):
                    return False
            return True
        if isinstance(value, list):
            if not value:
                fields.append(f"{path or '$'} = []")
            for index, child in enumerate(value):
                if not walk(child, f"{path}[{index}]", depth + 1):
                    return False
            return True
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        fields.append(f"{path or '$'} = {encoded}")
        return len(fields) <= MAX_FIELDS

    try:
        if not walk(document, "", 0):
            return None
    except (RecursionError, TypeError, ValueError):
        return None
    output = "Структурированные поля JSON\n" + "\n".join(fields)
    if len(output) > MAX_OUTPUT_CHARS:
        return None
    return output
