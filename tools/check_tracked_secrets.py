#!/usr/bin/env python3
"""Fail closed on common credentials committed to the tracked XIDER tree.

This is a lightweight guardrail for accidental commits, not a replacement for
provider-side secret scanning. It never prints matched values.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENV_EXAMPLES = {".env.example", ".env.sample"}
SENSITIVE_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".crt"}

# Test-only PEM text exercises log redaction and archive rejection. Lock the
# allowlist to exact files and counts so adding a marker elsewhere fails CI.
PEM_FIXTURE_COUNTS = {
    "TG-BOT-SERVER/tests/test_access_store.py": 5,
    "TG-BOT-SERVER/tests/test_bot_ui.py": 1,
    "TG-BOT-SERVER/tests/test_diagnostics.py": 1,
    "TG-BOT-SERVER/tests/test_server_ops.py": 1,
    "XGENT-MCS/tests/test_shell_approval_gate.py": 2,
    "XGENT-WDS/tests/test_shell_approval_gate.py": 2,
    "tools/test_command_approval_packaging.py": 6,
}

MARKER_PATTERNS = (
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    ),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Telegram bot token", re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
)
PRIVATE_KEY_MARKER = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(BOT_TOKEN|API_KEY|ACCESS_TOKEN|CLIENT_SECRET|MQTT_PASSWORD|"
    r"PASSWORD|PASSWD|PRIVATE_KEY|SECRET_KEY)\b\s*[:=]\s*"
    r"[\"']([A-Za-z0-9_+/=.-]{24,})[\"']"
)
PLACEHOLDER_MARKERS = (
    "${", "<", "your_", "your-", "example", "changeme", "replace", "dummy",
    "fake", "test_", "test-", "none", "null", "os.getenv", "getenv(",
)


def _entropy_per_character(value: str) -> float:
    counts = Counter(value)
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    findings: list[str] = []
    files = _tracked_files()
    seen_pem_counts: dict[str, int] = {}

    for name in files:
        path = Path(name)
        basename = path.name.lower()
        if (basename.startswith(".env") and basename not in ALLOWED_ENV_EXAMPLES) or path.suffix.lower() in SENSITIVE_SUFFIXES:
            findings.append(f"{name}: tracked environment or private-key file")

        try:
            raw = (ROOT / path).read_bytes()
        except OSError:
            continue
        if b"\0" in raw or len(raw) > 8_000_000:
            continue

        pem_count = len(PRIVATE_KEY_MARKER.findall(raw))
        if pem_count:
            seen_pem_counts[name] = pem_count

        for label, pattern in MARKER_PATTERNS:
            for match in pattern.finditer(raw):
                line = raw.count(b"\n", 0, match.start()) + 1
                findings.append(f"{name}:{line}: possible {label}")

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            match = CREDENTIAL_ASSIGNMENT.search(line)
            if not match:
                continue
            value = match.group(2)
            normalized = value.lower()
            if any(marker in normalized for marker in PLACEHOLDER_MARKERS):
                continue
            if len(value) >= 24 and _entropy_per_character(value) >= 4.0:
                findings.append(f"{name}:{line_number}: possible high-entropy {match.group(1)}")

    for name, count in seen_pem_counts.items():
        if PEM_FIXTURE_COUNTS.get(name) != count:
            findings.append(f"{name}: unapproved private-key marker(s) in tracked content")
    for name, expected in PEM_FIXTURE_COUNTS.items():
        if seen_pem_counts.get(name, 0) != expected:
            findings.append(f"{name}: test PEM fixture count changed; review the fixture allowlist")

    if findings:
        print("Tracked secret hygiene check failed; matched values are suppressed:", file=sys.stderr)
        for finding in sorted(set(findings)):
            print(f"- {finding}", file=sys.stderr)
        return 1

    print(f"Tracked secret hygiene check passed ({len(files)} files; matched values suppressed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
