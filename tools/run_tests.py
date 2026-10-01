#!/usr/bin/env python3
"""Run XIDER test suites in isolated pytest processes.

Components intentionally have local modules with shared names such as ``config``.
Separate pytest processes prevent imports from one component contaminating another.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ENV = {
    "SHARED_KEY": "xider-test-only-shared-key-0123456789",
    "BOT_TOKEN": "123456:TEST-TOKEN-NOT-REAL",
    "ADMIN_ID": "0",
    "MQTT_BROKER": "localhost",
    "MQTT_PORT": "8883",
    "MQTT_PREFIX": "xgent/test",
    "MQTT_TLS": "true",
    "MQTT_USERNAME": "xider-test-user",
    "MQTT_PASSWORD": "xider-test-password-not-real",
    "ENCRYPT_PAYLOAD": "false",
}
SUITES = (
    ("Telegram bot", ROOT / "TG-BOT-SERVER", "tests"),
    ("Windows agent", ROOT / "XGENT-WDS", "tests"),
    ("macOS agent", ROOT / "XGENT-MCS", "tests"),
    ("Operations", ROOT, "ops/tests"),
    ("Release tools", ROOT / "tools", "."),
    ("Deployment archive and server signatures", ROOT / "deploy", "tests"),
)
WINDOWS_FIXTURES = (
    ("Windows bootstrap preflight", "test_bootstrap_agent_preflight.ps1"),
    ("Windows full-setup preflight", "test_setup_all_preflight.ps1"),
    ("Windows installer selection", "test_install_agent.ps1"),
    ("Windows staged install and rollback", "test_bootstrap_agent_swap.ps1"),
    ("Windows full bootstrap transaction", "test_bootstrap_transaction.ps1"),
)
LINUX_FIXTURES = (
    ("Linux VPS update and rollback", "test_update_server.sh"),
    ("X-VAULT encrypted backup pipeline", "test_x_vault_pipeline.sh"),
    ("macOS LaunchAgent status", "test_macos_launch_status.sh"),
    ("macOS bootstrap transaction", "test_bootstrap_macos_transaction.sh"),
)


def run_platform_fixtures(env: dict[str, str]) -> list[str]:
    """Run platform-specific installer/rollback fixtures in isolated processes."""
    if os.name == "nt":
        shell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        fixtures = WINDOWS_FIXTURES
        prefix = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"] if shell else []
    elif sys.platform.startswith("linux"):
        shell = shutil.which("bash")
        fixtures = LINUX_FIXTURES
        prefix = [shell] if shell else []
    else:
        print("SKIP: deploy shell fixtures require Linux coreutils; run them in the Linux CI job.")
        return []

    if not shell:
        print("ERROR: required platform test runner is unavailable.")
        return ["deployment fixtures (runner unavailable)"]

    failures: list[str] = []
    for label, relative_path in fixtures:
        script = ROOT / "deploy" / "tests" / relative_path
        print(f"\n=== {label} ===", flush=True)
        result = subprocess.run(
            [*prefix, str(script)],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if result.returncode:
            failures.append(f"{label} (exit {result.returncode})")
    return failures


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

    failed.extend(run_platform_fixtures(env))

    print("\n=== XIDER test summary ===")
    if failed:
        print("FAILED: " + "; ".join(failed))
        return 1
    print(f"PASS: {len(SUITES)} isolated component suites and platform deployment fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
