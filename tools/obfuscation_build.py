"""Reproducible PyObfus preflight and source-tree transformation for release builds."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys


PYOBFUS_VERSION = "0.11.1"
REPORT_NAME = "xider-obfuscation-report.json"
PYOBFUS_CONFIG = Path(__file__).with_name("pyobfus.yaml")


def obfuscate_tree(source: Path, destination: Path, report_path: Path, *, platform: str) -> dict:
    """Transform one first-party source tree and write a privacy-safe evidence report.

    The PyObfus private name mapping is deliberately not generated or shipped.
    Nuitka remains a separate compilation step and is never counted as obfuscation.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    report_path = Path(report_path).resolve()
    if not source.is_dir() or destination.exists():
        raise ValueError("Obfuscation requires an existing source directory and a fresh output directory.")
    installed_version = importlib.metadata.version("pyobfus")
    if installed_version != PYOBFUS_VERSION:
        raise RuntimeError(f"Expected pyobfus=={PYOBFUS_VERSION}, found {installed_version}.")
    if not PYOBFUS_CONFIG.is_file():
        raise RuntimeError(f"PyObfus project configuration is missing: {PYOBFUS_CONFIG}")

    check = subprocess.run(
        [sys.executable, "-m", "pyobfus", "--check", str(source), "--json", "--offline", "--no-config"],
        cwd=source.parent, check=False, capture_output=True, text=True,
    )
    try:
        preflight = json.loads(check.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("PyObfus preflight did not return valid JSON.") from exc
    if check.returncode or preflight.get("parse_errors") or preflight.get("severity_counts", {}).get("high", 0):
        raise RuntimeError("PyObfus preflight reported a parse error or high-risk construct.")

    raw_report = report_path.with_name("pyobfus-build-report.json")
    subprocess.run(
        [
            sys.executable, "-m", "pyobfus", str(source), "-o", str(destination),
            "--config", str(PYOBFUS_CONFIG),
            "--verify-syntax", "--verify-names", "--preserve-unproved-module-attributes",
            "--build-report", str(raw_report),
        ],
        cwd=source.parent, check=True,
    )
    try:
        build = json.loads(raw_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("PyObfus did not write a valid build report.") from exc
    verification = build.get("verification", {})
    name_verification = build.get("name_verification", {})
    transformations = build.get("transformations", {})
    outputs = build.get("outputs", {})
    files = outputs.get("files", [])
    names_obfuscated = transformations.get("total_names_obfuscated")
    if (
        build.get("format") != "pyobfus-build-report"
        or build.get("state") != "completed"
        or not verification.get("syntax_valid")
        or verification.get("execution_performed")
        or not name_verification.get("names_valid")
        or name_verification.get("execution_performed")
        or type(names_obfuscated) is not int
        or names_obfuscated < 1
        or not files
        or outputs.get("file_count") != len(files)
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or Path(item["path"]).is_absolute()
            or ".." in Path(item["path"]).parts
            or not (destination / item["path"]).is_file()
            for item in files
        )
    ):
        raise RuntimeError("PyObfus build evidence is incomplete or inconsistent.")

    report = {
        "schema": "xider.obfuscation-report.v1",
        "platform": platform,
        "source_tree": source.name,
        "state": "applied",
        "tool": {"name": "PyObfus", "version": PYOBFUS_VERSION, "edition": "Community"},
        "configuration": "cross-file; preserve dynamic command-approval public API; no private mapping shipped",
        "scope": "first-party Python modules in the named source tree; third-party packages are excluded",
        "source_files_processed": outputs["file_count"],
        "names_obfuscated": names_obfuscated,
        "local_names_obfuscated": transformations.get("local_names_obfuscated"),
        "syntax_verified": True,
        "name_binding_verified": True,
        "execution_performed_by_obfuscator": False,
        "preflight": {
            "files_scanned": preflight.get("files_scanned"),
            "severity_counts": preflight.get("severity_counts", {}),
            "parse_errors": 0,
            "high_risk_findings": 0,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raw_report.unlink(missing_ok=True)
    return report
