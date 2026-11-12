"""Bounded, redacted diagnostics command handlers."""

from __future__ import annotations

import os
import re


def read_agent_logs(*, config_dir, device_id, publish_response) -> None:
    """Return a bounded, redacted tail of local component logs."""
    result = {
        "type": "agent_logs", "device_id": device_id, "ok": False,
        "state": "unavailable", "lines": 0, "text": "",
    }
    try:
        secret_patterns = (
            re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization|private[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"),
            re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        )
        clean = []
        truncated = False
        unavailable_sources = []
        for label, log_path in (
            ("agent", config_dir / "xgent.log"),
            ("guardian", config_dir / "guardian.log"),
            ("safari", config_dir / "safari.log"),
            ("siri", config_dir / "siri.log"),
        ):
            try:
                with log_path.open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 16 * 1024), os.SEEK_SET)
                    raw = stream.read(16 * 1024)
            except FileNotFoundError:
                continue
            except OSError:
                unavailable_sources.append(label)
                continue
            truncated = truncated or size > 16 * 1024
            text = raw.decode("utf-8", errors="replace")
            all_rows = text.splitlines()
            truncated = truncated or len(all_rows) > 40
            for row in all_rows[-40:]:
                for pattern in secret_patterns:
                    row = pattern.sub(
                        lambda match: f"{match.group(1)}=[REDACTED]"
                        if match.lastindex and match.lastindex >= 3 else "Bearer [REDACTED]",
                        row,
                    )
                clean.append(f"[{label}] {row[-480:]}")
        if not clean and unavailable_sources:
            result.update(state="unavailable", text="Не удалось прочитать локальные журналы XIDER-компонентов.")
            publish_response("output", result)
            return
        if not clean:
            result.update(state="missing", text="Локальные журналы XIDER-компонентов ещё не созданы.")
            publish_response("output", result)
            return
        joined = "\n".join(clean)
        truncated = truncated or len(clean) > 80 or len(joined) > 8000
        result.update(
            ok=True, state="available", lines=min(len(clean), 80),
            truncated=truncated, partial=bool(unavailable_sources),
            text=joined[-8000:],
        )
    except FileNotFoundError:
        result.update(state="missing", text="Локальные журналы XIDER-компонентов ещё не созданы.")
    except OSError:
        result.update(state="unavailable", text="Не удалось прочитать локальные журналы XIDER-компонентов.")
    publish_response("output", result)
