"""Build source-free Windows Agent and Guard Keeper one-file binaries."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WDS = ROOT / "XGENT-WDS"
MCS = ROOT / "XGENT-MCS"
TOOLS = ROOT / "tools"
OUTPUT = WDS / "dist" / "nuitka"
BUILD_ROOT = WDS / "build" / "nuitka-windows"


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
    search_paths = (WDS, MCS, TOOLS)
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
        command.extend(("--include-package=pyttsx3", "--include-module=soundfile"))
    command.extend(f"--include-module={name}" for name in modules)
    command.append(str(entry))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    artifact = output_dir / output_name
    if not artifact.is_file() or artifact.stat().st_size < 1024 * 1024:
        raise RuntimeError(f"Nuitka did not produce a plausible executable: {artifact}")
    return artifact


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Windows agent bundles must be built on Windows.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    agent = _build(
        WDS / "xgent_wds.py", BUILD_ROOT / "agent", "XGENT-WDS.exe", _hidden_imports(),
    )
    guardian_dir = BUILD_ROOT / "guardian"
    guardian = _build(
        WDS / "xider_guardian_wds.py", guardian_dir, "XIDER-Guard-Keeper.exe",
        ("update_package", "release_signature"),
    )
    # Copy only compiled executables into the release root. Nuitka's generated
    # C and distribution trees remain outside the upload directory.
    for artifact in (agent, guardian):
        destination = OUTPUT / artifact.name
        if artifact != destination:
            shutil.copy2(artifact, destination)
    print(f"Built {OUTPUT / 'XGENT-WDS.exe'}")
    print(f"Built {OUTPUT / 'XIDER-Guard-Keeper.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
