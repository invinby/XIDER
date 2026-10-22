# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

REPO_ROOT = Path(SPECPATH).resolve().parent
MAC_AGENT_DIR = REPO_ROOT / 'XGENT-MCS'

datas = []
binaries = []
hiddenimports = ['pystray._win32', 'comtypes.stream',
                 'cv2_enumerate_cameras', 'camera_series', 'microphone_devices', 'microphone_level', 'audio_output_control', 'media_history', 'media_request_control', 'dns_diagnostics', 'dns_settings', 'dns_comparison', 'dnssec_check', 'gateway_probe', 'route_trace', 'path_mtu', 'captive_portal', 'ipv6_connectivity', 'tls_certificate', 'service_probe', 'latency_series', 'packet_loss_history', 'jitter_measurement', 'network_throughput', 'network_link_speed', 'route_lookup', 'endpoint_batch', 'wifi_quality', 'wifi_channel', 'wifi_connection_history', 'proxy_status', 'vpn_route', 'network_traffic', 'network_traffic_schedule', 'interface_status_history',
                 'command_module_loader', 'command_signing', 'command_replay', 'shell_execution',
                 'cryptography.hazmat.primitives.asymmetric.ed25519',
                 'winrt.windows.devices.geolocation',
                 'winrt.windows.security.authorization.appcapabilityaccess']
tmp_ret = collect_all('soundfile')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyttsx3')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['xgent_wds.py'],
    pathex=[str(Path(SPECPATH).resolve()), str(MAC_AGENT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(REPO_ROOT / 'tools' / 'command_approval_bundle_smoke.py')],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='XGENT-WDS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
)
