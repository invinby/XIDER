#!/usr/bin/env python3
"""Run XIDER test suites in isolated pytest processes.

Components intentionally have local modules with shared names such as ``config``.
Separate pytest processes prevent imports from one component contaminating another.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ENV = {
    "SHARED_KEY": "xider-test-only-shared-key-0123456789",
    "BOT_TOKEN": "123456:TEST-TOKEN-NOT-REAL",
    "ADMIN_ID": "0",
    "MQTT_BROKER": "localhost",
    "MQTT_PORT": "1883",
    "MQTT_PREFIX": "xgent/test",
    "MQTT_TLS": "false",
    "ENCRYPT_PAYLOAD": "false",
}
SUITES = (
    ("Telegram bot", ROOT / "TG-BOT-SERVER", "tests"),
    ("Windows agent", ROOT / "XGENT-WDS", "tests"),
    ("macOS agent", ROOT / "XGENT-MCS", "tests"),
    ("Operations", ROOT, "ops/tests"),
    ("Release tools", ROOT / "tools", "test_build_release_manifest.py"),
)


def main() -> int:
    env = os.environ.copy()
    env.update(TEST_ENV)
    failed: list[str] = []

    for label, cwd, test_path in SUITES:
        print(f"\n=== {label} ===", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", test_path],
            cwd=cwd,
            env=env,
            check=False,
        )
        if result.returncode:
            failed.append(f"{label} (exit {result.returncode})")

    print("\n=== XIDER test summary ===")
    if failed:
        print("FAILED: " + "; ".join(failed))
        return 1
    print(f"PASS: all {len(SUITES)} isolated suites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
