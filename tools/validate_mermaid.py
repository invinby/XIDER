"""Parse every Mermaid fenced block using the pinned Mermaid CLI."""

from __future__ import annotations

import re
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"(?im)^```mermaid\s*\r?\n(.*?)^```\s*$", re.DOTALL)
OPEN = re.compile(r"(?im)^```mermaid\s*$")


def _puppeteer_args() -> list[str]:
    """Use Chromium's required hosted-runner workaround only in read-only CI."""
    if sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub's Ubuntu AppArmor policy blocks unprivileged user namespaces.
        # This CI job has read-only repo access and no secrets; local renders keep sandboxing.
        return ["--no-sandbox", "--disable-setuid-sandbox"]
    return []


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python tools/validate_mermaid.py FILE [FILE ...]")
        return 2
    cli = ROOT / "tools" / "node_modules" / ".bin" / ("mmdc.cmd" if sys.platform == "win32" else "mmdc")
    if not cli.is_file():
        print("Mermaid CLI is not installed; run npm ci --prefix tools")
        return 2
    failed = False
    for raw_path in argv:
        path = Path(raw_path).resolve()
        source = path.read_text(encoding="utf-8")
        blocks = list(FENCE.finditer(source))
        if len(blocks) != len(OPEN.findall(source)):
            start = OPEN.search(source)
            line = source.count("\n", 0, start.start()) + 1 if start else 1
            print(f"{path.name}:{line}: unclosed Mermaid fence")
            failed = True
            continue
        if not blocks:
            print(f"{path.name}: no Mermaid blocks")
            failed = True
            continue
        for index, block in enumerate(blocks, 1):
            line = source.count("\n", 0, block.start()) + 1
            with tempfile.TemporaryDirectory(prefix="xider-mermaid-") as directory:
                input_path = Path(directory) / "diagram.mmd"
                output_path = Path(directory) / "diagram.svg"
                input_path.write_text(block.group(1), encoding="utf-8")
                command = [str(cli), "-i", str(input_path), "-o", str(output_path), "--quiet"]
                puppeteer_args = _puppeteer_args()
                if puppeteer_args:
                    config_path = Path(directory) / "puppeteer.json"
                    config_path.write_text(json.dumps({"args": puppeteer_args}), encoding="utf-8")
                    command.extend(["--puppeteerConfigFile", str(config_path)])
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                )
            if result.returncode:
                detail = (result.stderr or result.stdout).strip().replace("\r", "")
                print(f"{path.name}:{line}: block {index}: {detail}")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
