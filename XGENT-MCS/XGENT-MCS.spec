# -*- mode: python ; coding: utf-8 -*-
"""The location-enabled runtime must be a properly signed stable app bundle."""
import os
import re
from pathlib import Path

version = re.search(r'^VERSION = "([0-9.]+)"', (Path(SPECPATH) / "config.py").read_text(encoding="utf-8"),
                    re.MULTILINE).group(1)

identity = os.environ.get("XIDER_MACOS_SIGNING_IDENTITY", "")
if not identity.startswith(("Apple Development:", "Developer ID Application:")):
    raise ValueError("Core Location build blocked: proper non-ad-hoc signing identity required")

a = Analysis(
    ['xgent_mcs.py'], pathex=[], binaries=[], datas=[],
    hiddenimports=['paho.mqtt.client', 'psutil', 'cryptography',
                   'cryptography.hazmat.primitives.ciphers.aead',
                   'cryptography.hazmat.primitives.asymmetric.ed25519',
                   'command_module_loader', 'command_signing', 'command_replay',
                   'fake_update_screen', 'AppKit', 'Foundation', 'CoreLocation', 'location_adapter'],
    hookspath=[], hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH).resolve().parent / 'tools' / 'command_approval_bundle_smoke.py')],
    excludes=['tkinter', 'test', 'unittest'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name='XGENT-MCS',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=identity, entitlements_file='location.entitlements',
)
app = BUNDLE(
    exe, name='XIDER.app', bundle_identifier='com.xider.agent',
    info_plist={
        'CFBundleName': 'XIDER',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'LSUIElement': True,
        'NSLocationUsageDescription': 'XIDER checks one current location only after an explicit XIDER request and your local OS permission. It does not track location in the background.',
        'NSLocationWhenInUseUsageDescription': 'XIDER checks one current location only after an explicit XIDER request and your local OS permission. It does not track location in the background.',
        'NSCameraUsageDescription': 'XIDER takes a camera snapshot after an explicit request.',
        'NSMicrophoneUsageDescription': 'XIDER records audio after an explicit request.',
        'NSAppleEventsUsageDescription': 'XIDER runs owner-requested scripts.',
    },
)
