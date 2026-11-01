"""Build source-free macOS Agent, Guard Keeper, and Safari/Siri bundles."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.obfuscation_build import REPORT_NAME, obfuscate_tree


MCS = ROOT / "XGENT-MCS"
OUTPUT = MCS / "dist" / "nuitka"
BUILD_ROOT = MCS / "build" / "nuitka-macos"
VERSION_FILE = MCS / "config.py"

AGENT_RESOURCES = {
    "NSLocationUsageDescription": (
        "XIDER checks one current location only after an explicit request and local OS permission. "
        "It does not track location in the background."
    ),
    "NSLocationWhenInUseUsageDescription": (
        "XIDER checks one current location only after an explicit request and local OS permission. "
        "It does not track location in the background."
    ),
    "NSCameraUsageDescription": "XIDER takes a camera snapshot after an explicit owner request.",
    "NSMicrophoneUsageDescription": "XIDER records audio after an explicit owner request.",
    "NSAppleEventsUsageDescription": "XIDER runs owner-requested scripts.",
}
NUITKA_PROTECTED_RESOURCE_KEYS = frozenset({
    "NSLocationWhenInUseUsageDescription",
    "NSCameraUsageDescription",
    "NSMicrophoneUsageDescription",
    "NSAppleEventsUsageDescription",
})


def _version() -> str:
    tree = ast.parse(VERSION_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value.count(".") == 2 and all(part.isdigit() for part in value.split(".")):
                return value
    raise ValueError("Could not read a numeric XGENT-MCS VERSION from config.py")


def _hidden_imports() -> tuple[str, ...]:
    """Reuse the explicit dynamic-import inventory from the existing spec."""
    tree = ast.parse((MCS / "XGENT-MCS.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "Analysis":
            continue
        keyword = next((item for item in node.keywords if item.arg == "hiddenimports"), None)
        if keyword is None:
            continue
        value = ast.literal_eval(keyword.value)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("XGENT-MCS.spec hiddenimports must remain a literal string list")
        return tuple(dict.fromkeys((*value, "command_approval_bundle_smoke")))
    raise ValueError("Could not find Analysis(hiddenimports=...) in XGENT-MCS.spec")


def _command(
    entry: Path,
    output_dir: Path,
    output_name: str,
    *,
    version: str,
    bundle_name: str,
    bundle_id: str,
    modules: tuple[str, ...] = (),
    protected_resources: dict[str, str] | None = None,
) -> list[str]:
    command = [
        sys.executable, "-m", "nuitka",
        "--mode=app",
        "--macos-app-console-mode=disable",
        f"--macos-app-name={bundle_name}",
        f"--macos-app-version={version}",
        "--macos-app-macos-min-version=12.0",
        "--jobs=2",
        f"--output-dir={output_dir}",
        f"--output-filename={output_name}",
        f"--company-name=XIDER",
        f"--product-name={bundle_name}",
        # sounddevice ships Windows PortAudio DLLs in its macOS wheel too.
        # Nuitka's DLL plugin otherwise tries to link those PE files into the
        # macOS app and aborts the arm64 build. macOS uses the package's .dylib.
        "--noinclude-dlls=*.dll",
    ]
    for key, description in (protected_resources or {}).items():
        if key not in NUITKA_PROTECTED_RESOURCE_KEYS:
            continue
        command.append(f"--macos-app-protected-resource={key}:{description}")
    command.extend(f"--include-module={module}" for module in modules)
    command.append(str(entry))
    return command


def _prepare_bundle(app: Path, *, bundle_name: str, bundle_id: str, executable: str,
                    version: str, resources: dict[str, str] | None = None) -> Path:
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    executable_path = app / "Contents" / "MacOS" / executable
    if not executable_path.is_file():
        raise RuntimeError(f"Nuitka bundle is missing its executable: {executable_path}")
    info.update({
        "CFBundleIdentifier": bundle_id,
        "CFBundleName": bundle_name,
        "CFBundleDisplayName": bundle_name,
        "CFBundleExecutable": executable,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSUIElement": True,
    })
    info.update(resources or {})
    with info_path.open("wb") as stream:
        plistlib.dump(info, stream, fmt=plistlib.FMT_XML, sort_keys=True)
    return executable_path


def _build(
    entry: Path,
    output_name: str,
    bundle_name: str,
    bundle_id: str,
    *,
    version: str,
    modules: tuple[str, ...] = (),
    resources: dict[str, str] | None = None,
    signing_identity: str = "",
    entitlements: Path | None = None,
) -> tuple[Path, Path]:
    target = BUILD_ROOT / bundle_name
    target.mkdir(parents=True, exist_ok=True)
    command = _command(
        entry, target, output_name, version=version, bundle_name=bundle_name,
        bundle_id=bundle_id, modules=modules, protected_resources=resources,
    )
    environment = os.environ.copy()
    search_paths = (entry.parent, MCS, ROOT / "tools")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(path) for path in search_paths]
        + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
    )
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    candidates = tuple(target.glob("*.app"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one Nuitka app bundle in {target}; found {len(candidates)}")
    app = candidates[0]
    expected_name = f"{bundle_name}.app"
    if app.name != expected_name:
        renamed = target / expected_name
        if renamed.exists():
            shutil.rmtree(renamed)
        app.rename(renamed)
        app = renamed
    executable = _prepare_bundle(
        app, bundle_name=bundle_name, bundle_id=bundle_id, executable=output_name,
        version=version, resources=resources,
    )
    if not executable.is_file() or executable.stat().st_size < 1024 * 1024:
        raise RuntimeError(f"Nuitka did not produce a plausible executable: {executable}")
    if signing_identity:
        args = ["codesign", "--force", "--deep", "--options", "runtime", "--timestamp",
                "--identifier", bundle_id]
        if entitlements:
            args.extend(("--entitlements", str(entitlements)))
        args.extend(("--sign", signing_identity, str(app)))
        subprocess.run(args, cwd=ROOT, check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], cwd=ROOT, check=True)
    return app, executable


def _verify_agent_executable(executable: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify_command_approval_bundle.py"),
         "--nuitka-executable", str(executable)],
        cwd=ROOT, check=True,
    )


def _assert_compiled_bundles_are_source_free(bundle_pairs) -> None:
    for _build_app, installed_app in bundle_pairs:
        unexpected = [
            path for path in installed_app.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".pyc", ".pyo"}
        ]
        if unexpected:
            raise RuntimeError(f"Compiled bundle contains Python source/bytecode: {unexpected[0]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsigned-preview", action="store_true",
                        help="build a non-distributable branch artifact without Apple signing")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("macOS bundles must be built on macOS.")
    identity = os.environ.get("XIDER_MACOS_SIGNING_IDENTITY", "").strip()
    if not args.unsigned_preview and not identity.startswith("Developer ID Application:"):
        raise SystemExit("A Developer ID Application identity is required outside unsigned preview builds.")
    version = _version()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    obfuscation_root = Path(tempfile.mkdtemp(prefix="obfuscated-mcs-", dir=BUILD_ROOT))
    obfuscated_mcs = obfuscation_root / "source"
    report_path = obfuscation_root / REPORT_NAME
    report = obfuscate_tree(MCS, obfuscated_mcs, report_path, platform="macos")
    report["components"] = [
        {"id": "macos_agent", "state": "applied", "source_tree": "XGENT-MCS"},
        {"id": "macos_guard_keeper", "state": "applied", "source_tree": "XGENT-MCS"},
        {"id": "safari", "state": "applied", "source_tree": "XGENT-MCS"},
        {"id": "siri", "state": "applied", "source_tree": "XGENT-MCS"},
    ]
    report["unobfuscated_first_party_scope"] = (
        "Build-only helper modules loaded from tools and third-party dependencies are not counted as obfuscated."
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent, executable = _build(
        obfuscated_mcs / "xgent_mcs.py", "XGENT-MCS", "XIDER", "com.xider.agent", version=version,
        modules=_hidden_imports(), resources=AGENT_RESOURCES,
        signing_identity=identity,
        entitlements=MCS / "location.entitlements",
    )
    guardian, _ = _build(
        obfuscated_mcs / "xider_guardian.py", "XIDER-Guard-Keeper", "XIDER-Guard-Keeper", "com.xider.guardian",
        version=version, signing_identity=identity,
    )
    layers, _ = _build(
        obfuscated_mcs / "xider_layers.py", "XIDER-Layers", "XIDER-Layers", "com.xider.layers",
        version=version, signing_identity=identity,
    )
    destinations = (
        (agent, OUTPUT / "XIDER.app"),
        (guardian, OUTPUT / "XIDER-Guard-Keeper.app"),
        (layers, OUTPUT / "XIDER-Layers.app"),
    )
    for source, destination in destinations:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)
    shutil.copy2(report_path, OUTPUT / REPORT_NAME)
    try:
        shutil.rmtree(obfuscation_root)
    except OSError:
        print(f"Warning: successful obfuscation staging remains for cleanup: {obfuscation_root}")
    if identity:
        subprocess.run([sys.executable, str(ROOT / "tools" / "verify_macos_bundle.py"),
                        str(OUTPUT / "XIDER.app")], cwd=ROOT, check=True)
    _verify_agent_executable(OUTPUT / "XIDER.app" / "Contents" / "MacOS" / "XGENT-MCS")
    _assert_compiled_bundles_are_source_free(destinations)
    mode = "signed" if identity else "unsigned preview"
    print(f"Built {mode} Nuitka bundles under {OUTPUT}; unsigned previews are not distributable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
