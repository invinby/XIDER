#!/usr/bin/env python3
"""Reject metadata/signature gaps before calling a macOS bundle location-ready."""
import argparse
import pathlib
import plistlib
import re
import os
import subprocess

BUNDLE_ID = "com.xider.agent"

def verify_release_credentials(env):
    required = ("XIDER_MACOS_SIGNING_IDENTITY", "APPLE_CERTIFICATE_B64",
                "APPLE_CERTIFICATE_PASSWORD", "APPLE_KEYCHAIN_PASSWORD",
                "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD")
    errors = ["Required macOS release credential missing: " + key
              for key in required if not env.get(key, "").strip()]
    if not env.get("XIDER_MACOS_SIGNING_IDENTITY", "").startswith("Developer ID Application:"):
        errors.append("External release requires a Developer ID Application signing identity")
    return errors

def verify_bundle(app, *, distribution=False, run=subprocess.run):
    app = pathlib.Path(app)
    errors = []
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ["Missing or invalid Info.plist"]
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        errors.append("CFBundleIdentifier must be com.xider.agent")
    usage = info.get("NSLocationUsageDescription")
    if not isinstance(usage, str) or not usage.strip():
        errors.append("NSLocationUsageDescription must explain the one-shot request")
    executable = info.get("CFBundleExecutable")
    if (not isinstance(executable, str) or pathlib.Path(executable).name != executable
            or not (app / "Contents/MacOS" / executable).is_file()):
        errors.append("Missing bundled executable")
    try:
        verify = run(["codesign", "--verify", "--deep", "--strict", str(app)],
                     capture_output=True, text=True, timeout=30)
        if verify.returncode:
            errors.append("codesign --verify --deep --strict failed")
        display = run(["codesign", "-dv", "--verbose=4", str(app)],
                      capture_output=True, text=True, timeout=30)
        details = display.stdout + display.stderr
        if display.returncode:
            errors.append("Cannot inspect codesign identity")
        if "Signature=adhoc" in details or "flags=0x2(adhoc)" in details:
            errors.append("Ad-hoc signing is not accepted for Core Location")
        authorities = re.findall(r"^Authority=(.+)$", details, re.MULTILINE)
        if not any(a.startswith(("Apple Development:", "Developer ID Application:")) for a in authorities):
            errors.append("Proper Apple Development or Developer ID identity required")
        if not re.search(r"^TeamIdentifier=(?!not set$)\S+", details, re.MULTILINE):
            errors.append("Missing signing team")
        if not re.search(r"^Identifier=com\.xider\.agent$", details, re.MULTILINE):
            errors.append("Signature identifier does not match the stable app identity")
        if distribution:
            if not any(a.startswith("Developer ID Application:") for a in authorities):
                errors.append("External distribution requires Developer ID signing")
            stapled = run(["xcrun", "stapler", "validate", str(app)],
                          capture_output=True, text=True, timeout=30)
            if stapled.returncode:
                errors.append("External distribution requires a valid stapled notarization ticket")
    except (OSError, subprocess.TimeoutExpired):
        errors.append("macOS signing verification tools unavailable or timed out")
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=pathlib.Path, nargs="?")
    parser.add_argument("--distribution", action="store_true")
    parser.add_argument("--release-credentials", action="store_true")
    args = parser.parse_args()
    if args.release_credentials:
        errors = verify_release_credentials(os.environ)
    elif args.app is None:
        parser.error("app is required unless checking release credentials")
    else:
        errors = verify_bundle(args.app, distribution=args.distribution)
    for error in errors:
        print("BLOCKED: " + error)
    if not errors:
        print("PASS: bundle metadata/signature gate; physical location acceptance still required")
    return bool(errors)

if __name__ == "__main__":
    raise SystemExit(main())
