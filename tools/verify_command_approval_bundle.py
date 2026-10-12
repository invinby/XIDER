"""Inspect actual PyInstaller archives and run the service-free frozen probe."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

REQUIRED = {"command_module_loader", "command_signing", "command_replay", "shell_execution",
            "release_signature", "update_package",
            "cryptography.hazmat.primitives.asymmetric.ed25519",
            "winrt.windows.devices.geolocation",
            "winrt.windows.security.authorization.appcapabilityaccess"}
PRIVATE_KEY_BASENAMES = {"id_rsa", "id_ecdsa", "id_ed25519", "id_dsa"}
PRIVATE_PEM_MARKER = re.compile(rb"(?m)^-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?$")


def _contains_private_pem(archive, member):
    overlap = b""
    with archive.open(member) as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return False
            data = overlap + chunk
            if PRIVATE_PEM_MARKER.search(data):
                return True
            overlap = data[-256:]


def verify_executable(path):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(path))
    pyz_names = [name for name in archive.toc if name.endswith(".pyz")]
    if len(pyz_names) != 1:
        raise ValueError("Expected one embedded PYZ archive")
    pyz = archive.open_embedded_archive(pyz_names[0])
    missing = REQUIRED - pyz.toc.keys()
    if missing:
        raise ValueError("Missing bundled modules: " + ", ".join(sorted(missing)))
    with tempfile.TemporaryDirectory(prefix="xider-bundle-report-") as temporary:
        report = Path(temporary) / "probe.json"
        result = subprocess.run([str(Path(path).resolve()), "--command-approval-bundle-smoke", str(report)],
                                timeout=60, capture_output=True)
        if result.returncode != 0 or not report.is_file():
            detail = report.read_text(encoding="utf-8") if report.is_file() else "no probe report"
            raise ValueError("Frozen probe failed: " + detail)
        data = json.loads(report.read_text(encoding="utf-8"))
        if data.get("ok") is not True or set(data.get("modules", [])) != REQUIRED.intersection({
                "command_module_loader", "command_signing", "command_replay", "shell_execution"}):
            raise ValueError("Invalid frozen probe result")
        return data


def verify_public_only_zip(path):
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = Path(member.filename).name.lower()
            suffix = Path(name).suffix
            private_name = (name in PRIVATE_KEY_BASENAMES or "private-key" in name
                            or "private_key" in name or "signing-key" in name
                            or ("private" in name and "signing" in name))
            private_env = name == ".env" or (name.startswith(".env.")
                                               and name not in {".env.example", ".env.sample"})
            if private_env or name == "bot.env" or private_name:
                raise ValueError("Private configuration in package: " + member.filename)
            # Search every member, regardless of extension. A PEM-looking
            # fixture embedded inside a source-code string is not a PEM block
            # because the marker does not start a line by itself.
            if not member.is_dir() and _contains_private_pem(archive, member):
                raise ValueError("Private key material in package: " + member.filename)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--public-only-zip", type=Path, action="append", default=[])
    args = parser.parse_args()
    for path in args.public_only_zip:
        verify_public_only_zip(path)
    if args.executable:
        print(json.dumps(verify_executable(args.executable), sort_keys=True))
