"""Build source-free Windows Agent and Guard Keeper one-file binaries."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.obfuscation_build import REPORT_NAME, obfuscate_tree


WDS = ROOT / "XGENT-WDS"
MCS = ROOT / "XGENT-MCS"
TOOLS = ROOT / "tools"
OUTPUT = WDS / "dist" / "nuitka"
BUILD_PARENT = WDS / "build"
LEGACY_NUITKA_INTERMEDIATES = (
    "xgent_wds.build", "xgent_wds.dist", "xgent_wds.onefile-build",
    "xider_guardian_wds.build", "xider_guardian_wds.dist",
    "xider_guardian_wds.onefile-build",
)


def _hidden_imports() -> tuple[str, ...]:
    """Read the existing explicit hidden-module inventory without PyInstaller."""
    tree = ast.parse((WDS / "XGENT-WDS.spec").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "hiddenimports" for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("XGENT-WDS.spec hiddenimports must remain a literal string list")
            return tuple(dict.fromkeys((*value, "command_approval_bundle_smoke")))
    raise ValueError("Could not find the hiddenimports list in XGENT-WDS.spec")


def _build(entry: Path, output_dir: Path, output_name: str, modules: tuple[str, ...] = ()) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    # MinGW's archive contains deep paths; a short cache root avoids Windows
    # extraction failures under the default AppData cache path.
    environment.setdefault("NUITKA_CACHE_DIR", r"C:\NuitkaCache")
    search_paths = (entry.parent, MCS, TOOLS, WDS)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(path) for path in search_paths]
        + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
    )
    command = [
        sys.executable, "-m", "nuitka",
        "--mode=onefile",
        "--mingw64",
        "--assume-yes-for-downloads",
        "--jobs=2",
        "--windows-console-mode=disable",
        f"--output-dir={output_dir}",
        f"--output-filename={output_name}",
    ]
    if entry.name == "xgent_wds.py":
        command.extend((
            "--include-package=pyttsx3", "--include-module=soundfile",
            "--enable-plugin=tk-inter",
        ))
    command.extend(f"--include-module={name}" for name in modules)
    command.append(str(entry))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    artifact = output_dir / output_name
    if not artifact.is_file() or artifact.stat().st_size < 1024 * 1024:
        raise RuntimeError(f"Nuitka did not produce a plausible executable: {artifact}")
    return artifact


def _cleanup_legacy_nuitka_intermediates() -> None:
    """Remove only known Nuitka-generated legacy dirs after a successful build."""
    output_root = OUTPUT.resolve()
    for name in LEGACY_NUITKA_INTERMEDIATES:
        candidate = OUTPUT / name
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            resolved = candidate.resolve(strict=True)
            if resolved.parent != output_root:
                continue
            # Preserve any diagnostic/configuration payload if one was placed
            # in a legacy intermediate directory by a failed local build.
            if any(
                path.is_file() and (
                    path.suffix.casefold() in {".log", ".xml", ".dmp", ".crash"}
                    or path.name.casefold() == ".env"
                )
                for path in candidate.rglob("*")
            ):
                continue
            shutil.rmtree(candidate)
        except (OSError, RuntimeError):
            # A cleanup failure must not invalidate the just-built executables.
            continue


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Windows agent bundles must be built on Windows.")
    build_packages()
    return 0


def build_packages() -> tuple[Path, Path]:
    """Build in a fresh owned staging directory; keep failed stages for diagnosis."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    BUILD_PARENT.mkdir(parents=True, exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix="nuitka-windows-", dir=BUILD_PARENT))
    obfuscated_wds = build_root / "obfuscated-wds"
    report_path = build_root / REPORT_NAME
    report = obfuscate_tree(WDS, obfuscated_wds, report_path, platform="windows")
    report["components"] = [
        {"id": "windows_agent", "state": "applied", "source_tree": "XGENT-WDS"},
        {"id": "windows_guard_keeper", "state": "applied", "source_tree": "XGENT-WDS"},
    ]
    report["unobfuscated_first_party_scope"] = (
        "MCS helper modules loaded from XGENT-MCS and build-only helpers from tools; "
        "Nuitka compiles these modules but they are not counted as obfuscated."
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent = _build(
        obfuscated_wds / "xgent_wds.py", build_root / "agent", "XGENT-WDS.exe", _hidden_imports(),
    )
    guardian = _build(
        obfuscated_wds / "xider_guardian_wds.py", build_root / "guardian", "XIDER-Guard-Keeper.exe",
        ("update_package", "release_signature"),
    )
    # Copy only compiled executables into the release root. Nuitka's generated
    # C and distribution trees remain outside the upload directory.
    published_paths = []
    for artifact in (agent, guardian):
        destination = OUTPUT / artifact.name
        if artifact != destination:
            shutil.copy2(artifact, destination)
        published_paths.append(destination)
    shutil.copy2(report_path, OUTPUT / REPORT_NAME)
    # Both executables are safely copied. Remove only this invocation's fresh
    # Nuitka intermediates; failed staging remains available for diagnosis.
    try:
        shutil.rmtree(build_root)
    except OSError:
        print(f"Warning: successful build staging remains for cleanup: {build_root}")
    _cleanup_legacy_nuitka_intermediates()
    print(f"Built {published_paths[0]}")
    print(f"Built {published_paths[1]}")
    return published_paths[0], published_paths[1]


if __name__ == "__main__":
    raise SystemExit(main())
