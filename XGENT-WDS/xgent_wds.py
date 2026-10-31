"""Клиент XGENT для Windows (Asus).

Подключается к публичному MQTT-брокеру, слушает команды бота
и выполняет их на этой машине. Прозрачная версия: в системном трее
всегда виден значок с пунктом «Остановить XGENT».
"""

import base64
import binascii
import csv
import ctypes
import ctypes.wintypes
import datetime
import html
import hashlib
import ipaddress
import importlib.machinery
import importlib.util
import io
import json
import locale
import logging
import math
import msvcrt
import os
from pathlib import Path
import random
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import time
import webbrowser
from urllib.parse import urlsplit
import winreg
import winsound
from logging.handlers import RotatingFileHandler
from concurrent.futures import Future

# Release build probe runs before importing device services or loading .env.
if len(sys.argv) > 1 and sys.argv[1] == "--command-approval-bundle-smoke":
    import command_approval_bundle_smoke  # noqa: F401 - exits after writing its report

from energy_usage import parse_srum_energy_csv
from sleep_wake_history import parse_windows_sleep_wake_events
from startup_duration import parse_windows_startup_duration
from pending_restart import parse_pending_restart_checks
from sysinfo_schedule import load_schedule, save_schedule
import camera_quality
import camera_devices
import media_temp
import agent_file_inventory
import display_contact_sheet
import dns_diagnostics
import dns_settings
import dns_comparison
import dnssec_check
import gateway_probe
import route_trace
import path_mtu
import captive_portal
import ipv6_connectivity
import tls_certificate
import service_probe
import latency_series
import packet_loss_history
import jitter_measurement
import network_throughput
import network_link_speed
import route_lookup
import endpoint_batch
import wifi_quality
import wifi_channel
import wifi_connection_history
import proxy_status
import vpn_route
import network_traffic
import interface_status_history
from network_traffic_schedule import load_schedule as load_network_traffic_schedule, save_schedule as save_network_traffic_schedule
import window_capture
from media_request_control import MEDIA_REQUEST_ACTIONS, MediaRequestRegistry

_NUITKA_RUNTIME = bool("__compiled__" in globals() and not getattr(sys, "frozen", False))
_BUNDLED_RUNTIME = bool(getattr(sys, "frozen", False) or _NUITKA_RUNTIME)

MAX_FILE_PUT_BYTES = 30 * 1024 * 1024
MAX_FILE_PUT_B64_CHARS = 4 * ((MAX_FILE_PUT_BYTES + 2) // 3)
MAX_SCREENSHOT_BYTES = 30 * 1024 * 1024
SCREENSHOT_TIMEOUT = 10.0
_SCREENSHOT_CAPTURE_LOCK = threading.Lock()
_OPEN_URL_OPERATION_TTL_SEC = 600.0
_OPEN_URL_OPERATION_LIMIT = 256
_VOLUME_HISTORY_MIN_SPAN_SECONDS = 6 * 60 * 60
_VOLUME_HISTORY_RETENTION_SECONDS = 90 * 24 * 60 * 60
_VOLUME_HISTORY: dict[str, list[dict[str, float | int]]] = {}
_VOLUME_HISTORY_LOCK = threading.Lock()
_MEMORY_PRESSURE_SOURCE = "windows_memory_resource_notification"
_THERMAL_SOURCE = "windows_wmi_MSAcpi_ThermalZoneTemperature"
_BATTERY_HEALTH_SOURCE = "windows_wmi_battery_capacity"
_BATTERY_CYCLE_SOURCE = "windows_wmi_battery_cycle_count"
_SLEEP_WAKE_SOURCE = "windows_system_power_events"
_STARTUP_DURATION_SOURCE = "windows_diagnostics_performance_event_100"
_PENDING_RESTART_SOURCE = "windows_pending_restart_registry"


def _read_pending_restart_snapshot() -> dict:
    """Check known reboot markers without returning raw registry values."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": _PENDING_RESTART_SOURCE, "pending": None, "reasons": [], "completeness": "unknown"}
    script = (
        "$lm=[Microsoft.Win32.Registry]::LocalMachine; "
        "function Test-RegKey([string]$path){try{$k=$lm.OpenSubKey($path,$false);if($null -eq $k){return $false};$k.Dispose();return $true}catch{return $null}}; "
        "function Test-RegValue([string]$path,[string]$name,[bool]$nonzero){try{$k=$lm.OpenSubKey($path,$false);if($null -eq $k){return $false};$v=$k.GetValue($name,$null);$k.Dispose();if($null -eq $v){return $false};if($nonzero){return [long]$v -ne 0};return @($v).Count -gt 0}catch{return $null}}; "
        "$checks=@{component_servicing=(Test-RegKey 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending'); "
        "windows_update=(Test-RegKey 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'); "
        "pending_file_operations=(Test-RegValue 'SYSTEM\\CurrentControlSet\\Control\\Session Manager' 'PendingFileRenameOperations' $false); "
        "update_exe_volatile=(Test-RegValue 'SOFTWARE\\Microsoft\\Updates' 'UpdateExeVolatile' $true)}; "
        "ConvertTo-Json -InputObject @{checks=$checks} -Depth 2 -Compress"
    )
    unknown = {"state": "unknown", "source": _PENDING_RESTART_SOURCE, "pending": None, "reasons": [], "completeness": "unknown"}
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 16 * 1024:
            return unknown
        return parse_pending_restart_checks(completed.stdout)
    except Exception:
        log.info("Windows pending-restart telemetry unavailable", exc_info=True)
        return unknown


def _read_os_support_identity() -> dict:
    """Read only Windows release/edition identity fields needed for lifecycle lookup."""
    unknown = {"state": "unknown", "family": "windows", "release": None, "edition": None}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {**unknown, "state": "unsupported"}
    script = (
        "$p=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'; "
        "[pscustomobject]@{product_name=[string]$p.ProductName;display_version=[string]$p.DisplayVersion;"
        "build=[string]$p.CurrentBuild;edition=[string]$p.EditionID}|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 4096:
            return unknown
        raw = json.loads(completed.stdout)
        if not isinstance(raw, dict):
            return unknown
        build = raw.get("build")
        build_number = int(build) if isinstance(build, str) and build.isdigit() else None
        version = "11" if build_number is not None and build_number >= 22000 else "10" if build_number else None
        release = raw.get("display_version")
        if not version or not isinstance(release, str) or not re.fullmatch(r"\d{2}H[12]", release):
            return unknown
        edition = {"Core": "Home", "CoreSingleLanguage": "Home", "Professional": "Pro"}.get(raw.get("edition"))
        if not edition:
            return unknown
        return {"state": "supported", "family": "windows", "release": f"{version}|{release}", "edition": edition}
    except Exception:
        log.info("Windows OS support identity unavailable", exc_info=True)
        return unknown


def _read_hardware_inventory_snapshot() -> dict:
    """Project only non-serial hardware model fields from fixed CIM classes."""
    unknown = {
        "state": "unknown", "manufacturer": None, "model": None, "cpu": None,
        "architecture": None, "memory_gib": None, "graphics": [],
    }
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {**unknown, "state": "unsupported"}
    script = (
        "$cs=Get-CimInstance -ClassName Win32_ComputerSystem; "
        "$cpu=Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1; "
        "$gpu=@(Get-CimInstance -ClassName Win32_VideoController | ForEach-Object {[string]$_.Name} | "
        "Where-Object {$_} | Select-Object -First 8); "
        "$arch=switch([int]$cpu.Architecture){0{'x86'} 9{'x64'} 12{'ARM64'} default{$null}}; "
        "[pscustomobject]@{manufacturer=[string]$cs.Manufacturer;model=[string]$cs.Model;"
        "cpu=[string]$cpu.Name;architecture=[string]$arch;memory_bytes=[string]$cs.TotalPhysicalMemory;"
        "graphics=$gpu}|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 8192:
            return unknown
        raw = json.loads(completed.stdout)
        if not isinstance(raw, dict):
            return unknown

        def bounded(value):
            if not isinstance(value, str):
                return None
            value = value.strip()
            return value if value and len(value) <= 120 and not any(ord(char) < 32 for char in value) else None

        memory_bytes = raw.get("memory_bytes")
        memory_bytes = int(memory_bytes) if isinstance(memory_bytes, str) and memory_bytes.isdigit() else None
        memory_gib = (memory_bytes + 2**29) // 2**30 if memory_bytes and memory_bytes > 0 else None
        graphics = raw.get("graphics", [])
        graphics = graphics[:8] if isinstance(graphics, list) else [graphics] if isinstance(graphics, str) else []
        graphics = [value for value in (bounded(item) for item in graphics) if value]
        result = {
            "state": "supported", "manufacturer": bounded(raw.get("manufacturer")),
            "model": bounded(raw.get("model")), "cpu": bounded(raw.get("cpu")),
            "architecture": bounded(raw.get("architecture")), "memory_gib": memory_gib,
            "graphics": sorted(set(graphics)),
        }
        if not any(value is not None for key, value in result.items() if key not in {"state", "graphics"}) and not graphics:
            return unknown
        return result
    except Exception:
        log.info("Windows hardware inventory unavailable", exc_info=True)
        return unknown


def _read_driver_inventory_snapshot() -> dict:
    """Read a bounded list of signed driver labels, excluding hardware identifiers."""
    unknown = {"state": "unknown", "drivers": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "drivers": []}
    script = (
        "$rows=@(Get-CimInstance -ClassName Win32_PnPSignedDriver | Select-Object -First 301 "
        "@{Name='device_name';Expression={[string]$_.DeviceName}},"
        "@{Name='provider';Expression={[string]$_.DriverProviderName}},"
        "@{Name='version';Expression={[string]$_.DriverVersion}},"
        "@{Name='driver_date';Expression={[string]$_.DriverDate}}); "
        "if($rows.Count -gt 300){exit 3}; "
        "ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 256 * 1024:
            return unknown
        raw = json.loads(completed.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 300 or any(not isinstance(row, dict) for row in raw):
            return unknown
        drivers = []
        for row in raw:
            item = {}
            for source, target in (("device_name", "device_name"), ("provider", "provider"),
                                   ("version", "version"), ("driver_date", "driver_date")):
                value = row.get(source)
                if not isinstance(value, str):
                    value = None
                elif len(value) > 160 or any(ord(char) < 32 for char in value):
                    return unknown
                else:
                    value = value.strip() or None
                item[target] = value
            if not item["device_name"]:
                return unknown
            drivers.append(item)
        return {"state": "supported", "drivers": drivers}
    except Exception:
        log.info("Windows driver inventory unavailable", exc_info=True)
        return unknown


def _read_installed_app_inventory_snapshot() -> dict:
    """Read a bounded allowlist from uninstall registry keys; omit paths and commands."""
    unknown = {"state": "unknown", "apps": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "apps": []}
    script = (
        "$paths=@('HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'); "
        "$rows=@(foreach($p in $paths){Get-ItemProperty -Path $p -ErrorAction SilentlyContinue | "
        "Where-Object {$_.DisplayName} | Select-Object @{Name='name';Expression={[string]$_.DisplayName}},"
        "@{Name='publisher';Expression={[string]$_.Publisher}},"
        "@{Name='version';Expression={[string]$_.DisplayVersion}}}); "
        "if($rows.Count -gt 500){exit 3}; ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 384 * 1024:
            return unknown
        raw = json.loads(completed.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 500 or any(not isinstance(row, dict) for row in raw):
            return unknown
        apps = []
        for row in raw:
            item = {}
            for field in ("name", "publisher", "version"):
                value = row.get(field)
                if not isinstance(value, str):
                    value = None
                elif len(value) > 160 or any(ord(char) < 32 for char in value):
                    return unknown
                else:
                    value = value.strip() or None
                item[field] = value
            if not item["name"]:
                return unknown
            apps.append(item)
        return {"state": "supported", "apps": apps}
    except Exception:
        log.info("Windows installed-app inventory unavailable", exc_info=True)
        return unknown


def _read_startup_inventory_snapshot() -> dict:
    """Project startup entry names/scopes only; never export command values or paths."""
    unknown = {"state": "unknown", "entries": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "entries": []}
    script = (
        "$specs=@(@{path='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';scope='user_run'},"
        "@{path='HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';scope='machine_run'},"
        "@{path='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce';scope='user_run'},"
        "@{path='HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce';scope='machine_run'}); "
        "$rows=@(foreach($spec in $specs){try{$item=Get-ItemProperty -LiteralPath $spec.path -ErrorAction Stop;"
        "foreach($prop in $item.PSObject.Properties){if(-not $prop.Name.StartsWith('PS')){"
        "[pscustomobject]@{name=[string]$prop.Name;scope=[string]$spec.scope}}}}catch{}}; "
        "$folders=@(@{path=([Environment]::GetFolderPath('Startup'));scope='user_folder'},"
        "@{path=([Environment]::GetFolderPath('CommonStartup'));scope='machine_folder'}); "
        "foreach($folder in $folders){if($folder.path){Get-ChildItem -LiteralPath $folder.path -Force -ErrorAction SilentlyContinue | "
        "ForEach-Object {[pscustomobject]@{name=[string]$_.BaseName;scope=[string]$folder.scope}}}}); "
        "if($rows.Count -gt 300){exit 3}; ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 96 * 1024:
            return unknown
        raw = json.loads(completed.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 300 or any(not isinstance(row, dict) for row in raw):
            return unknown
        allowed_scopes = {"user_run", "machine_run", "user_folder", "machine_folder"}
        entries = []
        for row in raw:
            name, scope = row.get("name"), row.get("scope")
            if (not isinstance(name, str) or not name.strip() or len(name) > 160
                    or any(ord(char) < 32 for char in name)
                    or scope not in allowed_scopes):
                return unknown
            entries.append({"name": name.strip(), "scope": scope})
        return {"state": "supported", "entries": entries}
    except Exception:
        log.info("Windows startup inventory unavailable", exc_info=True)
        return unknown


def _read_service_inventory_snapshot() -> dict:
    """Read bounded Win32_Service status fields without binary paths or service accounts."""
    unknown = {"state": "unknown", "services": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "services": []}
    script = (
        "$rows=@(Get-CimInstance -ClassName Win32_Service | Select-Object -First 501 "
        "@{Name='name';Expression={[string]$_.Name}},"
        "@{Name='state';Expression={[string]$_.State}},"
        "@{Name='start_type';Expression={[string]$_.StartMode}}); "
        "if($rows.Count -gt 500){exit 3}; ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 192 * 1024:
            return unknown
        raw = json.loads(completed.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 500 or any(not isinstance(row, dict) for row in raw):
            return unknown
        states = {"Running", "Stopped", "Paused", "Start Pending", "Stop Pending", "Continue Pending", "Pause Pending"}
        start_types = {"Boot", "System", "Auto", "Manual", "Disabled"}
        services = []
        for row in raw:
            name, state, start_type = row.get("name"), row.get("state"), row.get("start_type")
            if (not isinstance(name, str) or not name.strip() or len(name) > 160
                    or any(ord(char) < 32 for char in name)
                    or state not in states or start_type not in start_types):
                return unknown
            services.append({"name": name.strip(), "state": state, "start_type": start_type})
        return {"state": "supported", "services": services}
    except Exception:
        log.info("Windows service inventory unavailable", exc_info=True)
        return unknown


def _read_crash_summary_snapshot() -> dict:
    """Aggregate recent critical/error events locally; never serialize event text."""
    unknown = {"state": "unknown", "events": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "events": []}
    script = (
        "$since=(Get-Date).AddDays(-7); $rows=@(); "
        "foreach($log in @('System','Application')){$records=Get-WinEvent -FilterHashtable @{LogName=$log;Level=1,2;StartTime=$since} "
        "-MaxEvents 500 -ErrorAction SilentlyContinue; foreach($group in ($records | Group-Object ProviderName,Id,Level)){"
        "$e=$group.Group[0]; $rows += [pscustomobject]@{provider=[string]$e.ProviderName;event_id=[int]$e.Id;"
        "level=[int]$e.Level;count=[int]$group.Count}}}; "
        "$rows=@($rows | Sort-Object provider,event_id,level | Select-Object -First 101); "
        "if($rows.Count -gt 100){exit 3}; ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 48 * 1024:
            return unknown
        raw = json.loads(completed.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 100 or any(not isinstance(row, dict) for row in raw):
            return unknown
        events = []
        for row in raw:
            provider, event_id, level, count = row.get("provider"), row.get("event_id"), row.get("level"), row.get("count")
            if (not isinstance(provider, str) or not provider.strip() or len(provider) > 120
                    or any(ord(char) < 32 for char in provider)
                    or type(event_id) is not int or not 1 <= event_id <= 65535
                    or type(level) is not int or level not in {1, 2}
                    or type(count) is not int or not 1 <= count <= 500):
                return unknown
            events.append({"provider": provider.strip(), "event_id": event_id, "level": level, "count": count})
        return {"state": "supported", "events": events}
    except Exception:
        log.info("Windows crash summary unavailable", exc_info=True)
        return unknown


def _read_smart_trend_snapshot() -> dict:
    """Return bounded Windows reliability counters without exporting disk IDs."""
    unknown = {"state": "unknown", "source": "windows_storage_reliability_counters", "disks": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {**unknown, "state": "unsupported"}
    script = (
        "$ErrorActionPreference='Stop'; $rows=@(); "
        "$disks=@(Get-PhysicalDisk -ErrorAction Stop | Select-Object -First 32); "
        "foreach($disk in $disks){$counter=$null; try{$counter=$disk | Get-StorageReliabilityCounter -ErrorAction Stop}catch{continue}; "
        "if($counter){$rows += [pscustomobject]@{FriendlyName=[string]$disk.FriendlyName;MediaType=[string]$disk.MediaType;"
        "Temperature=$counter.Temperature;Wear=$counter.Wear;PowerOnHours=$counter.PowerOnHours;"
        "ReadErrorsTotal=$counter.ReadErrorsTotal;WriteErrorsTotal=$counter.WriteErrorsTotal}}}; "
        "ConvertTo-Json -InputObject @($rows) -Compress -Depth 3"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 32 * 1024:
            return unknown
        raw = json.loads(completed.stdout) if completed.stdout.strip() else []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 32:
            return unknown
        fields = {
            "Temperature": ("temperature_c", 0.0, 150.0),
            "Wear": ("wear_percent", 0.0, 100.0),
            "PowerOnHours": ("power_on_hours", 0.0, 1e12),
            "ReadErrorsTotal": ("read_errors_total", 0.0, 1e15),
            "WriteErrorsTotal": ("write_errors_total", 0.0, 1e15),
        }
        disks, counts = [], {}
        for row in raw:
            if not isinstance(row, dict):
                return unknown
            model, media = row.get("FriendlyName"), row.get("MediaType")
            if not isinstance(model, str) or not model.strip() or len(model.strip()) > 120:
                continue
            model = " ".join(model.split())
            media = media.strip() if isinstance(media, str) and len(media) <= 20 else "Unknown"
            base_label = f"{model} [{media or 'Unknown'}]"
            counts[base_label] = counts.get(base_label, 0) + 1
            label = base_label if counts[base_label] == 1 else f"{base_label} #{counts[base_label]}"
            counters = {}
            for source_name, (name, minimum, maximum) in fields.items():
                value = row.get(source_name)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                value = float(value)
                if value == value and minimum <= value <= maximum:
                    counters[name] = value
            if counters:
                disks.append({"label": label, "counters": counters})
        return {**unknown, "state": "supported" if disks else "unsupported", "disks": disks}
    except Exception:
        log.info("Windows SMART trend unavailable", exc_info=True)
        return unknown


def _read_volume_topology_snapshot() -> dict:
    """Map bounded Windows volume mount aliases to non-reversible local keys."""
    source = "windows_get_volume_partition_access_paths"
    unknown = {"state": "unknown", "source": source, "volumes": []}
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {**unknown, "state": "unsupported"}
    script = (
        "$ErrorActionPreference='Stop'; $rows=@(); "
        "foreach($partition in @(Get-Partition -ErrorAction Stop | Select-Object -First 128)){ "
        "try{$volume=$partition | Get-Volume -ErrorAction Stop}catch{continue}; "
        "if(-not $volume -or -not $volume.UniqueId){continue}; "
        "$paths=@($partition.AccessPaths | Where-Object { $_ -is [string] -and $_.Length -le 512 }); "
        "if($volume.DriveLetter){$paths += ([string]$volume.DriveLetter + ':\\')}; "
        "$rows += [pscustomobject]@{UniqueId=[string]$volume.UniqueId;AccessPaths=@($paths | Select-Object -Unique)} }; "
        "ConvertTo-Json -InputObject @($rows) -Compress -Depth 3"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 128 * 1024:
            return unknown
        raw = json.loads(completed.stdout) if completed.stdout.strip() else []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) > 128:
            return unknown
        grouped: dict[str, set[str]] = {}
        for row in raw:
            if not isinstance(row, dict):
                return unknown
            unique_id = row.get("UniqueId")
            if not isinstance(unique_id, str) or not unique_id or len(unique_id) > 1024:
                continue
            volume_key = "vol-" + hashlib.sha256(unique_id.encode("utf-8")).hexdigest()[:16]
            paths = row.get("AccessPaths")
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or len(paths) > 32:
                return unknown
            grouped.setdefault(volume_key, set())
            for path in paths:
                if (isinstance(path, str) and 2 <= len(path) <= 512
                        and path[0].isascii() and path[0].isalpha() and path[1:3] == ":\\"
                        and "\\x00" not in path
                        and not any(ord(char) < 32 for char in path)):
                    grouped[volume_key].add(path)
        volumes = [
            {"volume_key": key, "mount_paths": sorted(paths)[:16], "state": "supported"}
            for key, paths in sorted(grouped.items()) if paths
        ][:64]
        return {**unknown, "state": "supported" if volumes else "unknown", "volumes": volumes}
    except Exception:
        log.info("Windows volume topology unavailable", exc_info=True)
        return unknown


def _read_clock_drift_snapshot() -> dict:
    """Estimate wall-clock steps against monotonic elapsed time over one second."""
    source = "wall_vs_monotonic_elapsed"
    try:
        wall_start = time.time_ns()
        monotonic_start = time.perf_counter_ns()
        time.sleep(1.0)
        wall_end = time.time_ns()
        monotonic_end = time.perf_counter_ns()
        wall_elapsed = wall_end - wall_start
        monotonic_elapsed = monotonic_end - monotonic_start
        sample_ms = round(monotonic_elapsed / 1_000_000)
        if (wall_elapsed <= 0 or monotonic_elapsed <= 0
                or not 500 <= sample_ms <= 5000):
            return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}
        drift_ms = round((wall_elapsed - monotonic_elapsed) / 1_000_000)
        return {"state": "supported", "source": source,
                "drift_ms": drift_ms, "sample_ms": sample_ms}
    except Exception:
        log.info("Windows local clock comparison unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "drift_ms": None, "sample_ms": None}


def _read_startup_duration_snapshot() -> dict:
    """Read only the measured BootTime field from the latest event 100."""
    powershell = shutil.which("powershell.exe")
    unknown = {"state": "unknown", "source": _STARTUP_DURATION_SOURCE, "duration_ms": None, "completed_at": None}
    if not powershell:
        return {**unknown, "state": "unsupported"}
    script = (
        "$event=Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational';Id=100} -MaxEvents 1 -ErrorAction SilentlyContinue; "
        "if(-not $event){'{}'; exit 0}; $xml=[xml]$event.ToXml(); "
        "$node=$xml.Event.EventData.Data | Where-Object { $_.Name -eq 'BootTime' } | Select-Object -First 1; "
        "$duration=0; if($node -and [long]::TryParse([string]$node.InnerText,[ref]$duration) -and $duration -gt 0){ "
        "ConvertTo-Json -InputObject @{duration_ms=$duration;completed_at=$event.TimeCreated.ToUniversalTime().ToString('o')} -Compress "
        "}else{'{}'}"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 32 * 1024:
            return unknown
        return parse_windows_startup_duration(completed.stdout)
    except Exception:
        log.info("Windows measured boot duration unavailable", exc_info=True)
        return unknown


def _read_sleep_wake_history_snapshot() -> dict:
    """Read only allowlisted sleep/resume event IDs from the System log."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}
    script = (
        "$start=(Get-Date).AddDays(-7); "
        "$kp=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=@(42,107);StartTime=$start} -MaxEvents 60 -ErrorAction SilentlyContinue | "
        "Select-Object -Property Id,ProviderName,@{Name='TimeCreated';Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}}); "
        "$pt=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Power-Troubleshooter';Id=1;StartTime=$start} -MaxEvents 60 -ErrorAction SilentlyContinue | "
        "Select-Object -Property Id,ProviderName,@{Name='TimeCreated';Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}}); "
        "$rows=@($kp)+@($pt); ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 1024 * 1024:
            return {"state": "unknown", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}
        return parse_windows_sleep_wake_events(completed.stdout)
    except Exception:
        log.info("Windows sleep/wake telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": _SLEEP_WAKE_SOURCE, "history_days": 7, "events": []}


def _read_battery_health_snapshot() -> dict:
    """Compare WMI design/full-charge capacities without using current charge percent."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}
    script = (
        "$design = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,DesignedCapacity); "
        "$full = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,FullChargedCapacity); "
        "ConvertTo-Json -InputObject @{design=$design;full=$full} -Depth 3 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}
        raw = json.loads(completed.stdout or "null")
        if not isinstance(raw, dict):
            return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}
        designs = raw.get("design") if isinstance(raw.get("design"), list) else [raw.get("design")]
        fulls = raw.get("full") if isinstance(raw.get("full"), list) else [raw.get("full")]
        design_by_name = {}
        for row in designs:
            if not isinstance(row, dict) or not isinstance(row.get("InstanceName"), str):
                continue
            value = row.get("DesignedCapacity")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10_000_000:
                continue
            design_by_name[row["InstanceName"].casefold()] = value
        batteries = []
        for row in fulls:
            if not isinstance(row, dict) or not isinstance(row.get("InstanceName"), str):
                continue
            key = row["InstanceName"].casefold()
            design = design_by_name.get(key)
            full = row.get("FullChargedCapacity")
            if (design is None or isinstance(full, bool) or not isinstance(full, int)
                    or not 0 < full < 10_000_000):
                continue
            health = full * 100.0 / design
            if not math.isfinite(health) or not 1.0 <= health <= 150.0:
                continue
            batteries.append({
                "label": f"Battery {len(batteries) + 1}",
                "design_capacity_mwh": design,
                "full_charge_capacity_mwh": full,
                "health_percent": round(health, 1),
                "quality": "native_provider",
            })
        if batteries:
            state = "supported"
        elif not designs and not fulls:
            state = "unsupported"
        else:
            state = "unknown"
        return {"state": state, "source": _BATTERY_HEALTH_SOURCE, "batteries": batteries}
    except Exception:
        log.info("Windows battery capacity telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": _BATTERY_HEALTH_SOURCE, "batteries": []}


def _read_battery_cycle_snapshot() -> dict:
    """Read cycle counts only when the Windows WMI provider exposes a positive counter."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
    script = (
        "$rows = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,CycleCount); "
        "ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        raw = json.loads(completed.stdout or "null")
        if raw is None or raw == []:
            return {"state": "unsupported", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        source_rows = raw if isinstance(raw, list) else [raw]
        rows = [row for row in source_rows[:8] if isinstance(row, dict) and isinstance(row.get("InstanceName"), str)]
        if not rows:
            return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}
        batteries = []
        for row in rows:
            count = row.get("CycleCount")
            if isinstance(count, bool) or not isinstance(count, int) or not 0 < count <= 1_000_000:
                continue
            batteries.append({
                "label": f"Battery {len(batteries) + 1}",
                "cycle_count": count,
                "quality": "native_provider",
            })
        if batteries:
            state = "supported"
        elif rows and all(type(row.get("CycleCount")) is int and row.get("CycleCount") == 0 for row in rows):
            # BATTERY_WMI_CYCLE_COUNT uses zero to mean the counter is unsupported.
            state = "unsupported"
        elif not rows:
            state = "unsupported"
        else:
            state = "unknown"
        return {"state": state, "source": _BATTERY_CYCLE_SOURCE, "batteries": batteries}
    except Exception:
        log.info("Windows battery cycle telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": _BATTERY_CYCLE_SOURCE, "batteries": []}


def _read_energy_usage_snapshot() -> dict:
    """Export SRUM only for this explicit request and return no raw rows or IDs."""
    source = "windows_srum_energy_estimation"
    powercfg = shutil.which("powercfg.exe") or shutil.which("powercfg")
    if not powercfg:
        return {"state": "unsupported", "source": source, "apps": []}
    export_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="xider-energy-", suffix=".csv", delete=False) as handle:
            export_path = handle.name
        completed = subprocess.run(
            [powercfg, "/srumutil", "/output", export_path, "/csv"],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not os.path.isfile(export_path):
            return {"state": "unknown", "source": source, "apps": []}
        if os.path.getsize(export_path) > 8 * 1024 * 1024:
            return {"state": "unknown", "source": source, "apps": []}
        with open(export_path, "rb") as report:
            raw = report.read(8 * 1024 * 1024 + 1)
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16", errors="strict")
        else:
            text = raw.decode("utf-8-sig", errors="strict")
        return parse_srum_energy_csv(text)
    except (OSError, UnicodeError, subprocess.SubprocessError, csv.Error, ValueError):
        log.info("Windows SRUM energy estimate unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "apps": []}
    finally:
        if export_path:
            try:
                os.unlink(export_path)
            except OSError:
                log.warning("Unable to remove temporary SRUM export file")


def _read_thermal_snapshot() -> dict:
    """Read only ACPI thermal-zone values when Windows exposes that WMI provider."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": _THERMAL_SOURCE, "sensors": []}
    script = (
        "$rows = @(Get-CimInstance -Namespace root/wmi -ClassName "
        "MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
        "Select-Object -First 32 -Property CurrentTemperature); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": _THERMAL_SOURCE, "sensors": []}
        try:
            raw = json.loads(completed.stdout or "null")
        except (TypeError, json.JSONDecodeError):
            return {"state": "unknown", "source": _THERMAL_SOURCE, "sensors": []}
        rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        if not rows:
            return {"state": "unsupported", "source": _THERMAL_SOURCE, "sensors": []}
        sensors = []
        for row in rows[:32]:
            value = row.get("CurrentTemperature") if isinstance(row, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            celsius = float(value) / 10.0 - 273.15
            if -40.0 <= celsius <= 150.0:
                sensors.append({
                    "label": f"Thermal zone {len(sensors) + 1}",
                    "temperature_c": round(celsius, 1), "quality": "native_provider",
                })
        if not sensors:
            return {"state": "unknown", "source": _THERMAL_SOURCE, "sensors": []}
        return {"state": "supported", "source": _THERMAL_SOURCE, "sensors": sensors}
    except Exception:
        log.info("Windows ACPI thermal-zone telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": _THERMAL_SOURCE, "sensors": []}


def _read_memory_pressure_sample() -> dict:
    """Read Windows low/high memory-resource notifications; do not infer pressure from RAM percentage."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMemoryResourceNotification.argtypes = [ctypes.c_int]
        kernel32.CreateMemoryResourceNotification.restype = ctypes.c_void_p
        kernel32.QueryMemoryResourceNotification.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.BOOL)]
        kernel32.QueryMemoryResourceNotification.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        low_handle = kernel32.CreateMemoryResourceNotification(0)
        high_handle = kernel32.CreateMemoryResourceNotification(1)
        if not low_handle or not high_handle:
            if low_handle:
                kernel32.CloseHandle(low_handle)
            if high_handle:
                kernel32.CloseHandle(high_handle)
            return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        try:
            low = ctypes.wintypes.BOOL()
            high = ctypes.wintypes.BOOL()
            if (not kernel32.QueryMemoryResourceNotification(low_handle, ctypes.byref(low))
                    or not kernel32.QueryMemoryResourceNotification(high_handle, ctypes.byref(high))):
                return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
            # Neither notification means the system is between thresholds, not necessarily "normal".
            state = "low" if low.value else "high" if high.value else "intermediate"
            return {"state": state, "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        finally:
            kernel32.CloseHandle(low_handle)
            kernel32.CloseHandle(high_handle)
    except Exception:
        log.info("Windows memory-resource notification unavailable", exc_info=True)
        return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}


def _memory_pressure_history_sample(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("source") != _MEMORY_PRESSURE_SOURCE:
        return {"state": "unknown", "available_percent": None}
    state = raw.get("state")
    if state not in {"low", "intermediate", "high"} or raw.get("available_percent") is not None:
        return {"state": "unknown", "available_percent": None}
    return {"state": state, "available_percent": None}


def _volume_history_measurement(device: str, mountpoint: str, total: int, used: int, now: float) -> dict:
    """Compare a live filesystem reading with bounded in-process observations."""
    key = f"{device[:256]}\0{mountpoint[:512]}"
    with _VOLUME_HISTORY_LOCK:
        if key not in _VOLUME_HISTORY and len(_VOLUME_HISTORY) >= 256:
            oldest_key = min(
                _VOLUME_HISTORY,
                key=lambda item: max((float(sample.get("at", 0)) for sample in _VOLUME_HISTORY[item]), default=0),
            )
            _VOLUME_HISTORY.pop(oldest_key, None)
        history = _VOLUME_HISTORY.setdefault(key, [])
        history[:] = [
            row for row in history
            if isinstance(row, dict) and 0 <= now - float(row.get("at", -1)) <= _VOLUME_HISTORY_RETENTION_SECONDS
        ]
        previous_capacity = any(
            row.get("total") != total and now - float(row.get("at", now)) >= _VOLUME_HISTORY_MIN_SPAN_SECONDS
            for row in history
        )
        baseline = max(
            (row for row in history
             if row.get("total") == total
             and _VOLUME_HISTORY_MIN_SPAN_SECONDS <= now - float(row.get("at", now)) <= _VOLUME_HISTORY_RETENTION_SECONDS),
            key=lambda row: float(row.get("at", 0)), default=None,
        )
        if not history or now - float(history[-1].get("at", now)) >= 300:
            history.append({"at": now, "total": total, "used": used})
            del history[:-128]
        row = {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": total - used,
            "used_percent": round(used * 100.0 / total, 1),
            "trend_state": "capacity_changed" if previous_capacity and baseline is None else "insufficient_history",
            "observed_span_seconds": None,
            "delta_used_bytes": None,
            "used_bytes_per_hour": None,
            "estimated_hours_to_full": None,
            "history_scope": "agent_process",
        }
        if baseline is not None:
            elapsed = now - float(baseline["at"])
            delta = used - int(baseline["used"])
            row["observed_span_seconds"] = int(elapsed)
            row["delta_used_bytes"] = delta
            if delta > 0:
                rate = delta * 3600.0 / elapsed
                row.update(
                    trend_state="increasing",
                    used_bytes_per_hour=round(rate),
                    estimated_hours_to_full=round(max(0, total - used) / rate, 1) if rate > 0 else None,
                )
            elif delta < 0:
                row["trend_state"] = "decreasing"
                row["used_bytes_per_hour"] = round(delta * 3600.0 / elapsed)
            else:
                row["trend_state"] = "stable"
                row["used_bytes_per_hour"] = 0
        return row


def _bounded_screen_grab():
    """At most one native call; a late image is closed after the caller deadline."""
    from PIL import ImageGrab
    if not _SCREENSHOT_CAPTURE_LOCK.acquire(blocking=False):
        raise TimeoutError("Previous native screen capture is still running")
    result = Future()

    def capture():
        try:
            result.set_result(ImageGrab.grab(all_screens=True))
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            _SCREENSHOT_CAPTURE_LOCK.release()

    def discard_late_image(completed):
        if completed.exception() is None:
            image = completed.result()
            if image is not None:
                try:
                    image.close()
                except Exception:
                    log.warning("Could not close late screenshot")

    try:
        threading.Thread(target=capture, name="xider-screen-capture", daemon=True).start()
    except BaseException:
        _SCREENSHOT_CAPTURE_LOCK.release()
        raise
    try:
        return result.result(timeout=SCREENSHOT_TIMEOUT)
    except TimeoutError:
        result.add_done_callback(discard_late_image)
        raise


def _screenshot_session_available() -> bool:
    """Read-only desktop access check; never opens consent or changes sessions."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        try:
            return True
        finally:
            user32.CloseDesktop(desktop)
    except (OSError, AttributeError):
        return False


def _validated_open_url(payload: dict) -> tuple[str, int]:
    url = payload.get("url")
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("URL is empty or too long")
    if any(char in url for char in "\r\n\t\x00"):
        raise ValueError("URL contains control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or not parsed.netloc:
        raise ValueError("Only HTTP(S) URLs with a host are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not allowed")
    if any(char.isspace() for char in parsed.netloc):
        raise ValueError("Whitespace in URL host is not allowed")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port is invalid")
    count = payload.get("count", 1)
    if type(count) is not int or count not in {1, 2, 3, 5}:
        raise ValueError("Open count must be one of 1, 2, 3, or 5")
    return url, count


def _decode_file_put_payload(payload: dict) -> bytes:
    """Decode a bounded, canonical base64 file payload before touching disk."""
    if "b64" in payload:
        encoded = payload["b64"]
    elif "data" in payload:
        encoded = payload["data"]
    else:
        raise ValueError("В запросе отсутствуют данные файла.")
    if not isinstance(encoded, str):
        raise ValueError("Данные файла должны быть строкой base64.")
    if len(encoded) > MAX_FILE_PUT_B64_CHARS:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Повреждены данные base64.") from exc
    if len(content) > MAX_FILE_PUT_BYTES:
        raise ValueError("Файл превышает лимит 30 МиБ.")
    return content


def _write_file_put_atomic(path: str, content: bytes) -> None:
    """Replace the destination only after the complete temporary file is written."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".xider-upload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary_path, path)
        temporary_path = ""
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass

_bootstrap_lock_file = None
EARLY_UPDATE_STATE = "none"


def _claim_bootstrap_lock() -> bool:
    """Hold the shared per-user lock before loading replaceable agent modules."""
    global _bootstrap_lock_file
    try:
        lock_path = Path.home() / ".xgent" / "agent.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            handle.close()
        except (NameError, OSError):
            pass
        return False
    _bootstrap_lock_file = handle
    return True


if __name__ == "__main__" and not _BUNDLED_RUNTIME:
    if not _claim_bootstrap_lock():
        sys.stderr.write("FATAL: XGENT-WDS уже запущен; второй экземпляр остановлен.\n")
        raise SystemExit(1)
    _repo_root = Path(__file__).resolve().parent.parent
    _release_module_dir = str(_repo_root / "XGENT-MCS")
    if _release_module_dir not in sys.path:
        sys.path.append(_release_module_dir)

import paho.mqtt.client as mqtt
import psutil

if not _BUNDLED_RUNTIME:
    _repo_root = Path(__file__).resolve().parent.parent
    _release_module_dir = str(_repo_root / "XGENT-MCS")
    if _release_module_dir not in sys.path:
        # Newer signed helpers staged beside this worker must take precedence.
        sys.path.append(_release_module_dir)
from release_signature import trusted_release_keys_ready, verify_manifest_signature
if not _BUNDLED_RUNTIME:
    from update_package import (
        UPDATE_STATE_NAME,
        download_verified_source_archive,
        extract_agent_files,
        install_agent_files,
        mark_agent_update_healthy,
        prepare_agent_update_start,
        rollback_unhealthy_agent,
        read_agent_update_result,
        update_operation_id,
    )

    if __name__ == "__main__":
        EARLY_UPDATE_STATE = prepare_agent_update_start(
            Path.home() / ".xgent" / UPDATE_STATE_NAME,
            Path(__file__).resolve().parent,
        )
        if EARLY_UPDATE_STATE in {"recovered", "rolled_back"}:
            message = (
                "XIDER-WDS: установка прервалась; восстановлены прежние файлы.\n"
                if EARLY_UPDATE_STATE == "recovered"
                else "XIDER-WDS: новая версия не прошла проверку; возвращена прежняя.\n"
            )
            sys.stderr.write(message)
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

from config import (
    CONFIG_DIR,
    set_guardian_desired_running,
    set_guardian_startup_enabled,
    DEFAULT_SHARED_KEY,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    HEARTBEAT_INTERVAL,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_TRANSPORT,
    MQTT_WS_PATH,
    MQTT_USERNAME,
    PLATFORM,
    SHARED_KEY,
    VERSION,
    write_runtime_health,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xgent.wds")

_PRANK_TEMP_PREFIX = "xider-prank-"
_PRANK_TEMP_TTL_SECONDS = 60


def _remove_prank_temp_file(path: str) -> None:
    target = Path(path)
    if not target.name.startswith(_PRANK_TEMP_PREFIX) or target.suffix.lower() != ".html":
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        log.warning("Unable to remove expired XIDER prank page")


def _schedule_prank_temp_cleanup(path: str) -> None:
    target = Path(path)
    if not target.name.startswith(_PRANK_TEMP_PREFIX) or target.suffix.lower() != ".html":
        return
    timer = threading.Timer(_PRANK_TEMP_TTL_SECONDS, _remove_prank_temp_file, args=(str(target),))
    timer.daemon = True
    timer.start()
MEDIA_REQUESTS = MediaRequestRegistry()
WINDOWS_TASK_NAME = "XIDER Agent"
WINDOWS_GUARDIAN_TASK_NAME = "XIDER Guardian"
_instance_lock_file = None


def acquire_instance_lock() -> bool:
    """X-LOCK: only one interactive Windows agent per user session.

    The OS releases this byte-range lock if the process exits unexpectedly.
    All installations use the same user config directory, so an old desktop
    copy and a new LocalAppData copy cannot both own the tray once updated.
    """
    global _instance_lock_file
    if _bootstrap_lock_file is not None:
        _instance_lock_file = _bootstrap_lock_file
        return True
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CONFIG_DIR / "agent.lock"
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _instance_lock_file = handle
    return True


def _scheduled_task_exists(task_name: str = WINDOWS_TASK_NAME) -> bool:
    """Проверить реальный Scheduled Task, которым устанавливается агент."""
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def _remove_scheduled_task(
    task_name: str = WINDOWS_TASK_NAME, *, stop_running: bool = True,
) -> tuple[bool, str]:
    """Remove one known XIDER task and confirm it is no longer registered.

    The worker calls this for its own task while handling the uninstall
    acknowledgement. ``schtasks /end`` would kill that same Python process
    before it can publish the correlated response, so callers can unregister
    a running task without interrupting its current process.
    """
    try:
        if stop_running:
            subprocess.run(
                ["schtasks.exe", "/End", "/TN", task_name],
                capture_output=True, text=True, check=False,
            )
        delete = subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        return False, str(exc)

    detail = (delete.stdout or delete.stderr or "").strip()
    if delete.returncode == 0:
        return True, detail
    # Task Scheduler output is localized, so use its query result rather than
    # matching English/Russian "task not found" messages.
    if not _scheduled_task_exists(task_name):
        return True, detail
    return False, detail or f"Scheduled Task is still registered: {task_name}"

# Команды, которые умеет выполнять этот клиент. Используются и для
# маршрутизации, и для отчёта о возможностях (capabilities).
SUPPORTED_COMMANDS = (
    "open_url",
    "notify",
    "sound",
    "status_request",
    "screenshot",
    "display_catalog",
    "display_resolution",
    "display_contact_sheet",
    "display_screenshot",
    "display_region_screenshot",
    "window_catalog",
    "window_screenshot",
    "media_temp_cleanup",
    "media_history",
    "webcam",
    "camera_series",
    "media_cancel",
    "camera_catalog",
    "camera_quality",
    "microphone_catalog",
    "microphone_level",
    "microphone_balance",
    "audio_output_catalog",
    "audio_output_switch",
    "microphone_test",
    "microphone_preview",
    "sysinfo",
    "sysinfo_interval",
    "network_traffic_interval",
    "cpu_history",
    "memory_history",
    "memory_pressure_history",
    "thermal_snapshot",
    "battery_health",
    "battery_cycles",
    "energy_usage",
    "sleep_wake_history",
    "startup_duration",
    "pending_restart",
    "os_support",
    "hardware_inventory",
    "driver_inventory",
    "installed_app_inventory",
    "startup_inventory",
    "service_inventory",
    "crash_summary",
    "smart_trend",
    "volume_topology",
    "clock_drift",
    "volume_history",
    "processes",
    "battery",
    "geo_location",
    "shell", "shell_cancel",
    "network",
    "services",
    "clipboard",
    "clipboard_set",
    "kill_process",
    "disks",
    "volume_set",
    "mic",
    "open_app",
    "capabilities",
    "permission_settings",
    "lock",
    "volume_toggle",
    "power",
    "stop",
    "agent_logs",
    "agent_files",
    "agent_file_get",
    "dir_list",
    "file_get",
    "file_put",
    "file_del",
    "find_file",
    "path_open",
    "ext_ip",
    "screen_off",
    "screensaver_on",
    "wallpaper_set",
    "msgbox_spam",
    "type_text",
    "hotkey",
    "proc_kill_name",
    "wifi_info",
    "usb_devices",
    "startup_list",
    "env_get",
    "netstat",
    "download_url",
    "prank_screamer",
    "prank_rickroll",
    "prank_matrix",
    "prank_siren",
    "prank_shout_tts",
    "prank_swap_mouse",
    "prank_crazy_cursor",
    "prank_hide_desktop",
    "prank_dancing_windows",
    "prank_black_screen",
    "prank_random_site",
    "display_brightness",
    "display_night_light",
    "display_rotate",
    "net_wifi_passwords",
    "net_bluetooth_list",
    "storage_smart",
    "sys_installed_apps",
    "sys_history_cmd",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep",
    "wake",
    "autorun_status",
    "autorun_enable",
    "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime",
    "sys_clean_temp",
    "net_ping",
    "dns_resolution",
    "dns_settings",
    "dns_comparison",
    "dnssec_check",
    "gateway_probe",
    "route_trace",
    "path_mtu",
    "captive_portal",
    "ipv6_connectivity",
    "tls_certificate",
    "service_probe",
    "latency_series",
    "packet_loss_history",
    "network_jitter",
    "network_throughput",
    "network_link_speed",
    "route_lookup",
    "endpoint_batch",
    "wifi_quality_history",
    "wifi_channel_summary",
    "wifi_connection_history",
    "proxy_status",
    "vpn_route_status",
    "network_traffic",
    "interface_status_history",
    # Расширенная матрица приколов (40 приколов)
    "prank_bsod",
    "prank_fake_update",
    "prank_toast_spam",
    "prank_keyboard_disco",
    "prank_beep_morse",
    "prank_open_notepad_type",
    "prank_hacker_typer",
    "prank_sound_spooky",
    "prank_sound_fart",
    "prank_minimize_all",
    "prank_open_calc_spam",
    "prank_invert_screen",
    "prank_slow_mouse",
    "prank_random_clicks",
    "prank_paste_clipboard_spam",
    "prank_type_reversed",
    "prank_volume_jump",
    "prank_say_whisper",
    "prank_fake_virus",
    "prank_open_cd",
    "prank_change_wallpaper",
    "prank_restore_wallpaper",
    "prank_speak_time",
    "prank_rickroll_terminal",
    "prank_screen_off_brief",
    "prank_alert_loop",
    "prank_open_browser_memes",
    "prank_glitch_cursor",
    "prank_shake_window",
    # Новые мега-приколы и троллинг (до 55 приколов)
    "prank_meme_wallpaper",
    "prank_caps_disco",
    "prank_cursor_circle",
    "prank_fake_error_spam",
    "prank_fake_delete_sys32",
    "prank_ghost_typer",
    "prank_fbi_lock",
    "prank_cat_invaders",
    "prank_fake_ransom_cats",
    "prank_nyan_stream",
    "prank_random_beeps",
    "prank_confetti_winner",
    "prank_low_battery_fake",
    "prank_earthquake",
    "prank_laugh_track",
    "prank_stop_all",
    # Обновление и безопасность
    "agent_update",
    "uninstall_agent",
)



# =====================================================================
#  Чистые функции форматирования (тестируются без MQTT/оборудования)
# =====================================================================

def _humanize_seconds(secs):
    """Человекочитаемое время из секунд: '5ч 32мин' / '12мин' / None."""
    if secs is None:
        return None
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return None
    if secs < 0:
        return None
    hours, remainder = divmod(secs, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


def _format_battery(battery):
    """Приводит psutil.sbattery (или None) к словарю для MQTT.

    Возвращает {"available": False} если батареи нет.
    """
    if battery is None:
        return {"available": False}
    secsleft = battery.secsleft
    if secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        secsleft = None
    plugged = bool(battery.power_plugged)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "source": "psutil.sensors_battery",
        "sampled_at": int(time.time()),
        "power_plugged": plugged,
        "secsleft": int(secsleft) if secsleft is not None else None,
        "time_left": _humanize_seconds(secsleft),
        "state": "charging" if plugged else "on_battery",
    }


def _format_network(addrs, stats, io):
    """Сводка по сетевым интерфейсам и суммарному трафику."""
    interfaces = []
    for name in sorted(addrs.keys()):
        nic_addrs = addrs[name]
        nic_stats = stats.get(name)
        ipv4 = ipv6 = mac = None
        for a in nic_addrs:
            if a.family == socket.AF_INET:
                ipv4 = a.address
            elif a.family == socket.AF_INET6:
                ipv6 = a.address.split("%")[0]
            elif a.family == psutil.AF_LINK:
                mac = a.address
        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
            "up": bool(nic_stats.isup) if nic_stats else False,
            "speed_mbps": nic_stats.speed if nic_stats else 0,
        })
    totals = {"bytes_sent": 0, "bytes_recv": 0}
    for counters in (io or {}).values():
        totals["bytes_sent"] += counters.bytes_sent
        totals["bytes_recv"] += counters.bytes_recv
    return {"interfaces": interfaces, "totals": totals}


def _format_services(services):
    """Сводка по службам Windows: счётчики и auto-start, которые не запущены.

    Только чтение. Не запускает и не останавливает службы.
    """
    total = 0
    running = 0
    failed_auto = []
    for svc in services:
        total += 1
        try:
            status = svc.status()
        except Exception:
            status = "unknown"
        if status == "running":
            running += 1
            continue
        try:
            start_type = svc.start_type()
        except Exception:
            start_type = "unknown"
        if start_type == "auto":
            failed_auto.append({
                "name": svc.name(),
                "display_name": svc.display_name(),
                "status": status,
            })
    return {
        "total": total,
        "running": running,
        "stopped": total - running,
        "failed_auto_start": failed_auto,
    }


def _format_processes(procs, top_n=5):
    """Топ-N процессов по памяти. Возвращает совместимые с ботом `lines`

    и структурированный `top` для будущих клиентов.
    """
    items = []
    for proc in procs:
        try:
            info = proc.info
        except Exception:
            continue
        name = info.get("name")
        mem = info.get("memory_percent") or 0
        items.append((name, mem, info.get("pid")))
    items.sort(key=lambda x: (x[1] or 0), reverse=True)
    top = items[:top_n]
    lines = [
        f"{name}: {mem:.1f}%" for name, mem, _ in top if name is not None
    ]
    structured = [
        {"name": n, "pid": pid, "mem_percent": round(m, 1)}
        for n, m, pid in top if n is not None
    ]
    return {"lines": lines, "top": structured}


def _primary_mac():
    """Первый валидный MAC из сетевых интерфейсов (для Wake-on-LAN)."""
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac = (addr.address or "").strip()
                if mac and mac.lower().replace("-", ":") != "00:00:00:00:00:00":
                    return mac
    return None


def _format_disks(partitions):
    """Список дисков с размерами (чистая функция — тестируется)."""
    disks = []
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent": round(usage.percent, 1),
        })
    return disks


def _regional_settings_snapshot(request_id: object = None) -> dict:
    """Read timezone/locale once without changing the process or OS settings."""
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    timezone_data = {"state": "unknown", "source": "python_localtime", "name": None, "utc_offset_minutes": None}
    locale_data = {"state": "unknown", "source": "python_runtime_locale", "name": None}
    try:
        local_now = datetime.datetime.now().astimezone()
        name = local_now.tzname()
        offset = local_now.utcoffset()
        offset_minutes = int(offset.total_seconds() // 60) if offset is not None else None
        if isinstance(name, str) and name.strip():
            timezone_data["name"] = name.strip()[:128]
        if offset_minutes is not None and -840 <= offset_minutes <= 840:
            timezone_data["utc_offset_minutes"] = offset_minutes
        if timezone_data["name"] is not None or timezone_data["utc_offset_minutes"] is not None:
            timezone_data["state"] = "supported"
    except (OSError, OverflowError, ValueError):
        pass
    try:
        language, _encoding = locale.getlocale()
        if isinstance(language, str) and language.strip():
            locale_data.update(state="supported", name=language.strip()[:128])
    except (TypeError, ValueError, locale.Error):
        pass
    return {
        "schema": 1,
        "feature_id": "D01-F23",
        "device_id": DEVICE_ID,
        "request_id": request_id if isinstance(request_id, str) and re.fullmatch(r"[0-9a-fA-F]{8,64}", request_id) else None,
        "source": "local_os_runtime_apis",
        "state": "supported" if "supported" in {timezone_data["state"], locale_data["state"]} else "unknown",
        "checked_at": checked_at,
        "data": {"timezone": timezone_data, "locale": locale_data},
    }


def _list_directory(path: str) -> dict:
    """Чистая функция: список файлов папки (имя, тип, размер)."""
    entries = []
    base = os.path.expandvars(os.path.expanduser(path or "."))
    try:
        for entry in sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower())):
            item = {"name": entry.name, "is_dir": entry.is_dir()}
            if not item["is_dir"]:
                try:
                    item["size"] = entry.stat().st_size
                except OSError:
                    item["size"] = 0
            entries.append(item)
        return {"ok": True, "path": base, "files": entries[:100], "total": len(entries)}
    except OSError as exc:
        return {"ok": False, "path": base, "error": str(exc), "files": [], "total": 0}


def _find_matches(pattern: str, root: str, limit: int = 30, max_depth: int = 4) -> dict:
    """Чистая функция: поиск файлов по подстроке имени (без сторонних зависимостей)."""
    matches = []
    for current, dirs, files in os.walk(os.path.expanduser(root or "~")):
        depth = current[len(root):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
            continue
        for name in files:
            if pattern.lower() in name.lower():
                matches.append(os.path.join(current, name))
                if len(matches) >= limit:
                    return {"ok": True, "matches": matches, "total": len(matches)}
    return {"ok": True, "matches": matches, "total": len(matches)}


def map_hotkeys(spec: str) -> list[int]:
    """Разобрать 'ctrl+shift+esc' → список VK-кодов (для keybd_event)."""
    VK_MAP = {
        "win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
        "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20,
        "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    }
    keys = []
    for raw in (spec or "").lower().split("+"):
        raw = raw.strip()
        if not raw:
            continue
        if raw in VK_MAP:
            keys.append(VK_MAP[raw])
        elif len(raw) == 1 and raw.isalnum():
            keys.append(ord(raw.upper()))
        elif re.fullmatch(r"f[1-9]|f1[0-2]", raw):
            keys.append(0x70 + int(raw[1:]) - 1)
        else:
            raise ValueError(f"unknown key: {raw}")
    return keys


# ---------- волна 2: диагностика и удалённые операции ----------

def _parse_wifi_output(text: str) -> dict:
    """Чистая функция: SSID и сигнал из netsh wlan show interfaces."""
    ssid = signal = None
    for line in (text or "").splitlines():
        low = line.lower()
        if ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if "ssid" in low and "bssid" not in low and value:
            ssid = value
        if ("signal" in low or "сигнал" in low) and value:
            signal = value
    return {"ssid": ssid, "signal": signal}


def _parse_reg_query(text: str) -> list[str]:
    """Чистая функция: имена записей autostart из reg query."""
    names = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("HKEY_") and "REG_SZ" in line:
            name = line.split("    ")[0].strip()
            if name:
                names.append(name)
    return names


def _collect_env(names: list[str] | None = None) -> dict:
    """Чистая функция: выбрать переменные окружения (без значений паролей)."""
    SAFE_KEYS = (
        "PATH", "USERNAME", "USER", "HOME", "USERPROFILE", "COMPUTERNAME",
        "HOSTNAME", "OS", "TEMP", "TMP", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "LANG", "SHELL", "PYTHON_HOME",
    )
    wanted = [n.upper() for n in (names or SAFE_KEYS)]
    result = {}
    for key, value in os.environ.items():
        if key.upper() in wanted:
            result[key] = value if len(value) < 400 else value[:397] + "..."
    return result


def _kill_by_name(name: str) -> dict:
    """Stop matching user-visible processes and wait for each stopped PID to exit."""
    low = (name or "").strip().lower()
    if not low:
        return {"ok": False, "state": "invalid", "error": "empty name", "count": 0, "killed": [],
                "text": "Не указано имя процесса; ничего не завершено."}
    killed, errors = [], 0
    matched = 0
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] == me:
                continue
            pname = (proc.info.get("name") or "").lower()
            if low in pname:
                matched += 1
                proc.kill()
                proc.wait(timeout=3)
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, psutil.TimeoutExpired):
            errors += 1
    if not matched:
        return {"ok": False, "state": "not_found", "count": 0, "killed": [], "errors": 0,
                "text": "Совпадений не найдено; ничего не завершено."}
    if errors:
        state = "partial" if killed else "failed"
        return {"ok": False, "state": state, "count": len(killed), "killed": killed[:20], "errors": errors,
                "text": f"Завершено и проверено: {len(killed)}; не подтверждено: {errors}. Результат частичный."}
    return {"ok": True, "state": "completed", "count": len(killed), "killed": killed[:20], "errors": 0,
            "text": f"Завершено и подтверждено: {len(killed)} процесс(ов)."}


# ---------- хелперы: экран, сеть, диски, софт (чистые, тестируемые) ----------

def _clamp(value, lo, hi, default):
    """Привести value к int в [lo..hi]; при ошибке — default."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _orientation_value(angle) -> int | None:
    """Угол поворота экрана → DMDO_* (0,1,2,3). None если угол не кратен 90°."""
    try:
        a = int(angle) % 360
    except (TypeError, ValueError):
        return None
    return {0: 0, 90: 1, 180: 2, 270: 3}.get(a)


def _parse_wifi_profile_names(text: str) -> list[str]:
    """Имена Wi-Fi профилей из `netsh wlan show profiles` (ru/en)."""
    names: list[str] = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        low = line.lower()
        if "profile" not in low and "профил" not in low:
            continue
        value = line.split(":", 1)[1].strip()
        if value and value not in names:
            names.append(value)
    return names


def _parse_key_content(text: str) -> str | None:
    """Пароль (Key Content) из вывода `netsh wlan show profile ... key=clear`."""
    for line in (text or "").splitlines():
        low = line.lower()
        if ("key content" in low or "содержимое ключа" in low) and ":" in line:
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


# --- Night Light: reverse-engineered CloudStore blob (nightlight-cli, MIT) ---

NL_STATE_SUBKEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)


def _night_light_enabled(data: bytes) -> bool:
    """True, если Night Light включён (по байту состояния)."""
    return bool(data) and len(data) > 18 and data[18] == 0x15


def _night_light_toggle_bytes(data: bytes) -> bytes:
    """Построить новый CloudStore-blob с переключённым состоянием.

    Раскладка взята из nightlight-cli (реверс-инжиниринг NightLight.cs):
    байт [18] == 0x15 — включено, 0x13 — выключено; при переключении длина
    блоба меняется (41/43 байта), счётчик обновляется в байтах 10..14.
    """
    raw = bytearray(data)
    enabled = _night_light_enabled(raw)
    if enabled:
        new_data = bytearray(41)
        new_data[0:22] = raw[0:22]
        if len(raw) > 25:
            copy_len = min(len(raw) - 25, 43 - 25)
            new_data[23:23 + copy_len] = raw[25:25 + copy_len]
        new_data[18] = 0x13
    else:
        new_data = bytearray(43)
        new_data[0:22] = raw[0:22]
        if len(raw) > 23:
            copy_len = min(len(raw) - 23, 41 - 23)
            new_data[25:25 + copy_len] = raw[23:23 + copy_len]
        new_data[18] = 0x15
        new_data[23] = 0x10
        new_data[24] = 0x00
    for i in range(10, 15):
        if new_data[i] != 0xFF:
            new_data[i] = (new_data[i] + 1) % 256
            break
    return bytes(new_data)


# --- DEVMODE (ChangeDisplaySettingsEx) для поворота экрана ---

class _POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _DEVMODEW(ctypes.Structure):
    """Минимальный DEVMODE для смены ориентации дисплея (WinAPI wingdi.h)."""
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPosition", _POINTL),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


DM_DISPLAYORIENTATION = 0x00000080
CDS_RESET = 0x00000004
DISP_CHANGE_SUCCESSFUL = 0


def _rotate_screen(angle: int) -> tuple[bool, str]:
    """Повернуть экран через ChangeDisplaySettingsEx. angle: 0/90/180/270."""
    orient = _orientation_value(angle)
    if orient is None:
        return False, f"Некорректный угол: {angle!r} (нужно 0/90/180/270)"
    try:
        user32 = ctypes.windll.user32
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return False, "EnumDisplaySettings не удался"

        is_current_portrait = (dm.dmDisplayOrientation in (1, 3))
        is_target_portrait = (orient in (1, 3))
        if is_current_portrait != is_target_portrait:
            dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth
            dm.dmFields = DM_DISPLAYORIENTATION | 0x00080000 | 0x00100000  # DM_PELSWIDTH | DM_PELSHEIGHT
        else:
            dm.dmFields = DM_DISPLAYORIENTATION

        dm.dmDisplayOrientation = orient
        res = user32.ChangeDisplaySettingsExW(
            None, ctypes.byref(dm), None, CDS_RESET, None
        )
        if res != DISP_CHANGE_SUCCESSFUL:
            return False, f"ChangeDisplaySettingsEx вернул {res}"
        return True, f"Экран повёрнут на {angle}°"
    except Exception as exc:  # noqa: BLE001
        return False, f"Ошибка поворота экрана: {exc}"


def _read_current_display_angle() -> int | None:
    """Return the current primary-display orientation in degrees, if available."""
    try:
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return None
        return {0: 0, 1: 90, 2: 180, 3: 270}.get(int(dm.dmDisplayOrientation))
    except Exception:
        return None


# =====================================================================
#  Иконка трея и клиент
# =====================================================================

def create_image():
    """Иконка для трея: синий круг с белой кнопкой «play» (64x64)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(30, 144, 255, 255))
    draw.ellipse((9, 9, 55, 55), outline=(255, 255, 255, 255), width=3)
    draw.polygon([(28, 21), (28, 43), (48, 32)], fill=(255, 255, 255, 255))
    return img


class XgentClient:
    """MQTT-клиент: подписка на команды, heartbeat и выполнение действий."""

    def __init__(self, on_stop_requested=None) -> None:
        self.on_stop_requested = on_stop_requested
        self._running = threading.Event()
        self._running.set()
        self._update_health = threading.Event()
        self._mqtt_connected = False
        self._mqtt_session_id = uuid.uuid4().hex
        self._subscription_lock = threading.RLock()
        self._pending_command_subacks = set()
        self._command_subscriptions_failed = False
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xgent-wds-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
            transport=MQTT_TRANSPORT,
        )
        if MQTT_TRANSPORT == "websockets":
            self._client.ws_set_options(path=MQTT_WS_PATH)
        # Экспоненциальный бэкофф переподключения (1с -> 120с).
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="xgent-heartbeat", daemon=True
        )
        self._sysinfo_schedule_wake = threading.Event()
        self._sysinfo_schedule = threading.Thread(
            target=self._sysinfo_schedule_loop, name="xgent-sysinfo-schedule", daemon=True,
        )
        self._network_traffic_schedule_wake = threading.Event()
        self._network_traffic_schedule = threading.Thread(
            target=self._network_traffic_schedule_loop, name="xgent-network-traffic-schedule", daemon=True,
        )
        self._interface_status_history_wake = threading.Event()
        self._interface_status_history_thread = threading.Thread(
            target=self._interface_status_history_loop, name="xgent-interface-status-history", daemon=True,
        )
        self._standby = False
        self._command_context = threading.local()
        self._active_prank_cancels: dict[str, object] = {}
        self._active_prank_cancels_lock = threading.RLock()
        self._open_url_operations: dict[str, dict] = {}
        self._open_url_operations_lock = threading.Lock()
        self._handlers = {
            "open_url": self._do_open_url,
            "notify": self._do_notify,
            "sound": self._do_sound,
            "status_request": self._do_status_request,
            "screenshot": self._do_screenshot,
            "display_catalog": self._do_display_catalog,
            "display_resolution": self._do_display_resolution,
            "display_contact_sheet": self._do_display_contact_sheet,
            "display_screenshot": self._do_display_screenshot,
            "display_region_screenshot": self._do_display_region_screenshot,
            "window_catalog": self._do_window_catalog,
            "window_screenshot": self._do_window_screenshot,
            "media_temp_cleanup": self._do_media_temp_cleanup,
            "media_history": self._do_media_history,
            "webcam": self._do_webcam,
            "camera_series": self._do_camera_series,
            "media_cancel": self._do_media_cancel,
            "shell_cancel": self._do_shell_cancel,
            "camera_catalog": self._do_camera_catalog,
            "microphone_catalog": self._do_microphone_catalog,
            "microphone_level": self._do_microphone_level,
            "microphone_balance": self._do_microphone_balance,
            "audio_output_catalog": self._do_audio_output_catalog,
            "audio_output_switch": self._do_audio_output_switch,
            "microphone_test": self._do_microphone_test,
            "microphone_preview": self._do_microphone_preview,
            "camera_quality": self._do_camera_quality,
            "sysinfo": self._do_sysinfo,
            "sysinfo_interval": self._do_sysinfo_interval,
            "network_traffic_interval": self._do_network_traffic_interval,
            "cpu_history": self._do_cpu_history,
            "memory_history": self._do_memory_history,
            "memory_pressure_history": self._do_memory_pressure_history,
            "thermal_snapshot": self._do_thermal_snapshot,
            "battery_health": self._do_battery_health,
            "battery_cycles": self._do_battery_cycles,
            "energy_usage": self._do_energy_usage,
            "sleep_wake_history": self._do_sleep_wake_history,
            "startup_duration": self._do_startup_duration,
            "pending_restart": self._do_pending_restart,
            "os_support": self._do_os_support,
            "hardware_inventory": self._do_hardware_inventory,
            "driver_inventory": self._do_driver_inventory,
            "installed_app_inventory": self._do_installed_app_inventory,
            "startup_inventory": self._do_startup_inventory,
            "service_inventory": self._do_service_inventory,
            "crash_summary": self._do_crash_summary,
            "smart_trend": self._do_smart_trend,
            "volume_topology": self._do_volume_topology,
            "clock_drift": self._do_clock_drift,
            "volume_history": self._do_volume_history,
            "processes": self._do_processes,
            "battery": self._do_battery,
            "geo_location": self._do_geo_location,
            "shell": self._do_shell,
            "network": self._do_network,
            "services": self._do_services,
            "clipboard": self._do_clipboard,
            "clipboard_set": self._do_clipboard_set,
            "kill_process": self._do_kill_process,
            "disks": self._do_disks,
            "volume_set": self._do_volume_set,
            "mic": self._do_mic,
            "open_app": self._do_open_app,
            "capabilities": self._do_capabilities,
            "agent_logs": self._do_agent_logs,
            "agent_files": self._do_agent_files,
            "agent_file_get": self._do_agent_file_get,
            "permission_settings": self._do_permission_settings,
            "lock": self._do_lock_screen,
            "volume_toggle": self._do_volume_toggle,
            "power": self._do_power,
            "stop": self._do_stop,
            "dir_list": self._do_dir_list,
            "file_get": self._do_file_get,
            "file_put": self._do_file_put,
            "file_del": self._do_file_del,
            "find_file": self._do_find_file,
            "path_open": self._do_path_open,
            "ext_ip": self._do_ext_ip,
            "screen_off": self._do_screen_off,
            "screensaver_on": self._do_screensaver_on,
            "wallpaper_set": self._do_wallpaper_set,
            "msgbox_spam": self._do_msgbox_spam,
            "type_text": self._do_type_text,
            "hotkey": self._do_hotkey,
            "proc_kill_name": self._do_proc_kill_name,
            "wifi_info": self._do_wifi_info,
            "usb_devices": self._do_usb_devices,
            "startup_list": self._do_startup_list,
            "env_get": self._do_env_get,
            "netstat": self._do_netstat,
            "download_url": self._do_download_url,
            "prank_screamer": self._do_prank_screamer,
            "prank_rickroll": self._do_prank_rickroll,
            "prank_matrix": self._do_prank_matrix,
            "prank_siren": self._do_prank_siren,
            "prank_shout_tts": self._do_prank_shout_tts,
            "prank_swap_mouse": self._do_prank_swap_mouse,
            "prank_crazy_cursor": self._do_prank_crazy_cursor,
            "prank_hide_desktop": self._do_prank_hide_desktop,
            "prank_dancing_windows": self._do_prank_dancing_windows,
            "prank_black_screen": self._do_prank_black_screen,
            "prank_random_site": self._do_prank_random_site,
            "display_brightness": self._do_display_brightness,
            "display_night_light": self._do_display_night_light,
            "display_rotate": self._do_display_rotate,
            "net_wifi_passwords": self._do_net_wifi_passwords,
            "net_bluetooth_list": self._do_net_bluetooth_list,
            "storage_smart": self._do_storage_smart,
            "sys_installed_apps": self._do_sys_installed_apps,
            "sys_history_cmd": self._do_sys_history_cmd,
            # Режим ожидания (Watchdog) и автозапуск
            "standby_sleep": self._do_standby_sleep,
            "wake": self._do_wake,
            "autorun_status": self._do_autorun_status,
            "autorun_enable": self._do_autorun_enable,
            "autorun_disable": self._do_autorun_disable,
            # Системные утилиты и диагностика
            "sys_uptime": self._do_sys_uptime,
            "sys_clean_temp": self._do_sys_clean_temp,
            "net_ping": self._do_net_ping,
            "dns_resolution": self._do_dns_resolution,
            "dns_settings": self._do_dns_settings,
            "dns_comparison": self._do_dns_comparison,
            "dnssec_check": self._do_dnssec_check,
            "gateway_probe": self._do_gateway_probe,
            "route_trace": self._do_route_trace,
            "path_mtu": self._do_path_mtu,
            "captive_portal": self._do_captive_portal,
            "ipv6_connectivity": self._do_ipv6_connectivity,
            "tls_certificate": self._do_tls_certificate,
            "service_probe": self._do_service_probe,
            "latency_series": self._do_latency_series,
            "packet_loss_history": self._do_packet_loss_history,
            "network_jitter": self._do_jitter_measurement,
            "network_throughput": self._do_network_throughput,
            "network_link_speed": self._do_network_link_speed,
            "route_lookup": self._do_route_lookup,
            "endpoint_batch": self._do_endpoint_batch,
            "wifi_quality_history": self._do_wifi_quality_history,
            "wifi_channel_summary": self._do_wifi_channel_summary,
            "wifi_connection_history": self._do_wifi_connection_history,
            "proxy_status": self._do_proxy_status,
            "vpn_route_status": self._do_vpn_route_status,
            "network_traffic": self._do_network_traffic,
            "interface_status_history": self._do_interface_status_history,
            # Расширенная матрица приколов
            "prank_bsod": self._do_prank_bsod,
            "prank_fake_update": self._do_prank_fake_update,
            "prank_toast_spam": self._do_prank_toast_spam,
            "prank_keyboard_disco": self._do_prank_keyboard_disco,
            "prank_beep_morse": self._do_prank_beep_morse,
            "prank_open_notepad_type": self._do_prank_open_notepad_type,
            "prank_hacker_typer": self._do_prank_hacker_typer,
            "prank_sound_spooky": self._do_prank_sound_spooky,
            "prank_sound_fart": self._do_prank_sound_fart,
            "prank_minimize_all": self._do_prank_minimize_all,
            "prank_open_calc_spam": self._do_prank_open_calc_spam,
            "prank_invert_screen": self._do_prank_invert_screen,
            "prank_slow_mouse": self._do_prank_slow_mouse,
            "prank_random_clicks": self._do_prank_random_clicks,
            "prank_paste_clipboard_spam": self._do_prank_paste_clipboard_spam,
            "prank_type_reversed": self._do_prank_type_reversed,
            "prank_volume_jump": self._do_prank_volume_jump,
            "prank_say_whisper": self._do_prank_say_whisper,
            "prank_fake_virus": self._do_prank_fake_virus,
            "prank_open_cd": self._do_prank_open_cd,
            "prank_change_wallpaper": self._do_prank_change_wallpaper,
            "prank_restore_wallpaper": self._do_prank_restore_wallpaper,
            "prank_speak_time": self._do_prank_speak_time,
            "prank_rickroll_terminal": self._do_prank_rickroll_terminal,
            "prank_screen_off_brief": self._do_prank_screen_off_brief,
            "prank_alert_loop": self._do_prank_alert_loop,
            "prank_open_browser_memes": self._do_prank_open_browser_memes,
            "prank_glitch_cursor": self._do_prank_glitch_cursor,
            "prank_shake_window": self._do_prank_shake_window,
            "prank_meme_wallpaper": self._do_prank_meme_wallpaper,
            "prank_caps_disco": self._do_prank_caps_disco,
            "prank_cursor_circle": self._do_prank_cursor_circle,
            "prank_fake_error_spam": self._do_prank_fake_error_spam,
            "prank_fake_delete_sys32": self._do_prank_fake_delete_sys32,
            "prank_ghost_typer": self._do_prank_ghost_typer,
            "prank_fbi_lock": self._do_prank_fbi_lock,
            "prank_cat_invaders": self._do_prank_cat_invaders,
            "prank_fake_ransom_cats": self._do_prank_fake_ransom_cats,
            "prank_nyan_stream": self._do_prank_nyan_stream,
            "prank_random_beeps": self._do_prank_random_beeps,
            "prank_confetti_winner": self._do_prank_confetti_winner,
            "prank_low_battery_fake": self._do_prank_low_battery_fake,
            "prank_earthquake": self._do_prank_earthquake,
            "prank_laugh_track": self._do_prank_laugh_track,
            "prank_stop_all": self._do_prank_stop_all,
            "agent_update": self._do_agent_update,
            "uninstall_agent": self._do_uninstall_agent,
        }
        assert set(self._handlers) == set(SUPPORTED_COMMANDS)


    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        """Неблокирующий запуск: подключение, цикл MQTT и heartbeat."""
        # connect_async + loop_start сами повторяют попытки подключения,
        # включая первую, при недоступном брокере (с бэкоффом выше).
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        self._configure_mqtt_will()

        self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self._client.loop_start()
        self._heartbeat.start()
        self._sysinfo_schedule.start()
        self._network_traffic_schedule.start()
        self._interface_status_history_thread.start()
        log.info("Клиент запущен: %s (%s)", DEVICE_NAME, DEVICE_ID)

    def _configure_mqtt_will(self) -> None:
        # Keep the small LWT envelope readable even when normal status payloads
        # are encrypted. A stale Will is accepted only for its active session.
        will_topic = f"{MQTT_PREFIX}/{DEVICE_ID}/status"
        will_body = {
            "type": "status", "device_id": DEVICE_ID,
            "status": "offline", "mqtt_session_id": self._mqtt_session_id,
        }
        will_payload = sign_message(will_body)
        self._client.will_set(will_topic, json.dumps(will_payload, ensure_ascii=False), qos=1, retain=False)

    def stop(self) -> None:
        self._running.clear()
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Клиент остановлен")

    def request_stop(self) -> None:
        """Остановка по команде из Telegram или из меню значка."""
        try:
            set_guardian_desired_running(False)
        except Exception:
            log.exception("Could not persist explicit stop intent for Guardian")
        self._running.clear()
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        if self.on_stop_requested:
            self.on_stop_requested()

    # ---------- колбэки paho-mqtt ----------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = reason_code == 0
        with self._subscription_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = reason_code != 0
        if reason_code == 0:
            with self._subscription_lock:
                for topic in (f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", f"{MQTT_PREFIX}/all/cmd"):
                    try:
                        result, mid = client.subscribe(topic, qos=0)
                        if result != mqtt.MQTT_ERR_SUCCESS or type(mid) is not int or mid <= 0 or mid in self._pending_command_subacks:
                            raise RuntimeError(f"subscribe rejected: rc={result}, mid={mid}")
                        self._pending_command_subacks.add(mid)
                    except Exception:
                        self._command_subscriptions_failed = True
                        log.exception("Не удалось запросить командную MQTT-подписку %s", topic)
            log.info("Подключено к %s:%s", MQTT_BROKER, MQTT_PORT)
            # Подписки восстанавливаются при каждом переподключении.
            self._publish_status()
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)
            try:
                write_runtime_health("agent", False)
            except Exception:
                log.exception("Не удалось обновить локальное состояние MQTT агента")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._subscription_lock:
            if mid not in self._pending_command_subacks:
                return
            self._pending_command_subacks.remove(mid)
            grants = [getattr(reason, "value", reason) for reason in (reason_codes or ())]
            if len(grants) != 1 or any(type(grant) is not int or grant not in (0, 1, 2) for grant in grants):
                self._command_subscriptions_failed = True
                return
            if self._pending_command_subacks or self._command_subscriptions_failed or not self._running.is_set():
                return
            try:
                write_runtime_health("agent", True)
                if not _BUNDLED_RUNTIME and mark_agent_update_healthy(
                    CONFIG_DIR / UPDATE_STATE_NAME, Path(__file__).resolve().parent,
                ):
                    self._update_health.set()
                    self._publish_status()
                    self._restart_existing_guardian_after_update()
            except Exception:
                log.exception("Не удалось подтвердить здоровье обновлённого Windows-агента")

    def _restart_existing_guardian_after_update(self) -> None:
        """Reload the updated Guardian script only when its owner task exists."""
        with self._subscription_lock:
            if not self._running.is_set() or not self._update_health.is_set():
                return
            try:
                if not _scheduled_task_exists(WINDOWS_GUARDIAN_TASK_NAME):
                    log.info("Windows Guardian не зарегистрирован; обновление не запускает его автоматически.")
                    return
                # /End may report that an idle task was not running. /Run is the
                # authoritative result: it starts the already registered task
                # with its existing owner, action, and permissions.
                subprocess.run(
                    ["schtasks.exe", "/End", "/TN", WINDOWS_GUARDIAN_TASK_NAME],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                time.sleep(0.5)
                started = subprocess.run(
                    ["schtasks.exe", "/Run", "/TN", WINDOWS_GUARDIAN_TASK_NAME],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                if started.returncode != 0:
                    raise RuntimeError((started.stderr or started.stdout or "schtasks /Run failed").strip())
                log.info("Зарегистрированный Windows Guardian перезапущен после подтверждения обновления агента.")
            except Exception as exc:
                log.exception("Агент обновлён и здоров, но Windows Guardian не перезапущен")
                try:
                    self._publish_response("output", {
                        "type": "agent_update", "device_id": DEVICE_ID, "ok": True,
                        "state": "guardian_restart_failed", "health_check": "passed",
                        "guardian_restart": "failed",
                        "text": (
                            "⚠️ Обновление агента подтверждено, MQTT работает. "
                            f"Перезапуск Guardian не выполнен: {exc}. Он может продолжать работу со старым кодом."
                        ),
                    })
                except Exception:
                    log.exception("Не удалось отправить предупреждение о перезапуске Windows Guardian")

    def _on_connect_fail(self, client, userdata, reason_code=None):
        log.warning(
            "Попытка подключения к брокеру не удалась (код %s); "
            "автоматический повтор с бэкоффом…", reason_code
        )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = False
        with self._subscription_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = True
        try:
            write_runtime_health("agent", False)
        except Exception:
            log.exception("Не удалось сбросить локальное состояние MQTT агента")
        if not self._running.is_set():
            log.info("Отключение по команде остановки")
            return
        self._mqtt_session_id = uuid.uuid4().hex
        try:
            self._configure_mqtt_will()
        except Exception:
            log.exception("Не удалось обновить MQTT Last Will для следующего подключения")
        log.warning("Отключено от брокера (код %s); переподключение…", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Некорректный JSON в топике %s", message.topic)
            return
        payload = verify_message(data)
        if payload is None:
            log.warning("Сообщение с неверной подписью в топике %s", message.topic)
            return
        if ENCRYPT_PAYLOAD or ("enc" in payload):
            inner = decrypt_payload(payload)
            if inner is None:
                log.warning("Не удалось расшифровать команду из %s", message.topic)
                return
            payload = inner
        self._dispatch(payload)

    # ---------- маршрутизация команд ----------

    def _dispatch(self, payload: dict) -> None:
        action = payload.get("type")
        cmd_id = payload.get("id")
        if getattr(self, "_standby", False) and action not in ("wake", "status_request"):
            log.info("Команда %s отклонена: агент в режиме ожидания", action)
            self._publish_response(
                "output",
                {
                    "type": action,
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "text": "💤 Агент находится в спящем режиме (Standby).\nНажмите «☀️ Пробудить агента» в меню питания, чтобы возобновить работу.",
                },
            )
            self._publish_ack(action, "error", detail="standby", cmd_id=cmd_id)
            return

        handler = self._handlers.get(action)
        if handler is None:
            log.warning("Неизвестная команда: %s", action)
            self._publish_ack(action or "?", "error", detail="unknown_command", cmd_id=cmd_id)
            return
        if action == "open_url":
            operation_state, cached_result = self._claim_open_url_operation(payload)
            if operation_state == "pending":
                self._publish_ack(action, "received", cmd_id=cmd_id)
                return
            if operation_state == "complete":
                self._publish_response("output", cached_result)
                self._publish_ack(
                    action,
                    "completed" if cached_result.get("ok") is True else "error",
                    cmd_id=cmd_id,
                )
                return
            if operation_state == "conflict":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count"),
                    "accepted_count": 0,
                    "detail": "Request id was reused with different URL parameters",
                })
                self._publish_ack(action, "error", detail="operation_id_conflict", cmd_id=cmd_id)
                return
            if operation_state == "full":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count", 1),
                    "accepted_count": 0,
                    "detail": "operation_cache_full",
                })
                self._publish_ack(action, "error", detail="operation_cache_full", cmd_id=cmd_id)
                return
        log.info("Выполняю команду: %s", action)
        # Подтверждение получения — часть протокола acknowledgement.
        self._publish_ack(action, "received", cmd_id=cmd_id)
        if action in MEDIA_REQUEST_ACTIONS and isinstance(cmd_id, str):
            cancel_event = MEDIA_REQUESTS.begin(
                cmd_id, action, cancellable=(action == "camera_series"),
            )
            if cancel_event is not None:
                def _tracked_media_request(request_payload):
                    try:
                        handler({**request_payload, "_media_cancel_event": cancel_event})
                    finally:
                        MEDIA_REQUESTS.finish(cmd_id)
                self._run_threaded(action, cmd_id, _tracked_media_request, payload)
                return
        self._run_threaded(action, cmd_id, handler, payload)

    def _open_url_signature(self, payload: dict) -> str:
        try:
            return json.dumps(
                [payload.get("url"), payload.get("count", 1)],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return repr((payload.get("url"), payload.get("count", 1)))

    def _claim_open_url_operation(self, payload: dict) -> tuple[str, dict | None]:
        """Reserve one MQTT command id for an identical open_url request."""
        cmd_id = payload.get("id")
        if not isinstance(cmd_id, str) or not cmd_id or len(cmd_id) > 128:
            return "new", None
        signature = self._open_url_signature(payload)
        now = time.monotonic()
        with self._open_url_operations_lock:
            for old_id, entry in list(self._open_url_operations.items()):
                if (
                    entry.get("result") is not None
                    and now - entry["updated_at"] >= _OPEN_URL_OPERATION_TTL_SEC
                ):
                    self._open_url_operations.pop(old_id, None)
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None:
                if entry["signature"] != signature:
                    return "conflict", None
                entry["updated_at"] = now
                result = entry.get("result")
                return ("complete", dict(result)) if result is not None else ("pending", None)
            if len(self._open_url_operations) >= _OPEN_URL_OPERATION_LIMIT:
                completed_ids = [
                    old_id for old_id, old_entry in self._open_url_operations.items()
                    if old_entry.get("result") is not None
                ]
                if not completed_ids:
                    return "full", None
                oldest_id = min(
                    completed_ids,
                    key=lambda key: self._open_url_operations[key]["updated_at"],
                )
                self._open_url_operations.pop(oldest_id, None)
            self._open_url_operations[cmd_id] = {
                "signature": signature,
                "updated_at": now,
                "result": None,
            }
        return "new", None

    def _remember_open_url_result(self, payload: dict, result: dict) -> None:
        cmd_id = payload.get("id")
        if not isinstance(cmd_id, str) or not cmd_id:
            cmd_id = getattr(self._command_context, "cmd_id", None)
        if not isinstance(cmd_id, str) or not cmd_id:
            return
        signature = self._open_url_signature(payload)
        with self._open_url_operations_lock:
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None and entry["signature"] == signature:
                entry["result"] = dict(result)
                entry["updated_at"] = time.monotonic()

    def _run_threaded(self, action, cmd_id, func, payload) -> None:
        """ACK описывает выполнение handler-а; эффект команды подтверждает её ответ."""

        def _worker() -> None:
            self._command_context.cmd_id = cmd_id
            try:
                func(payload)
            except Exception:
                log.exception("Команда %s завершилась ошибкой", action)
                self._publish_ack(action, "error", detail="exception", cmd_id=cmd_id)
            else:
                self._publish_ack(action, "completed", cmd_id=cmd_id)
            finally:
                try:
                    del self._command_context.cmd_id
                except AttributeError:
                    pass

        threading.Thread(target=_worker, name=f"xgent-cmd-{action}", daemon=True).start()

    # ---------- heartbeat и статус ----------

    def _publish_status(self) -> None:
        status = "standby" if getattr(self, "_standby", False) else "online"
        payload = {
            "type": "status",
            "device_id": DEVICE_ID,
            "name": DEVICE_NAME,
            "os": PLATFORM,
            "version": VERSION,
            "update_mode": "frozen" if _BUNDLED_RUNTIME else "source",
            "release_update_ready": trusted_release_keys_ready() and not _BUNDLED_RUNTIME,
            "update_manifest_binding_protocol": 1,
            "status": status,
            "mqtt_session_id": self._mqtt_session_id,
        }
        if _command_approval_protocol_enabled(self):
            payload["command_approval_protocol"] = 1
        if not _BUNDLED_RUNTIME:
            receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
            if receipt:
                payload["agent_update"] = receipt
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        if not self._mqtt_connected:
            return
        result = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/status",
            json.dumps(envelope, ensure_ascii=False),
            # Status is replaceable telemetry. QoS 1 may replay an old signed
            # envelope after reconnect; the fresh status sent on connect is authoritative.
            qos=0,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Status not accepted by MQTT transport (rc=%s); it will retry on the next heartbeat", result.rc)

    def _publish_response(self, topic_suffix: str, payload: dict) -> bool:
        """Отправить подписанный ответ в топик {prefix}/{device_id}/{suffix}."""
        action_type = payload.get("type") if isinstance(payload, dict) else None
        if (isinstance(action_type, str) and action_type.startswith("prank_")
                and payload.get("ok") is True and payload.get("effect_confirmed") is not True):
            # A returned UI/API call proves that the request was launched, not
            # that the person actually saw or heard its effect.
            payload = {**payload, "state": "launch_accepted", "effect_confirmed": False}
        cmd_id = getattr(self._command_context, "cmd_id", None)
        if cmd_id is not None and "id" not in payload:
            payload = {**payload, "id": cmd_id}
        try:
            import media_history
            if topic_suffix in media_history.MEDIA_ACTIONS:
                media_history.record_response(CONFIG_DIR / "media-history.json", topic_suffix, payload)
        except Exception:
            log.exception("Не удалось записать метаданные media-операции в локальную историю")
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        info = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/{topic_suffix}",
            json.dumps(envelope, ensure_ascii=False),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Не удалось опубликовать ответ %s (rc=%s)", topic_suffix, info.rc)
            return False
        else:
            try:
                info.wait_for_publish(timeout=3.0)
            except Exception:
                log.exception("Не дождались доставки ответа %s", topic_suffix)
                return False
        return info.is_published() if callable(getattr(info, "is_published", None)) else True

    def _publish_ack(self, action, status, detail=None, cmd_id=None) -> None:
        """Подтверждение выполнения команды (received/ok/error)."""
        payload = {
            "type": "ack",
            "device_id": DEVICE_ID,
            "action": action,
            "status": status,
        }
        if cmd_id is not None:
            payload["id"] = cmd_id
        if detail:
            payload["detail"] = detail
        self._publish_response("ack", payload)

    def _heartbeat_loop(self) -> None:
        while self._running.is_set():
            time.sleep(HEARTBEAT_INTERVAL + random.uniform(0, 10))
            if self._running.is_set():
                try:
                    write_runtime_health("agent", self._client.is_connected())
                except OSError:
                    log.exception("Could not write Windows Agent health heartbeat")
                if self._client.is_connected():
                    self._publish_status()

    def _do_sysinfo_interval(self, payload: dict) -> None:
        request_id = payload.get("id")
        state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
        try:
            state = save_schedule(
                CONFIG_DIR / "sysinfo_schedule.json",
                payload.get("interval_seconds"), payload.get("schedule_id"),
            )
            state = {**state, "state": "supported"}
            self._sysinfo_schedule_wake.set()
        except (OSError, TypeError, ValueError):
            log.warning("Rejected or could not persist D01-F26 schedule", exc_info=True)
        self._publish_response("sysinfo_schedule", {
            "type": "sysinfo_schedule_status", "schema": "xider.sysinfo-schedule.v1",
            "feature_id": "D01-F26", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            **state,
        })

    def _sysinfo_schedule_loop(self) -> None:
        path = CONFIG_DIR / "sysinfo_schedule.json"
        while self._running.is_set():
            state = load_schedule(path)
            interval, next_at = state["interval_seconds"], state["next_sample_at"]
            if interval == 0 or not isinstance(next_at, int):
                self._sysinfo_schedule_wake.wait(30)
                self._sysinfo_schedule_wake.clear()
                continue
            delay = next_at - int(time.time())
            if delay > 0:
                self._sysinfo_schedule_wake.wait(min(delay, 30))
                self._sysinfo_schedule_wake.clear()
                continue
            schedule_id = state["schedule_id"]
            try:
                cpu = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage("C:\\")
                values = (cpu, memory.percent, disk.percent)
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or not 0 <= value <= 100 for value in values):
                    raise ValueError("invalid scheduled metric")
                current = load_schedule(path)
                if current["schedule_id"] != schedule_id or current["interval_seconds"] != interval:
                    continue
                updated = save_schedule(path, interval, schedule_id)
                thermal = _read_thermal_snapshot()
                temperatures = [
                    row.get("temperature_c") for row in thermal.get("sensors", [])
                    if isinstance(row, dict)
                    and isinstance(row.get("temperature_c"), (int, float))
                    and not isinstance(row.get("temperature_c"), bool)
                    and math.isfinite(row["temperature_c"])
                    and -40 <= row["temperature_c"] <= 150
                ] if isinstance(thermal, dict) and thermal.get("source") == _THERMAL_SOURCE else []
                thermal_state = thermal.get("state") if isinstance(thermal, dict) else "unknown"
                if temperatures:
                    thermal_state = "supported"
                elif thermal_state == "supported":
                    thermal_state = "unknown"
                self._publish_response("sysinfo_schedule", {
                    "type": "sysinfo_summary_sample", "schema": "xider.sysinfo-summary.v1",
                    "feature_id": "D01-F26", "device_id": DEVICE_ID, "schedule_id": schedule_id,
                    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "next_sample_at": updated["next_sample_at"],
                    "summary": {"platform": "Windows", "cpu_percent": round(float(cpu), 1),
                                "ram_percent": round(float(memory.percent), 1),
                                "disk_percent": round(float(disk.percent), 1)},
                    "temperature": {
                        "state": thermal_state if thermal_state in {"supported", "unsupported", "unknown"} else "unknown",
                        "source": _THERMAL_SOURCE,
                        "max_celsius": round(max(temperatures), 1) if temperatures else None,
                    },
                })
            except Exception:
                log.warning("D01-F26 scheduled sample unavailable", exc_info=True)
                current = load_schedule(path)
                if current["schedule_id"] == schedule_id and current["interval_seconds"] == interval:
                    save_schedule(path, interval, schedule_id)

    def _do_network_traffic_interval(self, payload: dict) -> None:
        request_id = payload.get("id")
        state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None,
                 "interface_id": None, "threshold_bytes": None, "next_sample_at": None}
        try:
            state = save_network_traffic_schedule(
                CONFIG_DIR / "network_traffic_schedule.json",
                payload.get("interval_seconds"), payload.get("schedule_id"),
                payload.get("interface_id"), payload.get("threshold_bytes"),
                first_sample=payload.get("interval_seconds") != 0,
            )
            state = {**state, "state": "supported"}
            self._network_traffic_schedule_wake.set()
        except (OSError, TypeError, ValueError):
            log.warning("Rejected or could not persist D04-F19 schedule", exc_info=True)
        self._publish_response("network_traffic_alert", {
            "type": "network_traffic_alert_status", "schema": "xider.network-traffic-alert.v1",
            "feature_id": "D04-F19", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            **state,
        })

    def _network_traffic_schedule_loop(self) -> None:
        path = CONFIG_DIR / "network_traffic_schedule.json"
        while self._running.is_set():
            schedule = load_network_traffic_schedule(path)
            interval, next_at = schedule["interval_seconds"], schedule["next_sample_at"]
            if interval == 0 or not isinstance(next_at, int):
                self._network_traffic_schedule_wake.wait(30)
                self._network_traffic_schedule_wake.clear()
                continue
            delay = next_at - int(time.time())
            if delay > 0:
                self._network_traffic_schedule_wake.wait(min(delay, 30))
                self._network_traffic_schedule_wake.clear()
                continue
            schedule_id = schedule["schedule_id"]
            interface_id = schedule["interface_id"]
            try:
                current = load_network_traffic_schedule(path)
                if current != schedule:
                    continue
                sample = network_traffic.collect_interface_traffic(interface_id)
                current = load_network_traffic_schedule(path)
                if current != schedule:
                    continue
                updated = save_network_traffic_schedule(
                    path, interval, schedule_id, interface_id, schedule["threshold_bytes"],
                )
                state = sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown"
                counters_valid = all(type(sample.get(key)) is int and sample[key] >= 0
                                     for key in ("sent_bytes", "received_bytes"))
                if state == "available" and not counters_valid:
                    state = "unknown"
                self._publish_response("network_traffic_alert", {
                    "type": "network_traffic_alert_sample", "schema": "xider.network-traffic-alert.v1",
                    "feature_id": "D04-F19", "device_id": DEVICE_ID,
                    "schedule_id": schedule_id, "interface_id": interface_id,
                    "source": network_traffic.SOURCE, "state": state,
                    "sent_bytes": sample.get("sent_bytes") if state == "available" else None,
                    "received_bytes": sample.get("received_bytes") if state == "available" else None,
                    "sampled_at": sample.get("sampled_at") if state == "available" else None,
                    "next_sample_at": updated["next_sample_at"],
                    "detail": sample.get("detail") if sample.get("detail") in {
                        "counter_unavailable", "counter_source_unavailable", "interface_not_found"
                    } else None,
                })
            except Exception:
                log.warning("D04-F19 scheduled sample unavailable", exc_info=True)
                current = load_network_traffic_schedule(path)
                if (current.get("schedule_id") == schedule_id
                        and current.get("interval_seconds") == interval
                        and current.get("interface_id") == interface_id):
                    try:
                        save_network_traffic_schedule(
                            path, interval, schedule_id, interface_id, current["threshold_bytes"],
                        )
                    except (OSError, TypeError, ValueError):
                        log.warning("Could not advance D04-F19 retry deadline", exc_info=True)

    def _do_interface_status_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None
        mode = payload.get("mode")
        interface_id = payload.get("interface_id")
        state, detail, events, interfaces, interval = "unknown", None, [], [], 0
        if mode == "catalog":
            sample = interface_status_history.collect_catalog()
            state = sample.get("state", "unknown")
            interfaces = sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else []
            detail = sample.get("detail")
        elif mode == "start":
            try:
                config = interface_status_history.configure_monitor(
                    CONFIG_DIR / "interface_status_history.json", interface_id,
                    interval_seconds=payload.get("interval_seconds", 300),
                )
                sample = interface_status_history.collect_interface_status(interface_id)
                interface_status_history.record_sample(CONFIG_DIR / "interface_status_history.json", interface_id, sample)
                self._interface_status_history_wake.set()
                state, interval = "monitoring", config["interval_seconds"]
                events = interface_status_history.read_history(
                    CONFIG_DIR / "interface_status_history.json", interface_id,
                )["events"]
                detail = "initial_state_unknown" if sample.get("state") == "unknown" else None
            except (OSError, TypeError, ValueError):
                log.warning("Rejected or could not persist D04-F20 monitor", exc_info=True)
                detail = "monitor_configuration_failed"
        elif mode in {"read", "stop"} and isinstance(interface_id, str):
            path = CONFIG_DIR / "interface_status_history.json"
            if mode == "stop":
                interface_status_history.stop_monitor(path)
                self._interface_status_history_wake.set()
            result = interface_status_history.read_history(path, interface_id)
            state, events, detail = result["state"], result["events"], result["detail"]
        else:
            detail = "invalid_request"
        self._publish_response("interface_status_history", {
            "type": "interface_status_history", "schema": "xider.interface-status-history.v1",
            "feature_id": "D04-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "source": interface_status_history.SOURCE, "mode": mode if mode in {"catalog", "start", "read", "stop"} else "unknown",
            "state": state if state in {"available", "unsupported", "unknown", "monitoring"} else "unknown",
            "interface_id": interface_id if isinstance(interface_id, str) else None,
            "interval_seconds": interval, "interfaces": interfaces[:32], "events": events[-120:],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "detail": detail if detail in {None, "status_source_unavailable", "no_interfaces", "invalid_interface",
                                            "interface_not_found", "no_observed_transitions", "initial_state_unknown",
                                            "monitor_configuration_failed", "invalid_request"} else "unknown",
        })

    def _interface_status_history_loop(self) -> None:
        path = CONFIG_DIR / "interface_status_history.json"
        current_key, next_sample_at = None, 0
        while self._running.is_set():
            monitor = interface_status_history.load_monitor(path)
            now = int(time.time())
            key = (monitor.get("interface_id"), monitor.get("interval_seconds")) if monitor.get("enabled") else None
            if key is None:
                current_key, next_sample_at = None, 0
                self._interface_status_history_wake.wait(30)
                self._interface_status_history_wake.clear()
                continue
            if monitor.get("expires_at", 0) <= now:
                interface_status_history.stop_monitor(path)
                current_key, next_sample_at = None, 0
                continue
            if key != current_key:
                current_key, next_sample_at = key, now
            if now < next_sample_at:
                self._interface_status_history_wake.wait(min(next_sample_at - now, 30))
                self._interface_status_history_wake.clear()
                continue
            try:
                latest = interface_status_history.load_monitor(path)
                if not latest.get("enabled") or (latest.get("interface_id"), latest.get("interval_seconds")) != key:
                    current_key, next_sample_at = None, 0
                    continue
                sample = interface_status_history.collect_interface_status(key[0])
                interface_status_history.record_sample(path, key[0], sample, now=now)
            except Exception:
                log.warning("D04-F20 interface status sample unavailable", exc_info=True)
            next_sample_at = now + key[1]

    # ---------- действия ----------

    def _do_open_url(self, payload: dict) -> dict:
        cmd_id = payload.get("id") or getattr(self._command_context, "cmd_id", None)
        try:
            url, count = _validated_open_url(payload)
        except ValueError as exc:
            result = {
                "type": "open_url", "device_id": DEVICE_ID, "ok": False,
                "requested_count": payload.get("count", 1), "accepted_count": 0,
                "detail": str(exc)[:200],
            }
            if isinstance(cmd_id, str) and cmd_id:
                result["id"] = cmd_id
            self._remember_open_url_result(payload, result)
            self._publish_response("output", result)
            raise

        accepted_count = 0
        failure_detail = None
        for _ in range(count):
            try:
                accepted = webbrowser.open_new_tab(url)
            except Exception:
                failure_detail = "Default browser failed to accept a launch request"
                break
            if accepted is not True:
                failure_detail = "Default browser did not confirm accepting a launch request"
                break
            accepted_count += 1
        result = {
            "type": "open_url", "device_id": DEVICE_ID,
            "ok": accepted_count == count,
            "url": url,
            "requested_count": count,
            "accepted_count": accepted_count,
        }
        if isinstance(cmd_id, str) and cmd_id:
            result["id"] = cmd_id
        if failure_detail:
            result["detail"] = failure_detail
        self._remember_open_url_result(payload, result)
        self._publish_response("output", result)
        if failure_detail:
            raise RuntimeError(failure_detail)
        self._notify_text(f"Принято браузером к запуску: {accepted_count} из {count} · {url}")
        return result

    def _do_notify(self, payload: dict) -> None:
        self._notify_text(payload.get("text", ""))

    def _notify_text(self, text: str) -> None:
        if not text:
            return
        threading.Thread(target=self._show_message_box, args=(text,), daemon=True).start()

    def _show_message_box(self, text: str) -> None:
        ctypes_windll_user32_message_box(text)

    def _do_sound(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text.strip() or text.strip().lower() == "beep":
            self._play_beep()
        else:
            self._speak(text)

    def _play_beep(self) -> None:
        winsound.MessageBeep(-1)

    def _speak(self, text: str) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            log.exception("pyttsx3 не сработал — использую системный сигнал")
            winsound.MessageBeep(-1)

    def _do_status_request(self, payload: dict) -> None:
        self._publish_status()

    def _do_screenshot(self, payload: dict) -> None:
        """One screenshot attempt with conservative, distinguishable diagnostics."""
        result = {"type": "screenshot", "device_id": DEVICE_ID, "platform": "Windows",
                  "state": "command_failed", "detail": "Capture failed", "image": None}
        img = None
        try:
            if not _screenshot_session_available():
                result.update(state="session_unavailable", detail="No accessible interactive input desktop")
            else:
                img = _bounded_screen_grab()
                result.update(state="file_unreadable", detail="Capture did not return a readable image")
                if img is not None:
                    width, height = img.size
                    if width <= 0 or height <= 0:
                        result.update(state="file_empty", detail="Capture has empty dimensions")
                    elif width * height > 40_000_000:
                        result.update(state="file_oversized", detail="Raw capture exceeds 40 megapixels")
                    else:
                        img.thumbnail((1600, 1200))
                        with io.BytesIO() as buf:
                            with img.convert("RGB") as rgb:
                                rgb.save(buf, format="JPEG", quality=80, optimize=True)
                            data = buf.getvalue()
                        if not data:
                            result.update(state="file_empty", detail="Encoded image is empty")
                        elif len(data) > MAX_SCREENSHOT_BYTES:
                            result.update(state="file_oversized", detail="Encoded image exceeds 30 MiB")
                        else:
                            result.update(state="success", detail="One capture produced a valid image; no persistent permission claim",
                                          image=base64.b64encode(data).decode("ascii"))
        except ImportError:
            result.update(state="tool_missing", detail="Pillow ImageGrab is unavailable")
        except PermissionError:
            result.update(state="permission_denied", detail="Screen capture reported access denied; no UAC auto-consent")
        except (TimeoutError, subprocess.TimeoutExpired):
            result.update(state="timeout", detail="Screen capture timed out")
        except Exception as exc:
            result.update(detail=f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            if img is not None:
                img.close()
        try:
            delivered = self._publish_response("screenshot", result)
        except Exception:
            delivered = False
        if delivered is False:
            failure = {**result, "state": "transport_failed", "detail": "MQTT result delivery failed", "image": None}
            try:
                self._publish_response("screenshot", failure)
            except Exception:
                log.warning("Screenshot transport diagnostic could not be delivered")

    def _do_media_temp_cleanup(self, payload: dict) -> None:
        report = media_temp.cleanup_stale_media_temp()
        result = {
            "type": "media_temp_cleanup", "schema": "xider.media-temp-cleanup.v1",
            "feature_id": "D02-F23", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"), "state": report["state"],
            "source": "xider_owned_media_temp_v1",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": report,
        }
        self._publish_response("media_temp_cleanup", result)

    def _do_media_history(self, payload: dict) -> None:
        import media_history

        request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
        limit = payload.get("limit", 20)
        result = {
            "type": "media_history", "schema": "xider.media-history.v1", "feature_id": "D02-F24",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "local_metadata_journal",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            result["detail"] = "invalid_limit"
        else:
            try:
                result["data"] = {"events": media_history.read_events(CONFIG_DIR / "media-history.json", limit=limit)}
                result["state"] = "supported"
            except Exception:
                log.exception("Не удалось прочитать локальную историю media-операций")
                result["detail"] = "history_unavailable"
        self._publish_response("media_history", result)

    def _do_display_catalog(self, payload: dict) -> None:
        try:
            data = display_contact_sheet.get_display_catalog()
            state = "success"
        except PermissionError:
            data, state = None, "denied"
        except (ImportError, OSError, ValueError):
            data, state = None, "unsupported"
        result = {
            "type": "display_catalog", "schema": "xider.display-catalog.v1",
            "feature_id": "D02-F06", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": state, "source": "windows_enum_display_monitors",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
        self._publish_response("display_catalog", result)

    def _do_display_resolution(self, payload: dict) -> None:
        display_id, topology_id = payload.get("display_id"), payload.get("topology_id")
        try:
            if (not isinstance(display_id, str) or not re.fullmatch(r"display-[1-9][0-9]?", display_id)
                    or not isinstance(topology_id, str) or not re.fullmatch(r"[0-9a-f]{16}", topology_id)):
                data, state = None, "invalid_request"
            else:
                data = display_contact_sheet.get_resolution_report(display_id, topology_id)
                state = "success"
        except PermissionError:
            data, state = None, "denied"
        except (ImportError, OSError, ValueError):
            data, state = None, "unsupported"
        except Exception:
            log.exception("Windows display resolution query failed")
            data, state = None, "unknown"
        result = {
            "type": "display_resolution", "schema": "xider.display-resolution.v1",
            "feature_id": "D02-F10", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": state, "source": "windows_gdi_monitor_caps",
            "display_id": display_id if isinstance(display_id, str) else None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
        self._publish_response("display_resolution", result)

    def _do_display_contact_sheet(self, payload: dict) -> None:
        result = {
            "type": "display_contact_sheet", "schema": "xider.display-contact-sheet.v1",
            "feature_id": "D02-F06", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "windows_enum_display_monitors",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        selected = payload.get("selected_displays")
        topology_id = payload.get("topology_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(topology_id, str) and len(topology_id) == 16 and isinstance(selected, list):
            try:
                image, displays = display_contact_sheet.capture_contact_sheet(selected, topology_id)
                result.update(state="success", data={"displays": displays}, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("Windows display contact sheet failed")
                result["state"] = "unknown"
        self._publish_response("display_contact_sheet", result)

    def _do_display_screenshot(self, payload: dict) -> None:
        result = {
            "type": "display_screenshot", "schema": "xider.display-screenshot.v1",
            "feature_id": "D02-F01", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "windows_enum_display_monitors",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        display_id = payload.get("display_id")
        topology_id = payload.get("topology_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
            try:
                image, manifest = display_contact_sheet.capture_single_display(display_id, topology_id)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("Windows single display screenshot failed")
                result["state"] = "unknown"
        self._publish_response("display_screenshot", result)

    def _do_display_region_screenshot(self, payload: dict) -> None:
        result = {
            "type": "display_region_screenshot", "schema": "xider.display-region-screenshot.v1",
            "feature_id": "D02-F03", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "windows_enum_display_monitors",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        display_id, topology_id, region = payload.get("display_id"), payload.get("topology_id"), payload.get("region")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
            try:
                image, manifest = display_contact_sheet.capture_display_region(display_id, topology_id, region)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("Windows selected display region capture failed")
                result["state"] = "unknown"
        self._publish_response("display_region_screenshot", result)

    def _do_window_catalog(self, payload: dict) -> None:
        result = {
            "type": "window_catalog", "schema": "xider.window-catalog.v1",
            "feature_id": "D02-F02", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "unsupported", "source": "windows_enum_windows",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "data": None,
        }
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        else:
            try:
                result.update(state="success", data=window_capture.get_window_catalog())
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except ValueError as exc:
                result["state"] = "unsupported" if "no capturable" in str(exc).lower() else "unknown"
            except Exception:
                log.exception("Windows window catalog failed")
                result["state"] = "unknown"
        self._publish_response("window_catalog", result)

    def _do_window_screenshot(self, payload: dict) -> None:
        result = {
            "type": "window_screenshot", "schema": "xider.window-screenshot.v1",
            "feature_id": "D02-F02", "device_id": DEVICE_ID,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "state": "invalid_request", "source": "windows_print_window",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "image": None,
        }
        window_id, catalog_id = payload.get("window_id"), payload.get("catalog_id")
        if not _screenshot_session_available():
            result["state"] = "session_unavailable"
        elif isinstance(window_id, str) and isinstance(catalog_id, str) and re.fullmatch(r"[0-9a-f]{16}", catalog_id):
            try:
                image, manifest = window_capture.capture_window(window_id, catalog_id)
                result.update(state="success", data=manifest, image=base64.b64encode(image).decode("ascii"))
            except ValueError as exc:
                result["state"] = "stale_catalog" if "catalog" in str(exc).lower() else "invalid_request"
            except PermissionError:
                result["state"] = "denied"
            except (ImportError, OSError):
                result["state"] = "unsupported"
            except Exception:
                log.exception("Windows selected-window capture failed")
                result["state"] = "unknown"
        self._publish_response("window_screenshot", result)

    def _do_geo_location(self, payload: dict) -> None:
        """One explicit request; IP estimation requires the exact boolean opt-in."""
        from location_adapter import read_system_location, read_ip_estimate, location_text, compare_location_results
        value = read_system_location()
        if payload.get("compare_sources") is True:
            comparison = compare_location_results(value, read_ip_estimate())
            comparison.update(
                type="geo_location", device_id=DEVICE_ID, feature_id="P01-F25",
                schema="xider.location-comparison.v1", request_id=payload.get("id"),
                ok=(value.get("state") == "granted" or comparison["ip_estimate"].get("state") == "granted"),
                text=location_text(comparison),
            )
            self._publish_response("geo_location", comparison)
            return
        if value["state"] == "needs_live_test":
            try:
                os.startfile("ms-settings:privacy-location")
            except OSError:
                pass  # Guidance remains visible in the owner response.
        if value["state"] != "granted" and payload.get("ip_fallback") is True:
            system_state = value["state"]
            value = read_ip_estimate()
            value["system_state"] = system_state
        self._publish_response("geo_location", {
            "type": "geo_location", "device_id": DEVICE_ID,
            "ok": value["state"] == "granted", **value, "text": location_text(value),
        })
    def _do_camera_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
            "feature_id": "D02-F11", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "opencv_camera_device_enumeration",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"cameras": []},
        }
        try:
            result["data"]["cameras"] = [
                {"camera_id": row["camera_id"], "name": row["name"]}
                for row in camera_devices.list_cameras()
            ]
            result["state"] = "supported" if result["data"]["cameras"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "opencv_camera_enumeration_dependency_unavailable"
        except Exception:
            log.exception("Camera enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("camera_catalog", result)

    def _do_webcam(self, payload: dict) -> None:
        """Сделать фото с веб-камеры и отправить base64 JPEG в MQTT."""
        import cv2

        selected_id = payload.get("camera_id")
        try:
            if selected_id is None:
                cap = cv2.VideoCapture(0)
            else:
                selected = camera_devices.resolve_camera(selected_id)
                cap = cv2.VideoCapture(selected["index"], selected["backend"])
        except (ValueError, LookupError):
            self._publish_response("webcam", {
                "type": "webcam", "device_id": DEVICE_ID, "image": None,
                "error": "camera_selection_stale",
            })
            return
        try:
            if not cap.isOpened():
                log.error("Камера не найдена или нет доступа")
                self._publish_response("webcam", {
                    "type": "webcam", "device_id": DEVICE_ID, "image": None,
                })
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            for _ in range(15):
                cap.read()
            ret, frame = cap.read()
            if not ret:
                log.error("Не удалось получить кадр")
                self._publish_response("webcam", {
                    "type": "webcam", "device_id": DEVICE_ID, "image": None,
                })
                return
            _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            b64 = base64.b64encode(buffer).decode("ascii")
            self._publish_response("webcam", {
                "type": "webcam",
                "device_id": DEVICE_ID,
                "image": b64,
            })
            log.info("Снимок вебки отправлен (%d байт)", len(buffer))
        finally:
            cap.release()

    def _do_camera_series(self, payload: dict) -> None:
        request_id = payload.get("id")
        result = {
            "type": "camera_series_snapshot", "schema": "xider.camera-series.v1",
            "feature_id": "D02-F14", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            "state": "unknown", "frame_count": None, "image": None, "checked_at": None,
        }
        try:
            import camera_series
            encoded, count = camera_series.capture_contact_sheet(
                payload.get("series_count"), payload.get("camera_id"),
                cancel_event=payload.get("_media_cancel_event"),
            )
            result.update({"state": "supported", "frame_count": count, "image": base64.b64encode(encoded).decode("ascii")})
        except ValueError:
            result["state"] = "rejected"
        except camera_series.CameraSeriesCancelled:
            result["state"] = "cancelled"
        except Exception:
            log.exception("Bounded camera series failed")
        result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        if isinstance(request_id, str):
            MEDIA_REQUESTS.finish(request_id)
        self._publish_response("camera_series", result)

    def _do_media_cancel(self, payload: dict) -> None:
        request_id = payload.get("request_id")
        state = MEDIA_REQUESTS.cancel(request_id)
        self._publish_response("media_cancel", {
            "type": "media_cancel_result", "schema": "xider.media-cancel.v1",
            "feature_id": "D02-F22", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            "state": state, "source": "agent_process_local",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })

    def _do_camera_quality(self, payload: dict) -> None:
        """Analyze one explicitly requested camera frame locally; never send pixels."""
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
            "feature_id": "D02-F13", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": camera_quality.ANALYSIS_SOURCE,
            "checked_at": None, "data": None,
        }
        cap = None
        frame = None
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                result["detail"] = "camera_unavailable_or_permission_unknown"
            else:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                ok, frame = cap.read()
                if ok and frame is not None:
                    result["data"] = camera_quality.analyze_frame(frame, cv2)
                    result["state"] = "supported"
                else:
                    result["detail"] = "single_frame_capture_failed"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "opencv_dependency_unavailable"
            result["detail"] = "camera_analysis_dependency_missing"
        except Exception:
            log.exception("Camera quality analysis failed")
            result["detail"] = "camera_analysis_failed"
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    log.debug("Camera release failed", exc_info=True)
            frame = None
            result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self._publish_response("camera_quality", result)

    def _do_cpu_history(self, payload: dict) -> None:
        """Sample local CPU use for one bounded, owner-selected window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        result = {
            "type": "cpu_history", "schema": "xider.cpu-history.v1", "feature_id": "D01-F02",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.cpu_percent", "checked_at": checked_at, "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("cpu_history", result)
            return
        try:
            samples = []
            for index in range(period // 5):
                value = psutil.cpu_percent(interval=5)
                if (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 100
                ):
                    raise ValueError("invalid_cpu_sample")
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    "percent": round(float(value), 1),
                })
            result.update(
                state="supported",
                data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
            )
        except Exception:
            log.exception("CPU history sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("cpu_history", result)

    def _do_memory_history(self, payload: dict) -> None:
        """Sample local RAM and swap use for one bounded, owner-selected window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.virtual_memory+psutil.swap_memory",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("memory_history", result)
            return
        try:
            samples, swap_states = [], []
            for index in range(period // 5):
                time.sleep(5)
                ram = psutil.virtual_memory().percent
                if (isinstance(ram, bool) or not isinstance(ram, (int, float))
                        or not math.isfinite(ram) or not 0 <= ram <= 100):
                    raise ValueError("invalid_ram_sample")
                swap_percent, swap_state = None, "unknown"
                try:
                    swap = psutil.swap_memory()
                    total, value = getattr(swap, "total", None), getattr(swap, "percent", None)
                    if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
                        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                                and math.isfinite(value) and 0 <= value <= 100):
                            swap_percent, swap_state = round(float(value), 1), "supported"
                    elif total == 0:
                        swap_state = "unsupported"
                except Exception:
                    log.info("Swap telemetry unavailable for memory history", exc_info=True)
                swap_states.append(swap_state)
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    "ram_percent": round(float(ram), 1),
                    "swap_percent": swap_percent,
                })
            swap_state = (
                swap_states[0] if swap_states and all(value == swap_states[0] for value in swap_states)
                else "unknown"
            )
            result.update(
                state="supported",
                data={"period_seconds": period, "sample_interval_seconds": 5,
                      "swap_state": swap_state, "samples": samples},
            )
        except Exception:
            log.exception("Memory history sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("memory_history", result)

    def _do_memory_pressure_history(self, payload: dict) -> None:
        """Sample Windows memory-resource threshold notifications for a bounded window."""
        period = payload.get("period_seconds")
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
            "feature_id": "D01-F05", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": _MEMORY_PRESSURE_SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        if type(period) is not int or period not in {15, 30, 60}:
            result["detail"] = "period_not_supported"
            self._publish_response("memory_pressure_history", result)
            return
        try:
            samples = []
            for index in range(period // 5):
                time.sleep(5)
                samples.append({
                    "offset_seconds": (index + 1) * 5,
                    **_memory_pressure_history_sample(_read_memory_pressure_sample()),
                })
            result.update(
                state="supported" if any(item["state"] in {"low", "intermediate", "high"} for item in samples)
                else "unknown",
                data={"period_seconds": period, "sample_interval_seconds": 5, "samples": samples},
            )
        except Exception:
            log.exception("Memory pressure sampling failed")
            result["detail"] = "sampling_failed"
        self._publish_response("memory_pressure_history", result)

    def _do_thermal_snapshot(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_thermal_snapshot()
        result = {
            "type": "thermal_snapshot", "schema": "xider.thermal-snapshot.v1",
            "feature_id": "D01-F06", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"sensors": snapshot["sensors"]},
        }
        self._publish_response("thermal_snapshot", result)

    def _do_battery_health(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_battery_health_snapshot()
        result = {
            "type": "battery_health_snapshot", "schema": "xider.battery-health.v1",
            "feature_id": "D01-F07", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"batteries": snapshot["batteries"]},
        }
        self._publish_response("battery_health", result)

    def _do_battery_cycles(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_battery_cycle_snapshot()
        self._publish_response("battery_cycles", {
            "type": "battery_cycle_snapshot", "schema": "xider.battery-cycles.v1",
            "feature_id": "D01-F08", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"batteries": snapshot["batteries"]},
        })

    def _do_energy_usage(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_energy_usage_snapshot()
        self._publish_response("energy_usage", {
            "type": "energy_usage_snapshot", "schema": "xider.energy-usage.v1",
            "feature_id": "D01-F09", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {
                "unit": snapshot.get("unit") if snapshot["state"] == "supported" else None,
                "apps": snapshot.get("apps", []),
            },
        })

    def _do_sleep_wake_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_sleep_wake_history_snapshot()
        self._publish_response("sleep_wake_history", {
            "type": "sleep_wake_history_snapshot", "schema": "xider.sleep-wake-history.v1",
            "feature_id": "D01-F10", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"history_days": 7, "events": snapshot["events"]},
        })

    def _do_startup_duration(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_startup_duration_snapshot()
        self._publish_response("startup_duration", {
            "type": "startup_duration_snapshot", "schema": "xider.startup-duration.v1",
            "feature_id": "D01-F11", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"duration_ms": snapshot.get("duration_ms"), "completed_at": snapshot.get("completed_at")},
        })

    def _do_pending_restart(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_pending_restart_snapshot()
        self._publish_response("pending_restart", {
            "type": "pending_restart_snapshot", "schema": "xider.pending-restart.v1",
            "feature_id": "D01-F12", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {
                "pending": snapshot.get("pending"),
                "reasons": snapshot.get("reasons", []),
                "completeness": snapshot.get("completeness", "unknown"),
            },
        })

    def _do_os_support(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        identity = _read_os_support_identity()
        self._publish_response("os_support", {
            "type": "os_support_snapshot", "schema": "xider.os-support.v1",
            "feature_id": "D01-F13", "device_id": DEVICE_ID, "request_id": request_id,
            "state": identity["state"], "source": "windows_current_version_registry",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"family": identity["family"], "release": identity["release"], "edition": identity["edition"]},
        })

    def _do_hardware_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        inventory = _read_hardware_inventory_snapshot()
        self._publish_response("hardware_inventory", {
            "type": "hardware_inventory_snapshot", "schema": "xider.hardware-inventory.v1",
            "feature_id": "D01-F14", "device_id": DEVICE_ID, "request_id": request_id,
            "state": inventory["state"], "source": "windows_cim_hardware_inventory",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {key: value for key, value in inventory.items() if key != "state"},
        })

    def _do_driver_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        inventory = _read_driver_inventory_snapshot()
        self._publish_response("driver_inventory", {
            "type": "driver_inventory_snapshot", "schema": "xider.driver-inventory.v1",
            "feature_id": "D01-F15", "device_id": DEVICE_ID, "request_id": request_id,
            "state": inventory["state"], "source": "windows_cim_signed_drivers",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"drivers": inventory["drivers"]},
        })

    def _do_installed_app_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_installed_app_inventory_snapshot()
        self._publish_response("installed_app_inventory", {
            "type": "installed_app_inventory_snapshot", "schema": "xider.installed-app-inventory.v1",
            "feature_id": "D01-F16", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "windows_uninstall_registry_projection",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"apps": snapshot["apps"]},
        })

    def _do_startup_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_startup_inventory_snapshot()
        self._publish_response("startup_inventory", {
            "type": "startup_inventory_snapshot", "schema": "xider.startup-inventory.v1",
            "feature_id": "D01-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "windows_run_keys_and_startup_folder_names",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"entries": snapshot["entries"]},
        })

    def _do_service_inventory(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_service_inventory_snapshot()
        self._publish_response("service_inventory", {
            "type": "service_inventory_snapshot", "schema": "xider.service-inventory.v1",
            "feature_id": "D01-F18", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": "windows_cim_service_status",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"services": snapshot["services"]},
        })

    def _do_crash_summary(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        summary = _read_crash_summary_snapshot()
        self._publish_response("crash_summary", {
            "type": "crash_summary_snapshot", "schema": "xider.crash-summary.v1",
            "feature_id": "D01-F19", "device_id": DEVICE_ID, "request_id": request_id,
            "state": summary["state"], "source": "windows_event_log_provider_event_aggregate",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"events": summary["events"]},
        })

    def _do_smart_trend(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_smart_trend_snapshot()
        self._publish_response("smart_trend", {
            "type": "smart_trend_snapshot", "schema": "xider.smart-trend.v1",
            "feature_id": "D01-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"disks": snapshot["disks"]},
        })

    def _do_volume_topology(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_volume_topology_snapshot()
        self._publish_response("volume_topology", {
            "type": "volume_topology_snapshot", "schema": "xider.volume-topology.v1",
            "feature_id": "D01-F21", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"volumes": snapshot["volumes"]},
        })

    def _do_clock_drift(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        snapshot = _read_clock_drift_snapshot()
        self._publish_response("clock_drift", {
            "type": "clock_drift_snapshot", "schema": "xider.clock-drift.v1",
            "feature_id": "D01-F22", "device_id": DEVICE_ID, "request_id": request_id,
            "state": snapshot["state"], "source": snapshot["source"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"drift_ms": snapshot["drift_ms"], "sample_ms": snapshot["sample_ms"]},
        })

    def _do_sysinfo(self, payload: dict) -> None:
        """Собрать расширенную информацию о системе и отправить в MQTT."""
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        boot = psutil.boot_time()
        uptime_sec = int(time.time() - boot)
        hours, remainder = divmod(uptime_sec, 3600)
        minutes, _ = divmod(remainder, 60)
        uptime_str = f"{hours}ч {minutes}мин"
        battery = _format_battery(psutil.sensors_battery())

        self._publish_response("sysinfo", {
            "type": "sysinfo",
            "device_id": DEVICE_ID,
            "hostname": socket.gethostname(),
            "mac": _primary_mac(),
            "os": PLATFORM,
            "cpu_percent": round(cpu, 1),
            "ram_used_gb": round(mem.used / (1024 ** 3), 1),
            "ram_total_gb": round(mem.total / (1024 ** 3), 1),
            "ram_percent": round(mem.percent, 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_percent": round(disk.percent, 1),
            "boot_time_sec": int(boot),
            "uptime": uptime_str,
            "battery": battery,
            "regional_settings": _regional_settings_snapshot(payload.get("id")),
        })
        log.info("Sysinfo отправлен")

    def _do_volume_history(self, payload: dict) -> None:
        """Report local filesystem usage and cautious trend from prior owner queries."""
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        now = time.time()
        result = {
            "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "psutil.disk_partitions+psutil.disk_usage",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            partitions = list(psutil.disk_partitions(all=False))[:64]
            volumes = []
            for part in partitions:
                mountpoint = getattr(part, "mountpoint", None)
                device = getattr(part, "device", None)
                if not isinstance(mountpoint, str) or not mountpoint or not isinstance(device, str):
                    continue
                try:
                    usage = psutil.disk_usage(mountpoint)
                    total, used = getattr(usage, "total", None), getattr(usage, "used", None)
                    if type(total) is not int or type(used) is not int or total <= 0 or not 0 <= used <= total:
                        raise ValueError("invalid_volume_usage")
                    volume = _volume_history_measurement(device, mountpoint, total, used, now)
                    volume.update(
                        mountpoint=mountpoint[:512], filesystem=str(getattr(part, "fstype", "") or "")[:32],
                        state="supported",
                    )
                except Exception:
                    log.info("Volume usage unavailable for a mounted filesystem", exc_info=True)
                    volume = {
                        "mountpoint": mountpoint[:512], "filesystem": str(getattr(part, "fstype", "") or "")[:32],
                        "state": "unknown", "trend_state": "unknown", "total_bytes": None,
                        "used_bytes": None, "free_bytes": None, "used_percent": None,
                        "observed_span_seconds": None, "delta_used_bytes": None,
                        "used_bytes_per_hour": None, "estimated_hours_to_full": None,
                        "history_scope": "agent_process",
                    }
                volumes.append(volume)
            result.update(
                state="supported" if any(item.get("state") == "supported" for item in volumes)
                else "unsupported" if not partitions else "unknown",
                data={"history_minimum_span_seconds": _VOLUME_HISTORY_MIN_SPAN_SECONDS,
                      "history_scope": "agent_process", "volumes": volumes},
            )
            if not partitions:
                result["detail"] = "no_mountable_volumes"
        except Exception:
            log.exception("Volume history collection failed")
            result["detail"] = "collection_failed"
        self._publish_response("volume_history", result)


    def _do_processes(self, payload: dict) -> None:
        """Топ-5 процессов по памяти (совместимо с ботом)."""
        procs = list(psutil.process_iter(["pid", "name", "memory_percent"]))
        top_n = int(payload.get("top_n", 5) or 5)
        top_n = max(1, min(top_n, 25))
        result = _format_processes(procs, top_n=top_n)
        self._publish_response("processes", {
            "type": "processes",
            "device_id": DEVICE_ID,
            "lines": result["lines"],
            "top": result["top"],
        })
        log.info("Список процессов отправлен")

    def _do_battery(self, payload: dict) -> None:
        """Состояние батареи/питания (для ноутбуков)."""
        battery = _format_battery(psutil.sensors_battery())
        self._publish_response("battery", {
            "type": "battery",
            "device_id": DEVICE_ID,
            **battery,
        })
        log.info("Состояние батареи отправлено")

    def _do_network(self, payload: dict) -> None:
        """Сводка по сетевым интерфейсам и трафику."""
        result = _format_network(
            psutil.net_if_addrs(),
            psutil.net_if_stats(),
            psutil.net_io_counters(pernic=True),
        )
        self._publish_response("network", {
            "type": "network",
            "device_id": DEVICE_ID,
            "interfaces": result["interfaces"],
            "totals": result["totals"],
        })
        log.info("Сетевая сводка отправлена")

    def _do_services(self, payload: dict) -> None:
        """Сводка по службам Windows (только чтение)."""
        result = _format_services(psutil.win_service_iter())
        self._publish_response("services", {
            "type": "services",
            "device_id": DEVICE_ID,
            "total": result["total"],
            "running": result["running"],
            "stopped": result["stopped"],
            "failed_auto_start": result["failed_auto_start"],
        })
        log.info("Сводка служб отправлена")

    def _do_capabilities(self, payload: dict) -> None:
        """Сообщить серверу, какие возможности поддерживает этот клиент.

        НЕ содержит секретов (SHARED_KEY намеренно исключён).
        """
        feature_status = _detect_feature_status()
        response = {
            "type": "capabilities",
            "device_id": DEVICE_ID,
            "platform": PLATFORM,
            "version": VERSION,
            "hostname": socket.gethostname(),
            "commands": sorted(SUPPORTED_COMMANDS),
            "features": _detect_features(),
            "feature_status": feature_status,
            **_capability_snapshot(feature_status),
            "shell_runner": _shell_runner_diagnostic(self),
        }
        if _command_approval_protocol_enabled(self):
            response["command_approval_protocol"] = 1
        self._publish_response("capabilities", response)
        log.info("Отчёт о возможностях отправлен")

    def _do_agent_files(self, payload: dict) -> None:
        """Return allowlisted component metadata only; never return file contents."""
        try:
            report = agent_file_inventory.collect_agent_file_inventory(
                agent_version=VERSION,
            )
            result = {
                "type": "agent_files", "device_id": DEVICE_ID,
                "id": payload.get("id"), "request_id": payload.get("id"),
                "ok": report.get("state") in {"available", "partial"},
                **report,
            }
        except Exception:
            log.exception("Agent-file inventory collection failed")
            result = {
                "type": "agent_files", "device_id": DEVICE_ID, "ok": False,
                "id": payload.get("id"), "request_id": payload.get("id"),
                "schema": agent_file_inventory.SCHEMA, "state": "error",
                "components": [], "protection": {
                    "obfuscation_state": "unknown", "integrity_state": "unverified",
                },
            }
        self._publish_response("output", result)

    def _do_agent_file_get(self, payload: dict) -> None:
        """Return bounded contents for a fixed, readable text-file ID only."""
        raw_request_id = payload.get("id")
        request_id = (
            raw_request_id
            if isinstance(raw_request_id, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw_request_id)
            else None
        )
        requested_file_id = payload.get("file_id")
        result = agent_file_inventory.read_allowlisted_text_file(requested_file_id)
        response = {
            "type": "agent_file_get",
            "schema": agent_file_inventory.TEXT_FILE_SCHEMA,
            "device_id": DEVICE_ID,
            "id": request_id,
            "request_id": request_id,
            "file_id": result.get("file_id"),
            "state": result.get("state", "unavailable"),
            "content": result.get("content", ""),
            "truncated": bool(result.get("truncated", False)),
        }
        self._publish_response("output", response)

    def _do_agent_logs(self, payload: dict) -> None:
        """Return a bounded, redacted tail of the local agent log on owner request."""
        result = {
            "type": "agent_logs", "device_id": DEVICE_ID, "ok": False,
            "state": "unavailable", "lines": 0, "text": "",
        }
        try:
            secret_patterns = (
                re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization|private[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"),
                re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
            )
            clean = []
            truncated = False
            unavailable_sources = []
            for label, log_path in (
                ("agent", CONFIG_DIR / "xgent.log"),
                ("guardian", CONFIG_DIR / "guardian.log"),
                ("safari", CONFIG_DIR / "safari.log"),
                ("siri", CONFIG_DIR / "siri.log"),
            ):
                try:
                    with log_path.open("rb") as stream:
                        stream.seek(0, os.SEEK_END)
                        size = stream.tell()
                        stream.seek(max(0, size - 16 * 1024), os.SEEK_SET)
                        raw = stream.read(16 * 1024)
                except FileNotFoundError:
                    continue
                except OSError:
                    unavailable_sources.append(label)
                    continue
                truncated = truncated or size > 16 * 1024
                text = raw.decode("utf-8", errors="replace")
                all_rows = text.splitlines()
                truncated = truncated or len(all_rows) > 40
                for row in all_rows[-40:]:
                    for pattern in secret_patterns:
                        row = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex and match.lastindex >= 3 else "Bearer [REDACTED]", row)
                    clean.append(f"[{label}] {row[-480:]}")
            if not clean and unavailable_sources:
                result.update(state="unavailable", text="Не удалось прочитать локальные журналы XIDER-компонентов.")
                self._publish_response("output", result)
                return
            if not clean:
                result.update(state="missing", text="Локальные журналы XIDER-компонентов ещё не созданы.")
                self._publish_response("output", result)
                return
            joined = "\n".join(clean)
            truncated = truncated or len(clean) > 80 or len(joined) > 8000
            result.update(
                ok=True, state="available", lines=min(len(clean), 80),
                truncated=truncated, partial=bool(unavailable_sources),
                text=joined[-8000:],
            )
        except FileNotFoundError:
            result.update(state="missing", text="Локальные журналы XIDER-компонентов ещё не созданы.")
        except OSError:
            result.update(state="unavailable", text="Не удалось прочитать локальные журналы XIDER-компонентов.")
        self._publish_response("output", result)

    def _do_permission_settings(self, payload: dict) -> dict:
        """Request Windows location consent locally, or open a fixed privacy page."""
        settings = {
            "camera": "ms-settings:privacy-webcam",
            "microphone": "ms-settings:privacy-microphone",
            "location": "ms-settings:privacy-location",
        }
        permission = payload.get("permission")
        uri = settings.get(permission) if isinstance(permission, str) else None
        result = {
            "type": "permission_settings",
            "device_id": DEVICE_ID,
            "permission": permission if uri else "unknown",
            "ok": False,
        }
        command_id = payload.get("id")
        if isinstance(command_id, str) and command_id:
            result["id"] = command_id
        opener = getattr(os, "startfile", None)
        if permission == "location":
            try:
                from location_adapter import native_backend
                permission_state, _enabled = native_backend().diagnose()
            except Exception:
                permission_state = "unknown"
            if permission_state in {"granted", "denied"}:
                # Windows does not repeat a decision prompt; let the owner
                # change or revoke it in the system Privacy page instead.
                try:
                    if callable(opener):
                        opener(uri)
                        result.update(
                            ok=True, state="settings_opened", permission_state=permission_state,
                            text="Открыл настройки геолокации Windows. Там можно изменить или отозвать решение; координаты не запрашивались.",
                        )
                    else:
                        result.update(state="unavailable", text="Не удалось открыть настройки геолокации Windows.")
                except OSError:
                    result.update(state="unknown", text="Windows не подтвердила открытие настроек геолокации.")
                self._publish_response("permission_settings", result)
                return result
            try:
                from permission_prompt import request_windows_location_consent
                outcome = request_windows_location_consent()
                state = outcome.get("state") if isinstance(outcome, dict) else "unknown"
                result["state"] = state if state in {"granted", "denied", "cancelled", "unknown", "unavailable", "unsupported"} else "unknown"
                result["ok"] = result["state"] in {"granted", "denied", "cancelled", "unknown"}
                result["text"] = str(outcome.get("text", "Состояние разрешения неизвестно."))[:500]
                if result["state"] in {"unavailable", "unsupported"} and callable(opener):
                    opener(uri)
                    result["settings_opened"] = True
                    result["ok"] = True
                    result["text"] += " Открыл системные настройки; выдачу доступа нужно подтвердить там вручную."
            except Exception:
                result["state"] = "unavailable"
                result["text"] = "Локальный запрос Windows недоступен; разрешение не выдано. Открой настройки геолокации вручную."
                try:
                    if callable(opener):
                        opener(uri)
                        result["settings_opened"] = True
                        result["ok"] = True
                except OSError:
                    pass
            self._publish_response("permission_settings", result)
            return result
        if uri and callable(opener):
            try:
                opener(uri)
                result["ok"] = True
                result["state"] = "settings_opened"
                result["text"] = (
                    "Открыл Параметры конфиденциальности. Для камеры и микрофона включи доступ "
                    "классических приложений; отдельного окна Allow/Don't Allow у этого desktop-агента нет. "
                    "Агент сам разрешения не меняет."
                )
            except OSError:
                result["state"] = "unknown"
                result["text"] = "Не удалось открыть Параметры Windows; открой нужный раздел вручную."
        elif uri:
            result["text"] = "Открытие Параметров Windows доступно только в Windows-сеансе; ничего не изменено."
        else:
            result["text"] = "Неизвестный или неподдерживаемый раздел настроек; команда отклонена."
        self._publish_response("permission_settings", result)
        return result

    def _do_clipboard(self, payload: dict) -> None:
        """Чтение буфера обмена (без записи)."""
        import pyperclip

        text = pyperclip.paste() or ""
        self._publish_response("clipboard", {
            "type": "clipboard",
            "device_id": DEVICE_ID,
            "text": text,
        })
        log.info("Буфер обмена отправлен (%d символов)", len(text))

    def _do_clipboard_set(self, payload: dict) -> None:
        """Записать текст в буфер обмена."""
        import pyperclip

        text = str(payload.get("text", ""))
        pyperclip.copy(text)
        self._publish_response("clipboard_set", {
            "type": "clipboard_set",
            "device_id": DEVICE_ID,
            "length": len(text),
        })
        log.info("В буфер обмена записано %d символов", len(text))

    def _do_kill_process(self, payload: dict) -> None:
        """Завершить процесс по PID (terminate, затем kill)."""
        pid = int(payload.get("pid", 0) or 0)
        proc = psutil.Process(pid)
        name = proc.name()
        log.info("kill_process: pid=%s (%s)", pid, name)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        self._publish_response("kill_process", {
            "type": "kill_process",
            "device_id": DEVICE_ID,
            "pid": pid,
            "name": name,
            "killed": True,
        })

    def _do_disks(self, payload: dict) -> None:
        """Список дисков с размерами."""
        disks = _format_disks(psutil.disk_partitions())
        self._publish_response("disks", {
            "type": "disks",
            "device_id": DEVICE_ID,
            "disks": disks,
            "lines": [
                f"{d['device']} {d['free_gb']}/{d['total_gb']} ГБ свободно ({d['percent']}%)"
                for d in disks
            ],
        })
        log.info("Список дисков отправлен")

        log.info("Список дисков отправлен")

    # ---------------------------------------------------------------
    #  Новые команды волны 1: файлы, система, приколы
    # ---------------------------------------------------------------

    def _do_dir_list(self, payload: dict) -> None:
        path = (payload.get("path") or "~").strip()
        result = _list_directory(path)
        self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, **result})
        log.info("dir_list: %s (найдено %s)", result.get("path"), result.get("total"))

    def _do_file_get(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if not path:
            return
        try:
            max_bytes = 45_000_000
            size = os.path.getsize(path)
            if size > max_bytes:
                self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                    "ok": False, "error": f"файл {size} байт > лимит"})
                return
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                "ok": True, "path": path, "b64": b64,
                                                "name": os.path.basename(path),
                                                # Canonical schema; aliases keep older bots compatible.
                                                "data": b64, "filename": os.path.basename(path)})
            log.info("file_get: %s (%d байт)", path, size)
        except OSError as exc:
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                "ok": False, "error": str(exc)})

    def _do_file_put(self, payload: dict) -> None:
        try:
            content = _decode_file_put_payload(payload)
            name = os.path.basename(payload.get("name") or "file.bin")
            raw_path = (payload.get("path") or "").strip()
            if raw_path:
                target = os.path.expanduser(os.path.expandvars(raw_path))
            else:
                target_dir = (payload.get("dir") or "~").strip()
                base = os.path.expanduser(os.path.expandvars(target_dir))
                target = os.path.join(base, name)
            _write_file_put_atomic(target, content)
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID,
                                                "ok": True, "path": target})
            log.info("file_put: %s", target)
        except (OSError, TypeError, ValueError) as exc:
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID,
                                                "ok": False, "error": str(exc)})

    def _do_file_del(self, payload: dict) -> None:
        raw_path = payload.get("path")
        path = raw_path.strip() if isinstance(raw_path, str) else ""
        if not path:
            self._publish_response("file_del", {
                "type": "file_del", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "error": "invalid_or_missing_path",
            })
            return
        try:
            os.remove(os.path.expanduser(os.path.expandvars(path)))
            ok, err = True, None
        except OSError as exc:
            ok, err = False, str(exc)
        self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID,
                                            "ok": ok, "path": path, "error": err})

    def _do_find_file(self, payload: dict) -> None:
        pattern = (payload.get("pattern") or "").strip()
        root = (payload.get("root") or "~").strip()
        result = _find_matches(pattern, root) if pattern else {"ok": False, "matches": [], "total": 0}
        self._publish_response("find_file", {"type": "find_file", "device_id": DEVICE_ID, **result})

    def _do_path_open(self, payload: dict) -> None:
        raw_path = payload.get("path")
        path = raw_path.strip() if isinstance(raw_path, str) else ""
        if not path:
            self._publish_response("path_open", {
                "type": "path_open", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "error": "invalid_or_missing_path",
            })
            return
        try:
            os.startfile(os.path.expanduser(path))  # type: ignore[attr-defined]
            ok, err = True, None
        except (OSError, AttributeError) as exc:
            ok, err = False, str(exc)
        self._publish_response("path_open", {"type": "path_open", "device_id": DEVICE_ID,
                                             "ok": ok, "path": path, "error": err})

    def _do_ext_ip(self, payload: dict) -> None:
        import urllib.request
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "ext_ip", "schema": "xider.external-ip.v1", "feature_id": "D04-F30",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": "ipify", "ip": "",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=6) as resp:
                candidate = resp.read(65).decode("ascii", "ignore").strip()
            address = ipaddress.ip_address(candidate)
            if address.is_global and str(address) == candidate:
                result.update(state="available", ip=candidate)
        except Exception:
            # Provider details may contain URLs, proxy configuration, or local
            # network metadata; return an explicit unknown state instead.
            pass
        result["text"] = f"🌍 Внешний IP: {result['ip']}" if result["ip"] else "Внешний IP не подтверждён"
        self._publish_response("ext_ip", result)

    def _do_screen_off(self, payload: dict) -> None:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        log.info("Монитор выключен")

    def _do_screensaver_on(self, payload: dict) -> None:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF140, 0)
        log.info("Screensaver запущен")

    def _get_master_volume(self):
        """Универсальное получение интерфейса IAudioEndpointVolume (поддержка pycaw всех версий)."""
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):
            return speakers.EndpointVolume
        dev = getattr(speakers, "_dev", speakers)
        interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _do_volume_set(self, payload: dict) -> None:
        """Установить общую громкость 0-100 (pycaw)."""
        try:
            requested = payload.get("level", 50)
            if isinstance(requested, bool):
                raise ValueError("boolean volume")
            level = max(0, min(100, int(requested)))
        except (TypeError, ValueError, OverflowError):
            result = {
                "type": "volume_set", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "level": None, "current": None,
                "source": "pycaw.IAudioEndpointVolume",
                "text": "⚠️ Не удалось изменить громкость: запрос содержит некорректное значение.",
            }
            self._publish_response("volume_set", result)
            self._publish_response("output", result)
            return

        volume = None
        setter_error = None
        try:
            volume = self._get_master_volume()
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        except Exception as exc:
            log.warning("Ошибка установки громкости: %s", exc)
            setter_error = exc

        current = None
        try:
            if volume is None:
                volume = self._get_master_volume()
            observed = float(volume.GetMasterVolumeLevelScalar())
            if math.isfinite(observed) and 0.0 <= observed <= 1.0:
                current = round(observed * 100)
        except Exception as exc:
            log.warning("Ошибка чтения фактической громкости: %s", exc)

        if current == level:
            ok, state = True, "confirmed"
            text = f"🎚 Громкость {current}%; уровень подтверждён системой."
        elif current is None:
            ok, state = False, "unknown"
            text = "⚠️ Запрос громкости отправлен, но система не вернула проверяемый уровень. Результат неизвестен."
        else:
            ok, state = False, "failed"
            text = f"⚠️ Запрошено {level}%, но система сообщила {current}%; изменение не подтверждено."
        if setter_error is not None and current != level and current is not None:
            log.debug("Volume setter failed and readback differs from requested value: %s", setter_error)

        result = {
            "type": "volume_set",
            "device_id": DEVICE_ID,
            "ok": ok,
            "state": state,
            "level": level,
            "current": current,
            "source": "pycaw.IAudioEndpointVolume",
            "text": text,
        }
        self._publish_response("volume_set", result)
        self._publish_response("output", result)
        log.info("Громкость: %s", text)

    def _do_microphone_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
            "feature_id": "D02-F15", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_query_devices",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"inputs": []},
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"]["inputs"] = microphone_devices.project_input_devices(sd.query_devices())
            result["state"] = "supported" if result["data"]["inputs"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Microphone input enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("microphone_catalog", result)

    def _do_microphone_level(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_level", "schema": "xider.microphone-level.v1",
            "feature_id": "D02-F16", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_input_stream_level",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            import sounddevice as sd
            import microphone_level
            levels = microphone_level.measure_input_level(sd, payload.get("input_device_id"), duration_seconds=1.0)
            if levels is None:
                result["state"] = "unavailable"
                result["detail"] = "selected_microphone_unavailable"
            else:
                result["state"] = "supported"
                result["data"] = {**levels, "observation_ms": 1000}
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Microphone input-level measurement failed")
            result["state"] = "unknown"
            result["detail"] = "microphone_level_measurement_failed"
        self._publish_response("microphone_level", result)

    def _do_microphone_balance(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_balance", "schema": "xider.microphone-balance.v1",
            "feature_id": "D02-F21", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_stereo_input_rms",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": None,
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"] = microphone_devices.measure_stereo_channel_balance(
                sd, input_device_id=payload.get("input_device_id"),
                input_device_name=payload.get("input_device_name"),
            )
            result["data"].pop("state", None)
            result["state"] = "supported"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            if str(exc) == "selected_stereo_microphone_unavailable":
                result["state"] = "unavailable"
                result["detail"] = str(exc)
            else:
                result["detail"] = "stereo_balance_measurement_failed"
        except Exception:
            log.exception("Microphone stereo-balance measurement failed")
            result["detail"] = "stereo_balance_measurement_failed"
        self._publish_response("microphone_balance", result)

    def _do_audio_output_catalog(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
            "feature_id": "D02-F19", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "sounddevice_query_devices",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "data": {"outputs": []},
        }
        try:
            import sounddevice as sd
            import microphone_devices
            result["data"]["outputs"] = microphone_devices.project_output_devices(sd.query_devices())
            result["state"] = "supported" if result["data"]["outputs"] else "unavailable"
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except Exception:
            log.exception("Audio output enumeration failed")
            result["state"] = "unknown"
            result["data"] = None
        self._publish_response("audio_output_catalog", result)

    def _do_audio_output_switch(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "audio_output_switch", "schema": "xider.audio-output-switch.v1",
            "feature_id": "D02-F18", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "data": None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            import sounddevice as sd
            import audio_output_control
            data = audio_output_control.switch_system_output(
                sd, output_id=payload.get("output_device_id"),
                output_name=payload.get("output_device_name"),
            )
            result["state"] = data.get("state", "unknown")
            if result["state"] in {"changed", "unchanged"}:
                result["data"] = {
                    "output_name": data["output_name"],
                    "previous_output_name": data["previous_output_name"],
                }
        except ImportError:
            result["state"] = "unsupported"
        except Exception:
            log.exception("System audio output switch failed")
        self._publish_response("audio_output_switch", result)

    def _do_microphone_test(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_test", "schema": "xider.microphone-test.v1",
            "feature_id": "D02-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "local_audio_roundtrip", "data": None,
        }
        try:
            import sounddevice as sd
            import soundfile as sf
            import microphone_devices
            data = microphone_devices.run_microphone_roundtrip(
                sd, sf, input_device_id=payload.get("input_device_id"),
                output_device_id=payload.get("output_device_id"),
                input_device_name=payload.get("input_device_name"),
                output_device_name=payload.get("output_device_name"),
            )
            result["state"] = data["state"]
            result["data"] = {"duration_ms": data["duration_ms"], "sample_rate": data["sample_rate"]}
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            result["state"] = "unavailable"
            result["detail"] = str(exc) if str(exc) in {"selected_audio_device_unavailable", "selected_audio_device_changed"} else "audio_roundtrip_failed"
        except Exception:
            log.exception("Local microphone/output test failed")
            result["state"] = "unknown"
            result["detail"] = "audio_roundtrip_failed"
        self._publish_response("microphone_test", result)

    def _do_microphone_preview(self, payload: dict) -> None:
        request_id = payload.get("id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id) else None
        result = {
            "type": "microphone_preview", "schema": "xider.microphone-preview.v1",
            "feature_id": "D02-F20", "device_id": DEVICE_ID, "request_id": request_id,
            "state": "unknown", "source": "local_playback_before_delivery", "audio": None,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            import sounddevice as sd
            import soundfile as sf
            import microphone_devices
            duration = payload.get("duration")
            audio = microphone_devices.preview_microphone_recording(
                sd, sf, input_device_id=payload.get("input_device_id"),
                output_device_id=payload.get("output_device_id"), duration_seconds=duration,
                input_device_name=payload.get("input_device_name"),
                output_device_name=payload.get("output_device_name"),
            )
            result["state"] = "previewed"
            result["duration_ms"] = duration * 1000
            result["audio"] = base64.b64encode(audio).decode("ascii")
        except ImportError:
            result["state"] = "unsupported"
            result["source"] = "sounddevice_dependency_unavailable"
        except ValueError as exc:
            result["state"] = "unavailable"
            allowed = {"selected_audio_device_unavailable", "selected_audio_device_changed",
                       "microphone_preview_duration_invalid", "microphone_preview_audio_invalid"}
            result["detail"] = str(exc) if str(exc) in allowed else "microphone_preview_failed"
        except Exception:
            log.exception("Local microphone preview failed")
            result["state"] = "unknown"
            result["detail"] = "microphone_preview_failed"
        self._publish_response("microphone_preview", result)

    def _do_mic(self, payload: dict) -> None:
        """Запись 5 секунд с микрофона по умолчанию, base64 ogg в MQTT."""
        try:
            import sounddevice as sd
            import soundfile as sf
            if "input_device_id" in payload:
                import microphone_devices
                input_index = microphone_devices.resolve_input_index(sd.query_devices(), payload.get("input_device_id"))
                if input_index is None:
                    self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID,
                                                    "audio": None, "state": "unavailable",
                                                    "error": "selected_microphone_unavailable"})
                    return
            else:
                input_index = None
            duration = int(payload.get("duration", 5))
            if duration <= 0 or duration > 60:
                duration = 5
            fs = 44100
            log.info("Запись микрофона %d сек @ %d Гц", duration, fs)
            kwargs = {"samplerate": fs, "channels": 1}
            if input_index is not None:
                kwargs["device"] = input_index
            recording = sd.rec(int(duration * fs), **kwargs)
            sd.wait()
            temp_file = media_temp.create_media_temp_file(".ogg")
            tmp_path = temp_file.name
            temp_file.close(retain_reservation=True)
            try:
                sf.write(tmp_path, recording, fs)
                temp_file.release()
                with open(tmp_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            finally:
                os.unlink(tmp_path)
                temp_file.release()
            self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID, "audio": b64})
            log.info("Аудиозапись отправлена")
        except Exception as exc:
            log.exception("Ошибка микрофона")
            self._publish_response("mic", {"type": "mic", "device_id": DEVICE_ID, "audio": None,
                                            "state": "unknown", "error": "microphone_capture_failed"})

    def _do_shell(self, payload: dict) -> None:
        """Выполнить shell-команду и вернуть вывод (лимит 20 сек)."""
        cmd = payload.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            self._publish_response("shell", {
                "type": "shell", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "error": "empty_or_invalid_command",
                "returncode": -1, "status": "rejected",
            })
            return
        if not _reserve_shell_approval(self, payload):
            self._publish_response("shell", {
                "type": "shell", "device_id": DEVICE_ID, "ok": False,
                "error": "approval_invalid_or_replayed", "returncode": -1, "status": "rejected",
            })
            log.warning("Shell request rejected by approval or replay guard")
            return
        try:
            execution = _load_command_approval_component("shell_execution")
        except ImportError:
            result = {"status": "error", "returncode": -2,
                      "output": "Execution component unavailable"}
        else:
            approved_id = (payload.get("approval") or {}).get("request_id")
            result = execution.execute_shell(cmd, payload.get("timeout"), request_id=approved_id)
        self._publish_response("shell", {"type": "shell", "device_id": DEVICE_ID, **result})
        log.info("Shell executed (rc=%s)", result["returncode"])

    def _do_shell_cancel(self, payload: dict) -> None:
        """Stop only a currently running, previously approved shell request."""
        target_id = payload.get("target_request_id")
        try:
            execution = _load_command_approval_component("shell_execution")
            status = execution.cancel_shell(target_id)
        except Exception:
            status = "unknown"
        if status not in {"cancelled", "unknown"}:
            status = "unknown"
        self._publish_response("shell_cancel", {
            "type": "shell_cancel", "device_id": DEVICE_ID,
            "target_request_id": target_id if isinstance(target_id, str) and len(target_id) <= 128 else "",
            "status": status,
        })

    def _do_hotkey(self, payload: dict) -> None:
        spec = (payload.get("keys") or "").strip()
        try:
            vks = map_hotkeys(spec)
        except ValueError as exc:
            self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID,
                                              "ok": False, "error": str(exc)})
            return
        KEYEVENTF_KEYUP = 0x0002

        def _press():
            for vk in vks:
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        threading.Thread(target=_press, name="xgent-hotkey", daemon=True).start()
        self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID,
                                          "ok": True, "keys": spec})

    def _do_proc_kill_name(self, payload: dict) -> None:
        result = _kill_by_name(payload.get("name") or "")
        log.info("proc_kill_name: %s", result)
        self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                  "device_id": DEVICE_ID, **result})

    def _do_wifi_info(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            parsed = _parse_wifi_output(out)
            text = f"📶 Wi-Fi: {parsed['ssid'] or '—'} (сигнал: {parsed['signal'] or '—'})"
        except Exception as exc:
            parsed, text = {}, f"⚠️ Wi-Fi недоступен: {exc}"
        self._publish_response("wifi_info", {"type": "wifi_info", "device_id": DEVICE_ID,
                                             "ok": bool(parsed), "text": text, **parsed})

    def _do_usb_devices(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -PresentOnly -Class USB | "
                 "Select-Object -First 25 -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL, timeout=20,
            )
            items = [ln.strip() for ln in out.splitlines() if ln.strip()]
            text = "🔌 USB:\n" + "\n".join(f"• {i}" for i in items[:25])
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ USB: {exc}", False
        self._publish_response("usb_devices", {"type": "usb_devices", "device_id": DEVICE_ID,
                                               "ok": ok, "text": text, "items": items})

    def _do_startup_list(self, payload: dict) -> None:
        names = []
        try:
            for hive in ("HKCU", "HKLM"):
                out = subprocess.check_output(
                    ["reg", "query",
                     f"{hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"],
                    text=True, stderr=subprocess.DEVNULL, timeout=10,
                )
                names.extend(_parse_reg_query(out))
            text = "🚀 Автозагрузка:\n" + ("\n".join(f"• {n}" for n in names[:30]) or "(пусто)")
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Автозагрузка: {exc}", False
        self._publish_response("startup_list", {"type": "startup_list", "device_id": DEVICE_ID,
                                                "ok": ok, "text": text, "items": names})

    def _do_env_get(self, payload: dict) -> None:
        raw = payload.get("names") or payload.get("name")
        names = [raw] if isinstance(raw, str) else (raw or None)
        env = _collect_env(names)
        text = "\n".join(f"{k}={v}" for k, v in sorted(env.items())) or "(пусто)"
        self._publish_response("env_get", {"type": "env_get", "device_id": DEVICE_ID,
                                           "ok": True, "text": text[:3500], "env": env})

    def _do_netstat(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netstat", "-an", "-p", "tcp"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            lines = [ln for ln in out.splitlines() if ln.strip()][:40]
            text = "🕸 TCP-соединения (первые 40):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ netstat: {exc}", False
        self._publish_response("netstat", {"type": "netstat", "device_id": DEVICE_ID,
                                           "ok": ok, "text": text[:3500]})

    def _do_download_url(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        dest = (payload.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Downloads", os.path.basename(url.split("?")[0]) or "xgent_download.bin"
        )
        try:
            result = subprocess.run(
                ["curl", "-L", "--max-time", "120", "-o", dest, url],
                capture_output=True, text=True, timeout=130,
            )
            ok = result.returncode == 0 and os.path.exists(dest)
            size = os.path.getsize(dest) if ok else 0
            text = f"⬇️ Скачано {size} байт → {dest}" if ok else f"⚠️ curl rc={result.returncode}"
        except Exception as exc:
            ok, size, text = False, 0, f"⚠️ {exc}"
        self._publish_response("download_url", {"type": "download_url", "device_id": DEVICE_ID,
                                                "ok": ok, "path": dest, "size": size, "text": text})

    def _do_wallpaper_set(self, payload: dict) -> None:
        """Установка обоев: по URL, из загруженного фото (b64) или случайного мема из сети."""
        url = (payload.get("url") or "").strip()
        b64 = (payload.get("b64") or "").strip()
        random_meme = payload.get("random_meme", False)

        # Автоматический бэкап текущих обоев перед заменой
        try:
            bf_path = CONFIG_DIR / "backup_wallpaper.txt"
            if not bf_path.exists():
                cur_buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.SystemParametersInfoW(0x0073, 512, cur_buf, 0)
                if cur_buf.value and os.path.exists(cur_buf.value):
                    bf_path.write_text(cur_buf.value, encoding="utf-8")
        except Exception as e:
            log.warning("Не удалось сохранить бэкап обоев: %s", e)

        img_path = os.path.join(tempfile.gettempdir(), "xgent_wallpaper.jpg")
        ok, err = False, None
        MEME_URLS = [
            "https://i.imgflip.com/4/30b1gx.jpg",
            "https://i.imgflip.com/4/1g8my4.jpg",
            "https://i.imgflip.com/4/1bij.jpg",
            "https://i.imgflip.com/4/26am.jpg",
            "https://i.imgflip.com/4/1ur9b0.jpg",
            "https://cataas.com/cat?width=1920&height=1080",
            "https://picsum.photos/1920/1080",
        ]

        try:
            import urllib.request
            if b64:
                raw_bytes = base64.b64decode(b64)
                with open(img_path, "wb") as f:
                    f.write(raw_bytes)
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены из переданного фото (размер %d байт)", len(raw_bytes))
            elif random_meme or not url:
                target_url = random.choice(MEME_URLS)
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(img_path, "wb") as out:
                    out.write(resp.read())
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены из случайного мема: %s", target_url)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(img_path, "wb") as out:
                    out.write(resp.read())
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены по URL: %s", url)
        except Exception as exc:
            ok, err = False, str(exc)

        self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID,
                                                 "ok": ok, "error": err})
        text = "🖼 Обои успешно обновлены на рабочем столе!" if ok else f"⚠️ Ошибка установки обоев: {err}"
        self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID,
                                         "ok": ok, "text": text})

    def _do_msgbox_spam(self, payload: dict) -> None:
        text = (payload.get("text") or "Привет от XGENT! :)")[:200]
        count = max(1, min(10, int(payload.get("count", 3) or 3)))

        def _spam():
            for _ in range(count):
                ctypes.windll.user32.MessageBoxW(None, text, "XGENT", 0x40)
        threading.Thread(target=_spam, name="xgent-msgbox", daemon=True).start()
        self._publish_response("msgbox_spam", {"type": "msgbox_spam", "device_id": DEVICE_ID,
                                               "ok": True, "count": count})

    def _do_type_text(self, payload: dict) -> None:
        """Напечатать текст на удалённой машине (SendInput Unicode)."""
        text = (payload.get("text") or "")[:500]
        KEYEVENTF_UNICODE = 0x0004
        INPUT_KEYBOARD = 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", UNION)]

        def _send_char(ch: str) -> None:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
            arr = (INPUT * 1)(inp)
            ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

        def _typer():
            for ch in text:
                _send_char(ch)
                time.sleep(0.01)
        threading.Thread(target=_typer, name="xgent-typer", daemon=True).start()
        self._publish_response("type_text", {"type": "type_text", "device_id": DEVICE_ID,
                                             "ok": True, "len": len(text)})


    def _do_open_app(self, payload: dict) -> None:
        """Запустить программу или открыть файл. Не использует `shell=True`."""
        app = (payload.get("app") or "").strip()
        if not app:
            log.warning("Команда open_app без имени")
            return
        log.info("open_app: %r", app)
        # Попробовать сначала через os.startfile (без shell), иначе — напрямую.
        try:
            os.startfile(app)  # type: ignore[attr-defined]
        except (AttributeError, FileNotFoundError, OSError):
            subprocess.Popen(app, shell=False)

    def _do_lock_screen(self, payload: dict) -> None:
        """Заблокировать экран Windows."""
        ok = bool(ctypes.windll.user32.LockWorkStation())
        self._publish_response("lock", {
            "type": "lock", "device_id": DEVICE_ID, "action": "lock",
            "id": payload.get("id") if isinstance(payload.get("id"), str) else None,
            "ok": ok,
            "error": None if ok else "LockWorkStation returned false",
        })
        log.info("LockWorkStation result: %s", ok)

    def _do_volume_toggle(self, payload: dict) -> None:
        """Mute/Unmute системного звука Windows через pycaw."""
        source = "pycaw.IAudioEndpointVolume"
        try:
            volume = self._get_master_volume()
            previous_mute = bool(volume.GetMute())
        except Exception as exc:
            log.warning("Не удалось прочитать состояние звука: %s", exc)
            result = {
                "type": "volume_toggle", "device_id": DEVICE_ID, "ok": False,
                "state": "unknown", "muted": None, "source": source,
                "text": "⚠️ Не удалось прочитать состояние звука; переключение не выполнялось.",
            }
            self._publish_response("volume_toggle", result)
            self._publish_response("output", result)
            return

        setter_error = None
        try:
            volume.SetMute(not previous_mute, None)
        except Exception as exc:
            setter_error = exc
            log.warning("Ошибка команды переключения звука: %s", exc)
        try:
            current_mute = bool(volume.GetMute())
        except Exception as exc:
            current_mute = None
            log.warning("Не удалось проверить новое состояние звука: %s", exc)

        if current_mute is None:
            ok, state = False, "unknown"
            text = "⚠️ Запрос переключения звука отправлен, но система не подтвердила состояние. Результат неизвестен."
        elif current_mute != previous_mute:
            ok, state = True, "confirmed"
            text = "🔇 Звук выключен; состояние подтверждено системой." if current_mute else "🔊 Звук включён; состояние подтверждено системой."
        else:
            ok, state = False, "failed"
            text = "⚠️ Состояние звука не изменилось; переключение не выполнено."
        if setter_error is not None and current_mute == previous_mute:
            log.debug("Mute setter failed and readback stayed unchanged: %s", setter_error)

        result = {
            "type": "volume_toggle",
            "device_id": DEVICE_ID,
            "ok": ok,
            "state": state,
            "muted": current_mute,
            "source": source,
            "text": text,
        }
        self._publish_response("volume_toggle", result)
        self._publish_response("output", result)
        log.info("Volume toggle state=%s", state)

    def _do_power(self, payload: dict) -> None:
        """Выключить, перезагрузить или отправить машину в сон."""
        action = payload.get("power_action") or payload.get("action", "")

        request_id = payload.get("id") or payload.get("request_id")
        request_id = request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None

        def publish(state: str, ok: bool, text: str, *, effect_confirmed: bool) -> None:
            result = {
                "type": "power", "device_id": DEVICE_ID, "action": action,
                "ok": ok, "state": state, "effect_confirmed": effect_confirmed,
                "source": "SetSuspendState" if action == "sleep" else "shutdown.exe",
                "text": text,
            }
            if request_id is not None:
                result["id"] = request_id
                result["request_id"] = request_id
            self._publish_response("power", result)
            self._publish_response("output", result)

        if action == "shutdown":
            log.info("Выключение машины…")
            try:
                subprocess.Popen(["shutdown.exe", "/s", "/f", "/t", "3", "/c", "XGENT: Выключение"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                log.warning("Не удалось запустить shutdown.exe: %s", exc)
                publish("failed", False, "⚠️ Windows не приняла команду выключения.", effect_confirmed=False)
                return
            publish("launch_accepted", True,
                    "⚡ Windows приняла команду выключения с задержкой 3 секунды; итоговое выключение не подтверждено.",
                    effect_confirmed=False)
        elif action == "reboot":
            log.info("Перезагрузка машины…")
            try:
                subprocess.Popen(["shutdown.exe", "/r", "/f", "/t", "3", "/c", "XGENT: Перезагрузка"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                log.warning("Не удалось запустить shutdown.exe: %s", exc)
                publish("failed", False, "⚠️ Windows не приняла команду перезагрузки.", effect_confirmed=False)
                return
            publish("launch_accepted", True,
                    "🔄 Windows приняла команду перезагрузки с задержкой 3 секунды; итоговая перезагрузка не подтверждена.",
                    effect_confirmed=False)
        elif action == "sleep":
            log.info("Устройство уходит в сон…")
            def _sleep():
                time.sleep(1.0)
                try:
                    suspended = bool(ctypes.windll.powrprof.SetSuspendState(0, 1, 0))
                except Exception as exc:
                    log.warning("SetSuspendState ctypes failed: %s, fallback to PowerShell", exc)
                    suspended = False
                if suspended:
                    publish("confirmed", True, "😴 Windows подтвердила переход в спящий режим.", effect_confirmed=True)
                    return
                try:
                    fallback = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                         "Add-Type -AssemblyName System.Windows.Forms; "
                         "$r=[System.Windows.Forms.Application]::SetSuspendState("
                         "[System.Windows.Forms.PowerState]::Suspend, $false, $false); "
                         "[Console]::Out.Write([string]$r)"],
                        capture_output=True, text=True, timeout=15, check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if fallback.returncode == 0 and fallback.stdout.strip().casefold() == "true":
                        publish("confirmed", True, "😴 Windows подтвердила переход в спящий режим.", effect_confirmed=True)
                    elif fallback.returncode == 0 and fallback.stdout.strip().casefold() == "false":
                        publish("failed", False, "⚠️ Windows отказалась перейти в спящий режим.", effect_confirmed=False)
                    else:
                        publish("unknown", False, "⚠️ Запрос сна отправлен, но Windows не подтвердила результат.", effect_confirmed=False)
                except Exception as exc:
                    log.warning("PowerShell sleep fallback failed: %s", exc)
                    publish("unknown", False, "⚠️ Запрос сна отправлен, но результат неизвестен.", effect_confirmed=False)
            try:
                threading.Thread(target=_sleep, name="xgent-power-sleep", daemon=True).start()
            except RuntimeError as exc:
                log.warning("Не удалось запустить sleep worker: %s", exc)
                publish("failed", False, "⚠️ Не удалось запустить обработчик перехода в сон.", effect_confirmed=False)
        else:
            log.warning("Неизвестное действие power: %s", action)
            publish("unsupported", False, "⚠️ Это действие питания не поддерживается.", effect_confirmed=False)

    def _do_stop(self, payload: dict) -> None:
        log.info("Получена команда остановки")
        self.request_stop()

    def _do_prank_screamer(self, payload: dict) -> None:
        def _run():
            try:
                vol = self._get_master_volume()
                vol.SetMasterVolumeLevelScalar(1.0, None)
                vol.SetMute(False, None)
            except Exception:
                pass
            webbrowser.open_new("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            for _ in range(5):
                try:
                    winsound.Beep(2500, 400)
                except Exception:
                    pass
        threading.Thread(target=_run, name="xgent-screamer", daemon=True).start()
        self._publish_response("prank_screamer", {"type": "prank_screamer", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_rickroll(self, payload: dict) -> None:
        try:
            tabs = min(max(int(payload.get("tabs", 10) or 10), 1), 50)
        except (TypeError, ValueError, OverflowError):
            tabs = 10
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        accepted = 0
        for _ in range(tabs):
            try:
                accepted += int(bool(webbrowser.open_new_tab(url)))
            except Exception:
                log.info("Windows browser rejected a prank tab request", exc_info=True)
            if _ + 1 < tabs:
                time.sleep(0.08)
        failed = tabs - accepted
        ok = accepted > 0
        request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
        self._publish_response("prank_rickroll", {
            "type": "prank_rickroll", "device_id": DEVICE_ID, "id": request_id,
            "ok": ok, "state": "launch_accepted" if ok else "failed",
            "tabs_requested": tabs, "tabs_accepted": accepted, "tabs_failed": failed,
            "text": (f"Браузер принял запрос на {accepted}/{tabs} вкладок; это не подтверждает показ страниц."
                     if ok else "Браузер не принял ни одного запроса на открытие вкладки."),
        })

    def _do_prank_matrix(self, payload: dict) -> None:
        cmd = 'start cmd /k "color 0a && title MATRIX && mode con: cols=120 lines=40 && @echo off && :loop && echo %random% %random% %random% %random% %random% %random% %random% %random% %random% && goto loop"'
        subprocess.Popen(cmd, shell=True)
        self._publish_response("prank_matrix", {
            "type": "prank_matrix", "device_id": DEVICE_ID, "id": payload.get("id"),
            "ok": True, "state": "launch_accepted", "effect_confirmed": False,
        })

    def _do_prank_siren(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            deadline = time.time() + duration
            while time.time() < deadline:
                try:
                    winsound.Beep(1200, 200)
                    winsound.Beep(700, 200)
                except Exception:
                    time.sleep(0.4)
        threading.Thread(target=_run, name="xgent-siren", daemon=True).start()
        self._publish_response("prank_siren", {"type": "prank_siren", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_shout_tts(self, payload: dict) -> None:
        text = str(payload.get("text") or "ВНИМАНИЕ! КРИТИЧЕСКАЯ ОШИБКА СИСТЕМЫ!")[:300]
        def _run():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty('volume', 1.0)
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                log.warning("TTS failed: %s", e)
        threading.Thread(target=_run, name="xgent-shout", daemon=True).start()
        self._publish_response("prank_shout_tts", {"type": "prank_shout_tts", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_swap_mouse(self, payload: dict) -> None:
        swap = bool(payload.get("swap", True))
        try:
            ctypes.windll.user32.SwapMouseButton(1 if swap else 0)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_swap_mouse", {"type": "prank_swap_mouse", "device_id": DEVICE_ID, "ok": ok, "swap": swap})

    def _do_prank_crazy_cursor(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            import math
            center_x, center_y = 800, 500
            angle = 0.0
            deadline = time.time() + duration
            while time.time() < deadline:
                x = center_x + int(250 * math.cos(angle))
                y = center_y + int(250 * math.sin(angle))
                ctypes.windll.user32.SetCursorPos(x, y)
                angle += 0.25
                time.sleep(0.02)
        threading.Thread(target=_run, name="xgent-crazycursor", daemon=True).start()
        self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_hide_desktop(self, payload: dict) -> None:
        hide = bool(payload.get("hide", True))
        try:
            progman = ctypes.windll.user32.FindWindowW("Progman", None)
            def_view = ctypes.windll.user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
            listview = ctypes.windll.user32.FindWindowExW(def_view, None, "SysListView32", None)
            if listview:
                cmd = 0 if hide else 5
                ctypes.windll.user32.ShowWindow(listview, cmd)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_hide_desktop", {"type": "prank_hide_desktop", "device_id": DEVICE_ID, "ok": ok, "hide": hide})

    def _do_prank_dancing_windows(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rc = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            orig_x, orig_y = rc.left, rc.top
            deadline = time.time() + duration
            offset = 25
            while time.time() < deadline:
                ctypes.windll.user32.MoveWindow(hwnd, orig_x + offset, orig_y, w, h, True)
                time.sleep(0.05)
                ctypes.windll.user32.MoveWindow(hwnd, orig_x - offset, orig_y, w, h, True)
                time.sleep(0.05)
            ctypes.windll.user32.MoveWindow(hwnd, orig_x, orig_y, w, h, True)
        threading.Thread(target=_run, name="xgent-dancewin", daemon=True).start()
        self._publish_response("prank_dancing_windows", {"type": "prank_dancing_windows", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_black_screen(self, payload: dict) -> None:
        SC_MONITORPOWER = 0xF170
        WM_SYSCOMMAND = 0x0112
        HWND_BROADCAST = 0xFFFF
        try:
            result = ctypes.c_size_t()
            accepted = bool(ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2,
                0x0002, 3000, ctypes.byref(result),
            ))
            self._publish_response("prank_black_screen", {
                "type": "prank_black_screen", "device_id": DEVICE_ID,
                "ok": accepted,
                "state": "launch_accepted" if accepted else "failed",
                "effect_confirmed": False,
                "text": (
                    "🌑 Windows приняла запрос погасить дисплей; визуальный результат не подтверждён."
                    if accepted else "⚠️ Windows не приняла запрос погасить дисплей."
                ),
            })
        except Exception as exc:
            self._publish_response("prank_black_screen", {
                "type": "prank_black_screen", "device_id": DEVICE_ID,
                "ok": False, "state": "failed", "effect_confirmed": False,
                "text": f"⚠️ Не удалось отправить запрос дисплею ({type(exc).__name__}).",
            })

    def _do_prank_random_site(self, payload: dict) -> None:
        SITES = [
            "https://hackertyper.net/",
            "https://pointerpointer.com/",
            "https://theuselessweb.com/",
            "https://cat-bounce.com/",
            "https://zoomquilt.org/",
        ]
        site = random.choice(SITES)
        try:
            accepted = bool(webbrowser.open_new_tab(site))
        except Exception as exc:
            self._publish_response("prank_random_site", {
                "type": "prank_random_site", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "effect_confirmed": False, "url": site,
                "text": f"⚠️ Не удалось передать ссылку браузеру ({type(exc).__name__}).",
            })
            return
        self._publish_response("prank_random_site", {
            "type": "prank_random_site", "device_id": DEVICE_ID, "ok": accepted,
            "state": "launch_accepted" if accepted else "failed",
            "effect_confirmed": False, "url": site,
            "text": (
                "🌐 Браузер принял запрос открыть ссылку; отображение страницы не подтверждено."
                if accepted else "⚠️ Браузер не принял запрос открыть ссылку."
            ),
        })

    def _do_display_brightness(self, payload: dict) -> None:
        """Установить яркость экрана 0-100 через WMI WmiSetBrightness."""
        lvl = _clamp(payload.get("level"), 0, 100, 50)
        ps_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def powershell(script: str):
            return subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8, check=False,
                creationflags=ps_flags,
            )

        def parse_level(value: str) -> int | None:
            lines = str(value or "").strip().splitlines()
            if not lines or not re.fullmatch(r"\s*\d{1,3}\s*", lines[-1]):
                return None
            parsed = int(lines[-1])
            return parsed if 0 <= parsed <= 100 else None

        observed = None
        source = "WMI.WmiMonitorBrightness"
        failure_text = None
        try:
            setter = powershell(
                f"$m=Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
                f"$r=$m.WmiSetBrightness(1, {lvl}); [Console]::Out.Write([string]$r.ReturnValue)"
            )
            raw_code = (setter.stdout or "").strip()
            if setter.returncode == 0 and re.fullmatch(r"\d+", raw_code) and int(raw_code) == 0:
                readback = powershell(
                    "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
                )
                if readback.returncode == 0:
                    observed = parse_level(readback.stdout)
            elif setter.returncode == 0 and re.fullmatch(r"\d+", raw_code):
                failure_text = f"WMI returned {raw_code}"
            else:
                failure_text = "WMI did not return a usable method result"
        except Exception as exc:
            failure_text = type(exc).__name__

        if observed == lvl:
            ok, state = True, "confirmed"
            text = f"🔆 Яркость {observed}%; значение подтверждено системой."
        else:
            fallback_error = None
            try:
                import screen_brightness_control as sbc
                sbc.set_brightness(lvl)
                raw_values = sbc.get_brightness()
                values = raw_values if isinstance(raw_values, list) else [raw_values]
                parsed_values = [parse_level(str(value)) for value in values]
                valid = [value for value in parsed_values if value is not None]
                if valid and len(valid) == len(values) and all(value == lvl for value in valid):
                    observed = valid[0] if len(valid) == 1 else valid
                    source = "screen_brightness_control"
                elif valid:
                    observed = valid[0] if len(valid) == 1 else valid
                    source = "screen_brightness_control"
                else:
                    fallback_error = "brightness readback unavailable"
            except Exception as exc:
                fallback_error = type(exc).__name__

            if observed == lvl or isinstance(observed, list) and observed and all(value == lvl for value in observed):
                ok, state = True, "confirmed"
                text = f"🔆 Яркость {lvl}%; значение подтверждено системой."
            elif observed is not None:
                ok, state = False, "failed"
                text = f"⚠️ Запрошено {lvl}%, но система сообщила другое значение ({observed}); изменение не подтверждено."
            else:
                ok, state = False, "unknown"
                text = "⚠️ Запрос яркости отправлен, но система не вернула проверяемый результат."
            if failure_text:
                log.debug("WMI brightness request was not confirmed: %s", failure_text)
            if fallback_error:
                log.debug("Brightness fallback unavailable: %s", fallback_error)

        result = {
            "type": "display_brightness", "device_id": DEVICE_ID,
            "ok": ok, "state": state, "level": lvl, "current": observed,
            "source": source, "text": text,
        }
        self._publish_response("display_brightness", result)
        self._publish_response("output", result)

    def _do_display_night_light(self, payload: dict) -> None:
        """Вкл/выкл «Ночной свет» через CloudStore-реестр (winreg)."""
        import winreg

        wanted = bool(payload.get("enabled", True))
        key = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NL_STATE_SUBKEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
            data, _ = winreg.QueryValueEx(key, "Data")
            current = _night_light_enabled(data)
            if current != wanted:
                winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, _night_light_toggle_bytes(data))
            readback, _ = winreg.QueryValueEx(key, "Data")
            observed = _night_light_enabled(readback)
            if observed is not wanted:
                ok, state = False, "unknown"
                text = "⚠️ Запрос Night Light записан, но повторное чтение реестра не подтвердило нужное состояние."
            else:
                ok, state = True, "success"
                state_text = "включён" if wanted else "выключен"
                text = (
                    f"🌙 Значение Night Light в CloudStore {state_text}; "
                    "визуальное изменение экрана не проверено."
                )
        except FileNotFoundError:
            ok, state = False, "unsupported"
            text = "⚠️ Ключ Night Light (CloudStore) не найден — функция недоступна на этой системе."
        except Exception as exc:
            ok, state = False, "failed"
            text = f"⚠️ Ошибка проверки/переключения Night Light: {type(exc).__name__}"
        finally:
            if key is not None:
                try:
                    winreg.CloseKey(key)
                except Exception:
                    log.debug("Night Light registry handle close failed", exc_info=True)
        self._publish_response("display_night_light", {
            "type": "display_night_light", "device_id": DEVICE_ID,
            "ok": ok, "state": state, "visual_effect_confirmed": False,
            "enabled": wanted, "observed": wanted if ok else None,
            "source": "HKCU CloudStore Data readback", "text": text,
        })

    def _do_display_rotate(self, payload: dict) -> None:
        """Повернуть экран: angle 0/90/180/270 через ChangeDisplaySettingsEx."""
        angle = payload.get("angle", 0)
        ok, text = _rotate_screen(angle)
        self._publish_response("display_rotate", {
            "type": "display_rotate", "device_id": DEVICE_ID,
            "ok": ok, "angle": angle, "text": text,
        })

    def _do_net_wifi_passwords(self, payload: dict) -> None:
        """Список сохранённых Wi-Fi сетей и паролей (netsh key=clear)."""
        try:
            profiles_out = subprocess.check_output(
                ["netsh", "wlan", "show", "profiles"],
                text=True, stderr=subprocess.DEVNULL, timeout=10, encoding="utf-8", errors="ignore",
            )
            names = _parse_wifi_profile_names(profiles_out)
            if not names:
                text, ok = "📶 Сохранённые Wi-Fi сети не найдены.", True
            else:
                rows, shown = [], names[:15]
                for n in shown:
                    try:
                        out = subprocess.check_output(
                            ["netsh", "wlan", "show", "profile", f"name={n}", "key=clear"],
                            text=True, stderr=subprocess.DEVNULL, timeout=10,
                            encoding="utf-8", errors="ignore",
                        )
                        pwd = _parse_key_content(out)
                        rows.append(f"• {n}: {pwd or '(без ключа)'}")
                    except Exception:
                        rows.append(f"• {n}: (недоступен)")
                text = "📶 Сохранённые Wi-Fi:\n" + "\n".join(rows)
                if len(names) > len(shown):
                    text += f"\n… и ещё {len(names) - len(shown)}"
                ok = True
        except Exception as exc:
            text, ok = f"⚠️ Wi-Fi: {exc}", False
        self._publish_response("net_wifi_passwords", {
            "type": "net_wifi_passwords", "device_id": DEVICE_ID, "ok": ok, "text": text[:3800],
        })

    def _do_net_bluetooth_list(self, payload: dict) -> None:
        """Список активных Bluetooth-устройств (Get-PnpDevice)."""
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Bluetooth -Status OK | Select-Object -First 20 -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            items = [l.strip() for l in out.splitlines() if l.strip()]
            text = "🔵 Bluetooth устройства:\n" + ("\n".join(f"• {i}" for i in items) or "(нет активных)")
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ Bluetooth: {exc}", False
        self._publish_response("net_bluetooth_list", {
            "type": "net_bluetooth_list", "device_id": DEVICE_ID,
            "ok": ok, "items": items[:20], "text": text,
        })

    def _do_storage_smart(self, payload: dict) -> None:
        """Здоровье дисков (SMART) через Get-PhysicalDisk/Get-StorageReliabilityCounter."""
        ps = (
            "$disks = Get-PhysicalDisk; "
            "foreach ($d in $disks) { "
            "  $r = $null; "
            "  try { $r = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch { $r = $null }; "
            "  [PSCustomObject]@{ "
            "    FriendlyName = $d.FriendlyName; MediaType = $d.MediaType; "
            "    Health = $d.HealthStatus; "
            "    Temp = if ($r) { $r.Temperature } else { $null }; "
            "    Wear = if ($r) { $r.Wear } else { $null } "
            "  } "
            "} | ConvertTo-Json -Compress"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, stderr=subprocess.DEVNULL, timeout=20,
            ).strip()
            import json as _json
            data = _json.loads(out) if out else []
            if isinstance(data, dict):
                data = [data]
            if not data:
                text, ok = "🩺 Накопители не найдены.", True
            else:
                lines = []
                for d in data:
                    temp = d.get("Temp")
                    wear = d.get("Wear")
                    lines.append(
                        f"• {d.get('FriendlyName')} [{d.get('MediaType')}] — "
                        f"{d.get('Health')}"
                        + (f", {temp}°C" if temp is not None else "")
                        + (f", износ {wear}%" if wear is not None else "")
                    )
                text, ok = "🩺 SMART и здоровье накопителей:\n" + "\n".join(lines), True
        except Exception as exc:
            text, ok = f"⚠️ SMART: {exc}", False
        self._publish_response("storage_smart", {
            "type": "storage_smart", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_installed_apps(self, payload: dict) -> None:
        """Установленное ПО: Uninstall-ключи реестра 64-bit + 32-bit (WOW6432Node)."""
        ps = (
            "$paths = @( "
            "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
            "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
            "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
            "); $names = @(); "
            "foreach ($p in $paths) { $names += (Get-ItemProperty $p -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DisplayName } | Select-Object -ExpandProperty DisplayName) }; "
            "$names | Sort-Object -Unique"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, stderr=subprocess.DEVNULL, timeout=25,
            )
            items = sorted({l.strip() for l in out.splitlines() if l.strip()})
            total = len(items)
            shown = items[:40]
            text = f"📦 Установленные программы ({total}):\n" + "\n".join(f"• {i}" for i in shown)
            if total > len(shown):
                text += f"\n… и ещё {total - len(shown)}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка получения софта: {exc}", False
        self._publish_response("sys_installed_apps", {
            "type": "sys_installed_apps", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_history_cmd(self, payload: dict) -> None:
        """Последние команды из истории PowerShell (PSReadLine)."""
        lines = _clamp(payload.get("lines"), 1, 200, 30)
        appdata = os.environ.get("APPDATA", "")
        hist_path = os.path.join(appdata, r"Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt")
        try:
            if os.path.exists(hist_path):
                with open(hist_path, "r", encoding="utf-8", errors="ignore") as f:
                    history = [l.strip() for l in f if l.strip()][-lines:]
                text = "🕘 История PowerShell (последние):\n" + "\n".join(f"• {h}" for h in history)
                ok = True
            else:
                text, ok = "История PowerShell не найдена.", True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения истории: {exc}", False
        self._publish_response("sys_history_cmd", {
            "type": "sys_history_cmd", "device_id": DEVICE_ID, "ok": ok, "text": text[:3800],
        })

    # ---------- Режим ожидания (Watchdog) и автозапуск ----------

    def _do_standby_sleep(self, payload: dict) -> None:
        """Перевод агента в спящий режим ожидания (Watchdog)."""
        self._standby = True
        self._publish_status()
        text = "💤 Агент переведён в спящий режим (Standby).\nБольшинство функций приостановлено. Для возобновления используйте кнопку «☀️ Пробудить агента»."
        self._publish_response("output", {
            "type": "standby_sleep", "device_id": DEVICE_ID, "ok": True, "text": text,
        })

    def _do_wake(self, payload: dict) -> None:
        """Пробуждение агента из спящего режима."""
        self._standby = False
        self._publish_status()
        text = "☀️ Агент успешно пробуждён и готов к приёму всех команд!"
        self._publish_response("output", {
            "type": "wake", "device_id": DEVICE_ID, "ok": True, "text": text,
        })

    def _do_autorun_status(self, payload: dict) -> None:
        """Проверка фактического Scheduled Task автозапуска."""
        try:
            state_path = CONFIG_DIR / "guardian.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            startup_enabled = bool(state.get("startup_enabled", True))
            enabled = _scheduled_task_exists() and startup_enabled
            text = (
                f"🚀 Автозапуск Windows: {'✅ ВКЛЮЧЕН' if enabled else '❌ ВЫКЛЮЧЕН'}\n"
                f"Источник: Scheduled Task «{WINDOWS_TASK_NAME}»"
            )
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка проверки автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_status", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_autorun_enable(self, payload: dict) -> None:
        """Enable the pre-registered visible Scheduled Task without source helpers."""
        try:
            if not _scheduled_task_exists():
                raise RuntimeError(
                    f"Задача «{WINDOWS_TASK_NAME}» отсутствует; переустанови подписанный Windows-пакет."
                )
            task = subprocess.run(
                ["schtasks.exe", "/Change", "/TN", WINDOWS_TASK_NAME, "/ENABLE"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if task.returncode != 0:
                raise RuntimeError((task.stderr or task.stdout or "не удалось включить Scheduled Task").strip())
            set_guardian_startup_enabled(True)
            result = subprocess.run(
                ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK_NAME],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "не удалось запустить Scheduled Task").strip())
            text = f"✅ Автозапуск Windows включён; задача «{WINDOWS_TASK_NAME}» запущена."
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка включения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_enable", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_autorun_disable(self, payload: dict) -> None:
        """Отключить Scheduled Task и убрать старый Registry-вариант."""
        startup_state_changed = False
        try:
            set_guardian_startup_enabled(False)
            startup_state_changed = True
            if _scheduled_task_exists():
                disabled = subprocess.run(
                    ["schtasks.exe", "/Change", "/TN", WINDOWS_TASK_NAME, "/DISABLE"],
                    capture_output=True, text=True, check=False,
                )
                if disabled.returncode != 0:
                    raise RuntimeError((disabled.stderr or disabled.stdout or "не удалось отключить Scheduled Task").strip())
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                    for value_name in ("XGENT", "XGentAgent"):
                        try:
                            winreg.DeleteValue(key, value_name)
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                pass
            text = "🛑 Автозапуск Windows отключён. Текущий агент продолжит работать до остановки."
            ok = True
        except Exception as exc:
            if startup_state_changed:
                try:
                    set_guardian_startup_enabled(True)
                except Exception:
                    log.exception("Could not restore Guardian startup state after autorun-disable failure")
            text = f"⚠️ Ошибка отключения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_disable", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    # ---------- Системные утилиты и диагностика ----------

    def _do_sys_uptime(self, payload: dict) -> None:
        """Время непрерывной работы системы (Uptime)."""
        try:
            boot_ts = psutil.boot_time()
            uptime_secs = int(time.time() - boot_ts)
            days, rem = divmod(uptime_secs, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, secs = divmod(rem, 60)
            boot_dt = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(boot_ts))
            text = (
                f"⏱ Время работы системы (Uptime):\n"
                f"• Аптайм: {days} дн. {hours} ч. {minutes} мин. {secs} сек.\n"
                f"• Время загрузки: {boot_dt}"
            )
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка получения аптайма: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "sys_uptime", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_clean_temp(self, payload: dict) -> None:
        """Remove expired regular files from XIDER's private media-temp namespace only."""
        report = media_temp.cleanup_stale_media_temp()
        state = report.get("state")
        deleted = int(report.get("deleted", 0) or 0)
        skipped = int(report.get("skipped", 0) or 0)
        failed = int(report.get("failed", 0) or 0)
        if state == "success":
            text = (
                "🧹 Очистка временных файлов XIDER завершена.\n"
                f"• Удалено просроченных файлов: {deleted}\n"
                f"• Пропущено (активные или неподходящие): {skipped}"
            )
            ok = True
        elif state == "partial":
            text = (
                "⚠️ Очистка временных файлов XIDER завершилась частично.\n"
                f"• Удалено просроченных файлов: {deleted}\n"
                f"• Пропущено: {skipped}\n"
                f"• Не удалось обработать: {failed}"
            )
            ok = False
        else:
            text = "⚠️ Не удалось безопасно проверить временную папку XIDER; файлы не очищены."
            ok = False
        self._publish_response("output", {
            "type": "sys_clean_temp", "device_id": DEVICE_ID, "ok": ok,
            "state": state if state in {"success", "partial"} else "error",
            "deleted": deleted, "skipped": skipped, "failed": failed, "text": text,
        })

    def _do_net_ping(self, payload: dict) -> None:
        """Проверка доступности узла (Ping)."""
        host = payload.get("host", "8.8.8.8")
        host = re.sub(r"[^\w\.-]", "", str(host)) or "8.8.8.8"
        try:
            res = subprocess.run(
                ["ping", "-n", "4", host],
                capture_output=True, text=True, timeout=10,
            )
            output = res.stdout or res.stderr
            text = f"🏓 Результаты Ping к {host}:\n```\n{output.strip()[:3500]}\n```"
            ok = (res.returncode == 0)
        except Exception as exc:
            text = f"⚠️ Ошибка ping: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "net_ping", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_dns_resolution(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dns_resolution",
            "schema": "xider.dns-resolution.v1",
            "feature_id": "D04-F05",
            "device_id": DEVICE_ID,
            "request_id": request_id,
            "state": "supported",
            "source": "system_getaddrinfo",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None,
            "detail": None,
        }
        try:
            result["data"] = dns_diagnostics.resolve_dns(
                payload.get("hostname"), payload.get("expected_addresses"),
            )
        except ValueError:
            result.update(state="invalid_request", detail="invalid_dns_request")
        except TimeoutError:
            result.update(state="unknown", detail="dns_lookup_timeout_or_busy")
        except (LookupError, socket.gaierror):
            result.update(state="unavailable", detail="dns_name_unavailable")
        except OSError:
            result.update(state="unavailable", detail="system_resolver_unavailable")
        self._publish_response("dns_resolution", result)

    def _do_dns_settings(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            snapshot = dns_settings.read_dns_settings()
            resolvers = snapshot.get("resolvers", [])
            state = snapshot.get("state", "supported" if resolvers else "unknown")
            if state not in {"supported", "unknown", "unsupported", "unavailable"}:
                state, resolvers = "unknown", []
            if not isinstance(resolvers, list):
                state, resolvers = "unknown", []
        except Exception:
            snapshot = {"source": dns_settings.SOURCE}
            state, resolvers = "unknown", []
        result = {
            "type": "dns_settings",
            "schema": "xider.dns-settings.v1",
            "feature_id": "D04-F06",
            "device_id": DEVICE_ID,
            "request_id": request_id,
            "state": state,
            "source": snapshot.get("source", dns_settings.SOURCE),
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": {"resolvers": resolvers},
        }
        self._publish_response("dns_settings", result)

    def _do_dns_comparison(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dns_comparison", "schema": "xider.dns-comparison.v1", "feature_id": "D04-F07",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "supported",
            "source": dns_comparison.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": None,
        }
        try:
            result["data"] = dns_comparison.compare_resolvers(payload.get("hostname"), payload.get("resolvers"))
            if result["data"].get("comparison") == "unknown":
                result["state"] = "unknown"
        except ValueError:
            result.update(state="invalid_request", detail="invalid_dns_comparison_request")
        except (OSError, TimeoutError):
            result.update(state="unknown", detail="dns_resolver_unavailable")
        self._publish_response("dns_comparison", result)

    def _do_dnssec_check(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "dnssec_check", "schema": "xider.dnssec-check.v1", "feature_id": "D04-F26",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": dnssec_check.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "hostname": None, "resolver": None, "ad_bit": None, "rcode": None,
        }
        try:
            checked = dnssec_check.check_domain(payload.get("hostname"), payload.get("resolver"))
            result.update(hostname=checked["hostname"], resolver=checked["resolver"],
                          state=checked["assessment"], ad_bit=checked["ad_bit"], rcode=checked["rcode"])
        except ValueError:
            result.update(state="invalid_request")
        except (OSError, TimeoutError):
            result.update(state="unknown")
        self._publish_response("dnssec_check", result)

    def _do_route_trace(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": route_trace.WINDOWS_SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": None,
        }
        try:
            traced = route_trace.trace_route(payload.get("target"), platform="windows")
            result["data"] = {key: traced[key] for key in ("target", "reached", "hops") if key in traced}
            result["source"] = traced.get("source", route_trace.WINDOWS_SOURCE)
            result["state"] = traced.get("state", "unknown")
            result["detail"] = traced.get("detail")
        except ValueError:
            result.update(state="invalid_request", detail="invalid_route_trace_target")
        except Exception:
            result.update(state="unknown", detail="route_trace_failed")
        self._publish_response("route_trace", result)

    def _do_path_mtu(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {"type": "path_mtu", "schema": "xider.path-mtu.v1", "feature_id": "D04-F10",
                  "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
                  "source": path_mtu.WINDOWS_SOURCE,
                  "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                  "data": None, "detail": None}
        try:
            snapshot = path_mtu.diagnose_path_mtu(payload.get("target"), platform="windows")
            result["source"], result["state"] = snapshot.get("source", path_mtu.WINDOWS_SOURCE), snapshot.get("state", "unknown")
            if result["state"] == "supported":
                result["data"] = {key: snapshot[key] for key in ("target", "mtu", "max_tested") if key in snapshot}
            else:
                result["data"] = None
                result["detail"] = snapshot.get("detail") or (
                    "ping_unavailable" if result["state"] == "unsupported" else "probe_no_confirmed_reply"
                )
        except ValueError:
            result.update(state="invalid_request", detail="invalid_path_mtu_target")
        except Exception:
            result.update(state="unknown", detail="path_mtu_probe_failed")
        self._publish_response("path_mtu", result)

    def _do_captive_portal(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "captive_portal", "schema": "xider.captive-portal.v1", "feature_id": "D04-F11",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": captive_portal.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": "probe_failed",
        }
        try:
            snapshot = captive_portal.detect_captive_portal()
            result["state"] = snapshot.get("state", "unknown")
            result["source"] = snapshot.get("source", captive_portal.SOURCE)
            origin = snapshot.get("portal_origin")
            result["data"] = {"portal_origin": origin} if origin is not None else None
            result["detail"] = snapshot.get("detail")
        except Exception:
            pass
        self._publish_response("captive_portal", result)

    def _do_ipv6_connectivity(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        result = {
            "type": "ipv6_connectivity", "schema": "xider.ipv6-connectivity.v1", "feature_id": "D04-F12",
            "device_id": DEVICE_ID, "request_id": request_id, "state": "unknown",
            "source": ipv6_connectivity.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": None, "detail": "probe_failed",
        }
        try:
            snapshot = ipv6_connectivity.probe_ipv6()
            state = snapshot.get("state")
            result["state"] = state if state in {"reachable", "no_route", "unknown", "unsupported"} else "unknown"
            result["source"] = snapshot.get("source", ipv6_connectivity.SOURCE)
            result["detail"] = snapshot.get("detail")
        except Exception:
            pass
        self._publish_response("ipv6_connectivity", result)

    def _do_tls_certificate(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        parsed = tls_certificate.parse_target(
            f"[{payload.get('host')}]:{payload.get('port')}" if isinstance(payload.get("host"), str) and ":" in payload["host"]
            else f"{payload.get('host')}:{payload.get('port')}"
        )
        host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
        result = {
            "type": "tls_certificate", "schema": "xider.tls-certificate.v1", "feature_id": "D04-F13",
            "device_id": DEVICE_ID, "request_id": request_id, "host": host, "port": port,
            "source": tls_certificate.SOURCE, "state": "invalid_request" if not parsed else "unknown",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": "invalid_endpoint" if not parsed else "tls_probe_failed", "data": None,
        }
        if parsed:
            try:
                snapshot = tls_certificate.probe_certificate(host, port)
                if snapshot.get("state") in {"valid", "expired", "not_yet_valid", "chain_invalid", "unknown"}:
                    result.update(state=snapshot["state"], detail=snapshot.get("detail"), data=snapshot.get("data"))
            except Exception:
                pass
        self._publish_response("tls_certificate", result)

    def _do_service_probe(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        raw_host = payload.get("host")
        parsed = tls_certificate.parse_target(
            f"[{raw_host}]:{payload.get('port')}" if isinstance(raw_host, str) and ":" in raw_host
            else f"{raw_host}:{payload.get('port')}"
        )
        protocol = payload.get("protocol")
        valid = parsed is not None and protocol in {"tcp", "tls"}
        host, port = (parsed["host"], parsed["port"]) if parsed else (None, None)
        result = {
            "type": "service_probe", "schema": "xider.service-probe.v1", "feature_id": "D04-F14",
            "device_id": DEVICE_ID, "request_id": request_id, "host": host, "port": port,
            "protocol": protocol if protocol in {"tcp", "tls"} else None,
            "source": service_probe.SOURCE, "state": "unknown" if valid else "invalid_request",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": "probe_failed" if valid else "invalid_endpoint_or_protocol",
        }
        if valid:
            try:
                snapshot = service_probe.probe_service(host, port, protocol)
                if snapshot.get("state") in {"reachable", "reachable_untrusted", "unreachable", "unknown"}:
                    result.update(state=snapshot["state"], detail=snapshot.get("detail"))
            except Exception:
                pass
        self._publish_response("service_probe", result)

    def _do_latency_series(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        host, port = payload.get("host"), payload.get("port")
        valid = latency_series._valid_endpoint(host, port)
        result = {
            "type": "latency_series", "schema": "xider.latency-series.v1", "feature_id": "D04-F01",
            "device_id": DEVICE_ID, "request_id": request_id,
            "host": host if valid else None, "port": port if valid else None,
            "source": latency_series.SOURCE, "state": "unknown" if valid else "invalid_request",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "samples": [], "summary": None,
            "detail": "probe_failed" if valid else "invalid_endpoint",
        }
        if valid:
            try:
                snapshot = latency_series.measure_latency_series(host, port)
                if snapshot.get("source") == latency_series.SOURCE and snapshot.get("state") in {"available", "partial", "unknown"}:
                    result.update(state=snapshot["state"], samples=snapshot["samples"],
                                  summary=snapshot["summary"], detail=snapshot["detail"])
            except Exception:
                pass
        self._publish_response("latency_series", result)

    def _do_packet_loss_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        address = payload.get("address")
        valid = packet_loss_history._literal_address(address)
        snapshot = None
        if valid:
            try:
                snapshot = packet_loss_history.measure_packet_loss(address=valid)
            except Exception:
                pass
        result = {
            "type": "packet_loss_history", "schema": "xider.packet-loss-history.v1", "feature_id": "D04-F02",
            "device_id": DEVICE_ID, "request_id": request_id,
            "address": valid, "source": packet_loss_history.SOURCE,
            "state": snapshot.get("state") if snapshot and snapshot.get("state") in {"complete", "partial", "unknown"} else "invalid_request" if not valid else "unknown",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": snapshot if snapshot and snapshot.get("source") == packet_loss_history.SOURCE else {
                "address": valid, "state": "unknown", "source": packet_loss_history.SOURCE,
                "detail": "probe_failed" if valid else "invalid_literal_address", "sent": 10,
                "observed": 0, "received": 0, "lost": 0, "loss_percent": None, "samples": [],
            },
        }
        self._publish_response("packet_loss_history", result)

    def _do_jitter_measurement(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        valid = packet_loss_history._literal_address(payload.get("address"))
        snapshot = None
        if valid:
            try:
                snapshot = jitter_measurement.measure_jitter(address=valid)
            except Exception:
                pass
        state = snapshot.get("state") if snapshot else "unknown"
        if state not in {"available", "partial", "unknown"}:
            state = "invalid_request" if not valid else "unknown"
        data = snapshot if snapshot and snapshot.get("source") == jitter_measurement.SOURCE else {
            "address": valid, "state": "unknown", "source": jitter_measurement.SOURCE,
            "detail": "probe_failed" if valid else "invalid_literal_address",
            "attempted": 10, "received": 0, "lost": 0, "unknown": 10,
            "pairs": 0, "jitter_ms": None, "samples": [],
        }
        self._publish_response("network_jitter", {
            "type": "network_jitter", "schema": "xider.network-jitter.v1", "feature_id": "D04-F03",
            "device_id": DEVICE_ID, "request_id": request_id, "address": valid,
            "source": jitter_measurement.SOURCE, "state": state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": data,
        })

    def _do_network_throughput(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        endpoint = network_throughput.parse_endpoint(payload.get("endpoint"))
        result = network_throughput.measure_throughput(endpoint["url"]) if endpoint else {
            "state": "invalid_request", "source": network_throughput.SOURCE,
            "detail": "invalid_test_endpoint",
        }
        state = result.get("state") if result.get("state") in {"available", "unknown", "limit_exceeded", "invalid_request"} else "unknown"
        self._publish_response("network_throughput", {
            "type": "network_throughput", "schema": "xider.network-throughput.v1", "feature_id": "D04-F04",
            "device_id": DEVICE_ID, "request_id": request_id,
            "endpoint": endpoint["url"] if endpoint else None,
            "source": network_throughput.SOURCE, "state": state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": result,
        })

    def _do_network_link_speed(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        interface_id = payload.get("interface_id")
        catalog = network_link_speed.collect_catalog()
        if interface_id is None:
            data = {"state": catalog["state"], "interfaces": catalog["interfaces"], "detail": catalog["detail"]}
            mode = "catalog"
        else:
            data = network_link_speed.collect_link_speed(interface_id)
            mode = "measure"
        state = data.get("state") if data.get("state") in {"available", "unsupported", "unknown"} else "unknown"
        self._publish_response("network_link_speed", {
            "type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
            "device_id": DEVICE_ID, "request_id": request_id, "mode": mode,
            "source": network_link_speed.SOURCE, "state": state,
            "interface_id": interface_id if isinstance(interface_id, str) else None,
            "data": data,
        })

    def _do_route_lookup(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        destination = payload.get("destination")
        try:
            normalized = route_lookup.normalize_destination(destination)
            data = route_lookup.lookup_route(normalized)
        except (ValueError, TypeError):
            normalized = ""
            data = {"state": "invalid_request"}
        self._publish_response("route_lookup", {
            "type": "route_lookup", "schema": "xider.route-lookup.v1", "feature_id": "D04-F22",
            "request_id": request_id, "device_id": DEVICE_ID,
            "source": route_lookup.WINDOWS_SOURCE, "state": data.get("state", "unknown"),
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"), "data": data,
        })

    def _do_endpoint_batch(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        ids = ("example", "cloudflare", "github")
        try:
            sample = endpoint_batch.check_public_set()
        except Exception:
            sample = {"preset": endpoint_batch.PRESET_ID, "state": "unknown", "results": []}
        state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
        rows = sample.get("results")
        results = []
        if (sample.get("preset") != endpoint_batch.PRESET_ID or not isinstance(rows, list)
                or len(rows) != len(ids)):
            state, rows = "unknown", []
        for index, target_id in enumerate(ids):
            row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
            item_state, detail = row.get("state"), row.get("detail")
            if row.get("target_id") != target_id or item_state not in {"reachable", "unreachable", "unknown"}:
                item_state, detail = "unknown", "probe_failed"
                state = "unknown"
            if detail not in {None, "probe_timeout", "connection_refused", "probe_failed"}:
                item_state, detail, state = "unknown", "probe_failed", "unknown"
            results.append({"target_id": target_id, "state": item_state, "detail": detail})
        result = {
            "type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
            "device_id": DEVICE_ID, "request_id": request_id, "source": endpoint_batch.WINDOWS_SOURCE,
            "preset": endpoint_batch.PRESET_ID, "state": state, "results": results,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._publish_response("endpoint_batch", result)

    def _do_wifi_quality_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_quality.collect_wifi_quality()
        except Exception:
            sample = {"state": "unknown", "metric": None, "value": None,
                      "source": wifi_quality.WINDOWS_SOURCE, "detail": "wifi_query_failed"}
        metric, value = sample.get("metric"), sample.get("value")
        if sample.get("state") != "available":
            metric = value = None
        result = {
            "type": "wifi_quality_history", "schema": "xider.wifi-quality.v1", "feature_id": "D04-F15",
            "device_id": DEVICE_ID, "request_id": request_id, "source": wifi_quality.WINDOWS_SOURCE,
            "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
            "metric": metric, "value": value,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else "wifi_query_failed",
        }
        self._publish_response("wifi_quality_history", result)

    def _do_wifi_channel_summary(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_channel.collect_wifi_channel()
        except Exception:
            sample = {"state": "unknown", "channel": None, "width_mhz": None,
                      "width_state": "unknown", "source": wifi_channel.WINDOWS_SOURCE,
                      "detail": "wifi_query_failed"}
        channel = sample.get("channel") if sample.get("state") == "available" else None
        width = sample.get("width_mhz") if channel is not None else None
        width_state = sample.get("width_state") if width is None else "available"
        if width_state not in {"available", "unsupported", "unknown"}:
            width_state = "unknown"
        result = {
            "type": "wifi_channel_summary", "schema": "xider.wifi-channel.v1", "feature_id": "D04-F16",
            "device_id": DEVICE_ID, "request_id": request_id, "source": wifi_channel.WINDOWS_SOURCE,
            "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
            "channel": channel, "width_mhz": width, "width_state": width_state,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if isinstance(sample.get("detail"), str) else None,
        }
        self._publish_response("wifi_channel_summary", result)

    def _do_wifi_connection_history(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = wifi_connection_history.collect_wifi_connection_history()
        except Exception:
            sample = {"state": "unknown", "events": [], "detail": "event_log_unavailable"}
        state = sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown"
        events = []
        if state == "available" and isinstance(sample.get("events"), list):
            for event in sample["events"][-20:]:
                if (isinstance(event, dict) and isinstance(event.get("at"), str)
                        and event.get("kind") in {"connected", "disconnected"}):
                    events.append({"at": event["at"], "kind": event["kind"]})
        if state == "available" and not events:
            state = "unknown"
        detail = sample.get("detail")
        allowed_details = {"no_connection_events", "event_log_unavailable", "invalid_event_log"}
        result = {
            "type": "wifi_connection_history", "schema": "xider.wifi-connection-history.v1",
            "feature_id": "D04-F17", "device_id": DEVICE_ID, "request_id": request_id,
            "source": wifi_connection_history.WINDOWS_SOURCE, "state": state, "events": events,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": detail if detail in allowed_details else ("invalid_event_log" if state == "unknown" else None),
        }
        self._publish_response("wifi_connection_history", result)

    def _do_proxy_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = proxy_status.collect_proxy_status()
        except Exception:
            sample = {"state": "unknown", "sources": []}
        state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
        sources = sample.get("sources") if state == "available" else []
        allowed = {"current_user_manual", "current_user_pac", "current_user_auto_detect", "winhttp", "environment"}
        if not isinstance(sources, list) or any(item not in allowed for item in sources) or len(set(sources)) != len(sources):
            state, sources = "unknown", []
        result = {
            "type": "proxy_status", "schema": "xider.proxy-status.v1", "feature_id": "D04-F23",
            "device_id": DEVICE_ID, "request_id": request_id, "source": proxy_status.WINDOWS_SOURCE,
            "state": state, "sources": sources,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if sample.get("detail") in {"registry_unavailable", "winhttp_query_failed"} else None,
        }
        self._publish_response("proxy_status", result)

    def _do_vpn_route_status(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            sample = vpn_route.collect_vpn_summary()
        except Exception:
            sample = {"state": "unknown", "up_tunnel_candidates": 0,
                      "down_tunnel_candidates": 0, "route_assessment": "not_verified"}
        state = sample.get("state") if sample.get("state") in {"available", "unknown"} else "unknown"
        up = sample.get("up_tunnel_candidates", 0)
        down = sample.get("down_tunnel_candidates", 0)
        if (not isinstance(up, int) or isinstance(up, bool) or not 0 <= up <= 128
                or not isinstance(down, int) or isinstance(down, bool) or not 0 <= down <= 128
                or sample.get("route_assessment") != "not_verified"):
            state, up, down = "unknown", 0, 0
        if state == "unknown":
            up, down = 0, 0
        result = {
            "type": "vpn_route_status", "schema": "xider.vpn-route.v1", "feature_id": "D04-F24",
            "device_id": DEVICE_ID, "request_id": request_id, "source": vpn_route.WINDOWS_SOURCE,
            "state": state, "up_tunnel_candidates": up, "down_tunnel_candidates": down,
            "route_assessment": "not_verified",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "detail": sample.get("detail") if sample.get("detail") in {"psutil_unavailable", "interface_query_failed"} else None,
        }
        self._publish_response("vpn_route_status", result)

    def _do_network_traffic(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        interface_id = payload.get("interface_id")
        if interface_id is None:
            sample = network_traffic.collect_catalog()
            result = {
                "type": "network_traffic", "schema": "xider.network-traffic.v1",
                "feature_id": "D04-F18", "device_id": DEVICE_ID, "request_id": request_id,
                "source": network_traffic.SOURCE, "mode": "catalog",
                "state": sample.get("state") if sample.get("state") in {"available", "unsupported", "unknown"} else "unknown",
                "interfaces": sample.get("interfaces", []) if isinstance(sample.get("interfaces"), list) else [],
                "detail": sample.get("detail") if sample.get("detail") in {"counter_source_unavailable", "no_interfaces"} else None,
            }
        else:
            sample = network_traffic.collect_interface_traffic(interface_id)
            result = {
                "type": "network_traffic", "schema": "xider.network-traffic.v1",
                "feature_id": "D04-F18", "device_id": DEVICE_ID, "request_id": request_id,
                "source": network_traffic.SOURCE, "mode": "sample",
                "state": sample.get("state") if sample.get("state") in {"available", "unknown", "unsupported"} else "unknown",
                "interface_id": sample.get("interface_id"), "label": sample.get("label"),
                "sent_bytes": sample.get("sent_bytes"), "received_bytes": sample.get("received_bytes"),
                "sampled_at": sample.get("sampled_at"),
                "detail": sample.get("detail") if sample.get("detail") in {"counter_unavailable", "counter_source_unavailable", "interface_not_found"} else None,
            }
        result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        self._publish_response("network_traffic", result)

    def _do_gateway_probe(self, payload: dict) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = None
        try:
            snapshot = gateway_probe.probe_gateway()
        except Exception:
            snapshot = {"state": "unknown", "gateway": None,
                        "source": gateway_probe.SOURCE, "detail": "gateway_probe_failed"}
        state = snapshot.get("state")
        if state not in {"reachable", "unknown", "no_gateway", "unsupported"}:
            state = "unknown"
        gateway = snapshot.get("gateway")
        try:
            gateway = str(ipaddress.IPv4Address(gateway)) if gateway is not None else None
        except (ipaddress.AddressValueError, TypeError):
            gateway, state = None, "unknown"
        result = {
            "type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
            "device_id": DEVICE_ID, "request_id": request_id, "state": state,
            "source": gateway_probe.SOURCE,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": {"gateway": gateway} if gateway else None,
            "detail": snapshot.get("detail") if isinstance(snapshot.get("detail"), str) else None,
        }
        self._publish_response("gateway_probe", result)

    # ---------- Расширенная матрица приколов (40 приколов) ----------

    def _do_prank_bsod(self, payload: dict) -> None:
        """Полноэкранный фейковый BSOD с анимацией и скрытием курсора мыши."""
        def _bsod():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.title("System Fatal Error")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.config(cursor="none")
                root.configure(bg="#0078D7")

                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<Double-Button-1>", lambda e: root.destroy())

                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()

                active = True
                def _mouse_lock():
                    user32 = ctypes.windll.user32
                    while active:
                        user32.SetCursorPos(sw // 2, sh // 2)
                        time.sleep(0.08)
                threading.Thread(target=_mouse_lock, daemon=True).start()

                frame = tk.Frame(root, bg="#0078D7")
                frame.place(relx=0.08, rely=0.15, relwidth=0.85, relheight=0.7)

                lbl_sad = tk.Label(frame, text=":(", font=("Segoe UI", 90), fg="white", bg="#0078D7")
                lbl_sad.pack(anchor="w", pady=(0, 20))

                msg_text = (
                    "На вашем ПК возникла проблема, и его необходимо перезагрузить.\n"
                    "Мы лишь собираем некоторые сведения об ошибке, а затем будет\n"
                    "выполнена автоматическая перезагрузка.\n"
                )
                lbl_msg = tk.Label(frame, text=msg_text, font=("Segoe UI Light", 22), fg="white", bg="#0078D7", justify="left")
                lbl_msg.pack(anchor="w", pady=(0, 25))

                lbl_pct = tk.Label(frame, text="0% завершено", font=("Segoe UI Light", 24), fg="white", bg="#0078D7")
                lbl_pct.pack(anchor="w", pady=(0, 40))

                bottom_frame = tk.Frame(frame, bg="#0078D7")
                bottom_frame.pack(anchor="w", fill="x")

                qr_canvas = tk.Canvas(bottom_frame, width=95, height=95, bg="white", highlightthickness=0)
                qr_canvas.pack(side="left", padx=(0, 20))

                stop_text = (
                    "Для получения дополнительных сведений об этой проблеме посетите\n"
                    "https://www.windows.com/stopcode\n\n"
                    "Код остановки: CRITICAL_PROCESS_DIED\n"
                    "Что вызвало проблему: win32kbase.sys"
                )
                lbl_stop = tk.Label(bottom_frame, text=stop_text, font=("Segoe UI", 12), fg="white", bg="#0078D7", justify="left")
                lbl_stop.pack(side="left", anchor="w")

                pct = 0
                def _update_pct():
                    nonlocal pct
                    if pct < 100:
                        pct += 15
                        if pct > 100:
                            pct = 100
                        lbl_pct.config(text=f"{pct}% завершено")
                        root.after(1400, _update_pct)
                    else:
                        root.after(2000, _close)

                def _close():
                    nonlocal active
                    active = False
                    try:
                        root.destroy()
                    except Exception:
                        pass

                root.after(1200, _update_pct)
                root.after(18000, _close)
                root.mainloop()
                active = False
            except Exception as e:
                log.error("BSOD error: %s", e)

        threading.Thread(target=_bsod, daemon=True, name="xgent-bsod").start()
        self._publish_response("output", {"type": "prank_bsod", "device_id": DEVICE_ID, "ok": True, "text": "💀 Полноэкранный BSOD (со скрытием мыши и блокировкой экрана) активирован!"})

    def _do_prank_fake_update(self, payload: dict) -> None:
        """Полноэкранная локальная демо-анимация с видимым выходом и таймером."""
        cancel_event = threading.Event()
        root_ref: dict[str, object] = {}

        def request_close() -> None:
            cancel_event.set()
            root = root_ref.get("root")
            if root is not None:
                try:
                    root.after(0, root.destroy)
                except Exception:
                    pass

        scene_id = f"fake_update:{id(cancel_event)}"
        self._register_prank_cancel(scene_id, request_close)

        def _update_screen():
            try:
                import tkinter as tk
                if cancel_event.is_set():
                    return
                root = tk.Tk()
                root_ref["root"] = root
                root.title("XIDER demo")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.configure(bg="#000000")
                closed = False
                started = time.monotonic()

                def _close(_event=None):
                    nonlocal closed
                    if closed:
                        return
                    closed = True
                    cancel_event.set()
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass

                root.bind("<Escape>", _close)
                root.bind("<Double-Button-1>", _close)
                root.protocol("WM_DELETE_WINDOW", _close)

                frame = tk.Frame(root, bg="#000000")
                frame.place(relx=0.5, rely=0.5, anchor="center")

                spinner = tk.Canvas(frame, width=80, height=80, bg="#000000", highlightthickness=0)
                spinner.pack(pady=20)

                lbl_title = tk.Label(frame, text="Работа с обновлениями", font=("Segoe UI", 26), fg="white", bg="#000000")
                lbl_title.pack(pady=(10, 5))

                lbl_demo = tk.Label(
                    frame, text="ДЕМО · Esc закрывает · 22с",
                    font=("Segoe UI", 16, "bold"), fg="#70d6ff", bg="#000000",
                )
                lbl_demo.pack(pady=5)

                lbl_sub = tk.Label(frame, text="Это локальная сценка: обновление и перезагрузка системы не запускаются.\nЭкран закроется автоматически; выйти можно клавишей Esc.", font=("Segoe UI Light", 14), fg="#cccccc", bg="#000000", justify="center")
                lbl_sub.pack(pady=(15, 0))

                angle = 0
                def _spin():
                    nonlocal angle
                    if closed:
                        return
                    spinner.delete("all")
                    import math
                    cx, cy, r = 40, 40, 25
                    for i in range(6):
                        a = math.radians(angle + i * 35)
                        x = cx + r * math.cos(a)
                        y = cy + r * math.sin(a)
                        dot_r = 3 + i * 0.8
                        spinner.create_oval(x - dot_r, y - dot_r, x + dot_r, y + dot_r, fill="white", outline="")
                    angle = (angle + 18) % 360
                    root.after(40, _spin)

                def _countdown():
                    if closed:
                        return
                    remaining = max(0, 22 - int(time.monotonic() - started))
                    lbl_demo.config(text=f"ДЕМО · Esc закрывает · {remaining}с")
                    if remaining == 0:
                        _close()
                    else:
                        root.after(250, _countdown)

                _spin()
                _countdown()
                root.mainloop()
            except Exception as e:
                log.error("Fake update error: %s", e)
            finally:
                self._finish_prank_cancel(scene_id)

        threading.Thread(target=_update_screen, daemon=True, name="xgent-fakeupdate").start()
        self._publish_response("output", {"type": "prank_fake_update", "device_id": DEVICE_ID, "ok": True, "text": "⏳ Демо-экран запущен на 22 секунды. Закрыть сразу: Esc или двойной щелчок."})

    def _do_prank_toast_spam(self, payload: dict) -> None:
        """Спам 5 системными уведомлениями в трее."""
        try:
            ps = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
                "$notify = New-Object System.Windows.Forms.NotifyIcon; "
                "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
                "$notify.Visible = $True; "
                "1..5 | ForEach-Object { "
                "  $notify.ShowBalloonTip(3000, 'Внимание системы #'+$_, 'Зафиксировано вторжение космических котов!', [System.Windows.Forms.ToolTipIcon]::Warning); "
                "  Start-Sleep -Milliseconds 700 "
                "}; $notify.Dispose()"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = "🔔 Серия из 5 фейковых системных уведомлений запущена в трее!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка уведомлений: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_toast_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_keyboard_disco(self, payload: dict) -> None:
        """Мигание индикаторами клавиатуры (CapsLock, NumLock, ScrollLock)."""
        def _disco():
            user32 = ctypes.windll.user32
            keys = [0x14, 0x90, 0x91]
            for _ in range(12):
                for k in keys:
                    user32.keybd_event(k, 0x45, 1, 0)
                    user32.keybd_event(k, 0x45, 1 | 2, 0)
                    time.sleep(0.12)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_keyboard_disco", "device_id": DEVICE_ID, "ok": True, "text": "💃 Дискотека клавиатуры (индикаторы Caps/Num/Scroll) активирована!"})

    def _do_prank_beep_morse(self, payload: dict) -> None:
        """Звуковой сигнал азбукой Морзе (SOS) через динамики."""
        def _morse():
            pattern = [(1000, 150)] * 3 + [(1000, 400)] * 3 + [(1000, 150)] * 3
            for freq, dur in pattern:
                try:
                    winsound.Beep(freq, dur)
                    time.sleep(0.08)
                except Exception:
                    pass
        threading.Thread(target=_morse, daemon=True).start()
        self._publish_response("output", {"type": "prank_beep_morse", "device_id": DEVICE_ID, "ok": True, "text": "📻 Сигнал Морзе (S.O.S) воспроизведён в динамиках!"})

    def _do_prank_open_notepad_type(self, payload: dict) -> None:
        """Открытие блокнота с эффектом живого набора текста."""
        msg = payload.get("text") or "Привет... Я слежу за тобой через розетку 👁️"
        clean_msg = msg.replace("'", " ").replace('"', " ")
        ps = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"Start-Process notepad.exe; "
            f"Start-Sleep -Milliseconds 800; "
            f"$ws.AppActivate('Notepad'); "
            f"Start-Sleep -Milliseconds 400; "
            f"'{clean_msg}'.ToCharArray() | ForEach-Object {{ "
            f"  $ws.SendKeys($_); Start-Sleep -Milliseconds 120 "
            f"}}"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._publish_response("output", {"type": "prank_open_notepad_type", "device_id": DEVICE_ID, "ok": True, "text": f"📝 Блокнот открыт, текст '{clean_msg[:40]}...' печатается!"})

    def _do_prank_hacker_typer(self, payload: dict) -> None:
        """Открытие интерактивного Hacker Typer."""
        try:
            webbrowser.open("https://hackertyper.net")
            text = "🧑‍💻 Hacker Typer открыт в браузере!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_hacker_typer", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_sound_spooky(self, payload: dict) -> None:
        """Жуткие потусторонние звуки."""
        def _play():
            try:
                for f in (300, 315, 290, 420, 260, 240, 500, 200):
                    winsound.Beep(f, 350)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_spooky", "device_id": DEVICE_ID, "ok": True, "text": "👻 Потусторонние жуткие звуки воспроизведены!"})

    def _do_prank_sound_fart(self, payload: dict) -> None:
        """Смешной звуковой эффект."""
        def _play():
            try:
                for f in (180, 160, 140, 120, 110, 95, 80):
                    winsound.Beep(f, 80)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_fart", "device_id": DEVICE_ID, "ok": True, "text": "💨 Смешной звуковой эффект воспроизведён!"})

    def _do_prank_minimize_all(self, payload: dict) -> None:
        """Сворачивание всех открытых окон."""
        try:
            ps = "(New-Object -ComObject Shell.Application).MinimizeAll()"
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = "📉 Все открытые окна свернуты на рабочий стол!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_minimize_all", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_open_calc_spam(self, payload: dict) -> None:
        """Каскад из 5 калькуляторов."""
        try:
            for _ in range(5):
                subprocess.Popen("calc.exe", shell=True)
                time.sleep(0.2)
            text = "🔢 5 калькуляторов открыты каскадом!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_calc_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_invert_screen(self, payload: dict) -> None:
        """Переворот экрана на 180° на 7 секунд с автовозвратом."""
        original_angle = _read_current_display_angle()
        if original_angle is None:
            self._publish_response("output", {
                "type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": False,
                "state": "unknown", "text": "⚠️ Не удалось прочитать исходный поворот экрана; сценка не запускалась.",
            })
            return
        cancel_event = threading.Event()
        scene_id = f"invert_screen:{time.time_ns()}"

        def restore() -> None:
            cancel_event.set()
            _rotate_screen(original_angle)

        self._register_prank_cancel(scene_id, restore)
        rotated, detail = _rotate_screen(180)
        if not rotated:
            self._finish_prank_cancel(scene_id)
            self._publish_response("output", {
                "type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "text": f"⚠️ Экран не повёрнут: {detail}",
            })
            return
        if cancel_event.is_set():
            restore()

        def _flip():
            try:
                if not cancel_event.wait(7):
                    restore()
            finally:
                self._finish_prank_cancel(scene_id)

        threading.Thread(target=_flip, daemon=True, name="xider-prank-invert").start()
        self._publish_response("output", {"type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": True, "text": "🙃 Экран перевернут вверх дном на 7 секунд!"})

    def _do_prank_slow_mouse(self, payload: dict) -> None:
        """Замедление скорости указателя мыши на 8 секунд с восстановлением."""
        try:
            user32 = ctypes.windll.user32
            cur_speed = ctypes.c_uint()
            if not user32.SystemParametersInfoW(0x0070, 0, ctypes.byref(cur_speed), 0):
                raise RuntimeError("не удалось прочитать скорость указателя")
            original_speed = int(cur_speed.value)
            if not 1 <= original_speed <= 20:
                raise RuntimeError("Windows вернула скорость указателя вне диапазона")
        except Exception as exc:
            self._publish_response("output", {
                "type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": False,
                "state": "unknown", "text": f"⚠️ Сценка не запущена: {type(exc).__name__}.",
            })
            return

        cancel_event = threading.Event()
        applied = threading.Event()
        restore_lock = threading.Lock()
        restored = False
        scene_id = f"slow_mouse:{time.time_ns()}"

        def restore() -> None:
            nonlocal restored
            cancel_event.set()
            if not applied.is_set():
                return
            with restore_lock:
                if restored:
                    return
                restored = True
                user32.SystemParametersInfoW(0x0071, 0, original_speed, 0)

        self._register_prank_cancel(scene_id, restore)
        try:
            if not user32.SystemParametersInfoW(0x0071, 0, 1, 0):
                raise RuntimeError("Windows не применила временную скорость указателя")
            applied.set()
            if cancel_event.is_set():
                restore()
        except Exception as exc:
            self._finish_prank_cancel(scene_id)
            self._publish_response("output", {
                "type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "text": f"⚠️ Сценка не запущена: {type(exc).__name__}.",
            })
            return

        def _slow():
            try:
                if not cancel_event.wait(8):
                    restore()
            finally:
                self._finish_prank_cancel(scene_id)

        threading.Thread(target=_slow, daemon=True, name="xider-prank-slow-mouse").start()
        self._publish_response("output", {"type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": True, "text": "🐌 Мышь замедлена до минимальной скорости на 8 секунд!"})

    def _do_prank_random_clicks(self, payload: dict) -> None:
        """Request random cursor movement for five seconds; does not synthesize clicks."""
        def _jiggle():
            user32 = ctypes.windll.user32
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            for _ in range(25):
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetCursorPos(pt.x + random.randint(-15, 15), pt.y + random.randint(-15, 15))
                time.sleep(0.2)
        try:
            worker = threading.Thread(target=_jiggle, daemon=True, name="xider-prank-cursor-jiggle")
            worker.start()
        except Exception as exc:
            self._publish_response("output", {
                "type": "prank_random_clicks", "device_id": DEVICE_ID, "ok": False,
                "state": "failed", "effect_confirmed": False,
                "text": f"⚠️ Не удалось запустить движение курсора: {type(exc).__name__}",
            })
            return
        self._publish_response("output", {
            "type": "prank_random_clicks", "device_id": DEVICE_ID, "ok": True,
            "state": "launch_accepted", "effect_confirmed": False,
            "text": "🤹 Запуск движения курсора принят на 5 секунд; клики не выполняются, перемещение не подтверждено.",
        })

    def _do_prank_paste_clipboard_spam(self, payload: dict) -> None:
        """Подмена буфера обмена на забавную фразу."""
        try:
            import pyperclip
            meme = "( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%"
            pyperclip.copy(meme)
            text = f"📋 В буфер обмена подложен мем:\n'{meme}'"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_paste_clipboard_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_type_reversed(self, payload: dict) -> None:
        """Инвертирование содержимого буфера обмена задом наперёд."""
        try:
            import pyperclip
            cur = pyperclip.paste() or "Привет мир"
            rev = cur[::-1]
            pyperclip.copy(rev)
            text = f"🔄 Буфер обмена инвертирован задом наперёд:\n{rev[:100]}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_type_reversed", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_volume_jump(self, payload: dict) -> None:
        """Скачки громкости (100% <-> 10%) с автовосстановлением."""
        def _jump():
            try:
                volume = self._get_master_volume()
                orig = volume.GetMasterVolumeLevelScalar()
                for _ in range(4):
                    volume.SetMasterVolumeLevelScalar(1.0, None)
                    time.sleep(0.3)
                    volume.SetMasterVolumeLevelScalar(0.1, None)
                    time.sleep(0.3)
                volume.SetMasterVolumeLevelScalar(orig, None)
            except Exception:
                pass
        threading.Thread(target=_jump, daemon=True).start()
        self._publish_response("output", {"type": "prank_volume_jump", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Скачки громкости (100% <-> 10%) запущены!"})

    def _do_prank_say_whisper(self, payload: dict) -> None:
        """Медленный шепчущий голос через TTS."""
        phrase = payload.get("text") or "Обернись... Ты здесь совсем не один..."
        def _speak():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 90)
                engine.setProperty("volume", 0.7)
                engine.say(phrase)
                engine.runAndWait()
            except Exception:
                pass
        threading.Thread(target=_speak, daemon=True).start()
        self._publish_response("output", {"type": "prank_say_whisper", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Шёпот воспроизведён: '{phrase}'"})

    def _do_prank_fake_virus(self, payload: dict) -> None:
        """Серия шуточных предупреждений об опасном вирусе."""
        def _alerts():
            user32 = ctypes.windll.user32
            user32.MessageBoxW(None, "Обнаружен критический троян: Win32.Pivko.Trojan!\nВсе запасы пива под угрозой.", "Windows Defender Security Alert", 0x10)
            time.sleep(0.4)
            user32.MessageBoxW(None, "Попытка нейтрализации...\nОшибка: Троян слишком весёлый.", "Windows Defender", 0x30)
            time.sleep(0.4)
            user32.MessageBoxW(None, "Шутка! Ваша система в полной безопасности. 😉", "XGENT Prank", 0x40)
        threading.Thread(target=_alerts, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_virus", "device_id": DEVICE_ID, "ok": True, "text": "🦠 Шуточные предупреждения о вирусе показаны пользователю!"})

    def _do_prank_open_cd(self, payload: dict) -> None:
        """Открытие лотка CD-ROM дисковода или звуковой сигнал."""
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW("set cdaudio door open", None, 0, None)
            text = "💿 Команда открытия CD-ROM отправлена!"
            ok = True
        except Exception as exc:
            winsound.Beep(800, 400)
            text = f"💿 CD-ROM физически не обнаружен (подан звуковой сигнал): {exc}"
            ok = True
        self._publish_response("output", {"type": "prank_open_cd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_change_wallpaper(self, payload: dict) -> None:
        """Установка шуточных обоев с сохранением резервной копии."""
        try:
            key_path = r"Control Panel\Desktop"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                cur_wall, _ = winreg.QueryValueEx(key, "WallPaper")
                with open(CONFIG_DIR / "backup_wallpaper.txt", "w", encoding="utf-8") as bf:
                    bf.write(cur_wall)
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1920, 1080), color=(15, 20, 35))
            draw = ImageDraw.Draw(img)
            draw.text((650, 520), "HACKED BY XGENT :)\n(Все ваши печеньки принадлежат нам)", fill=(0, 255, 120))
            wall_path = CONFIG_DIR / "prank_wall.bmp"
            img.save(wall_path, "BMP")
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(wall_path), 3)
            text = "🖼️ Мемные обои установлены (исходные сохранены для восстановления)!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка установки обоев: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_restore_wallpaper(self, payload: dict) -> None:
        """Восстановление исходных обоев рабочего стола."""
        ok, text = self._restore_saved_prank_wallpaper()
        self._publish_response("output", {"type": "prank_restore_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _restore_saved_prank_wallpaper(self) -> tuple[bool, str]:
        """Restore the saved wallpaper without publishing a second command result."""
        try:
            bf_path = CONFIG_DIR / "backup_wallpaper.txt"
            if bf_path.exists():
                with open(bf_path, "r", encoding="utf-8") as bf:
                    orig_wall = bf.read().strip()
                if orig_wall and os.path.exists(orig_wall):
                    ctypes.windll.user32.SystemParametersInfoW(20, 0, orig_wall, 3)
                    return True, "✅ Исходные обои восстановлены."
                else:
                    return False, "ℹ️ Исходный файл обоев не найден на диске."
            else:
                return False, "ℹ️ Резервная копия обоев не найдена."
        except Exception as exc:
            log.exception("Не удалось восстановить сохранённые обои")
            return False, f"⚠️ Ошибка восстановления обоев: {type(exc).__name__}."

    def _do_prank_speak_time(self, payload: dict) -> None:
        """Озвучивание точного времени говорящим синтезатором речи."""
        now = time.strftime("%H часов %M минут")
        phrase = f"Внимание! Точное время на вашей планете: {now}. Не забудьте сделать разминку!"
        def _speak():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 140)
                engine.say(phrase)
                engine.runAndWait()
            except Exception:
                pass
        threading.Thread(target=_speak, daemon=True).start()
        self._publish_response("output", {"type": "prank_speak_time", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Время озвучено: '{phrase}'"})

    def _do_prank_rickroll_terminal(self, payload: dict) -> None:
        """Запуск терминала с ASCII анимацией / рикроллом."""
        try:
            subprocess.Popen(["start", "cmd", "/k", "curl -s parrot.live || curl -s ascii.live/rick"], shell=True)
            text = "🕺 Терминал с ASCII анимацией запущен на экране!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка запуска терминала: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_rickroll_terminal", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_screen_off_brief(self, payload: dict) -> None:
        """Кратковременное погасание экрана на 3 секунды."""
        def _dark():
            user32 = ctypes.windll.user32
            user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
            time.sleep(3)
            user32.mouse_event(0x0001, 1, 1, 0, 0)
        threading.Thread(target=_dark, daemon=True).start()
        self._publish_response("output", {"type": "prank_screen_off_brief", "device_id": DEVICE_ID, "ok": True, "text": "🌑 Экран временно погашен на 3 секунды!"})

    def _do_prank_alert_loop(self, payload: dict) -> None:
        """Цепочка из 3 забавных диалоговых окон."""
        def _loop():
            user32 = ctypes.windll.user32
            user32.MessageBoxW(None, "Хотите ли вы удалить Интернет для освобождения памяти?", "Система Windows", 0x24)
            time.sleep(0.3)
            user32.MessageBoxW(None, "Вы уверены? Это действие невозможно отменить!", "Система Windows", 0x34)
            time.sleep(0.3)
            user32.MessageBoxW(None, "Отмена операции: Интернет слишком тяжелый.", "Система Windows", 0x40)
        threading.Thread(target=_loop, daemon=True).start()
        self._publish_response("output", {"type": "prank_alert_loop", "device_id": DEVICE_ID, "ok": True, "text": "❓ Шуточная цепочка диалоговых окон показана пользователю!"})

    def _do_prank_open_browser_memes(self, payload: dict) -> None:
        """Открытие 3 вкладок с легендарными мемами в браузере."""
        try:
            urls = [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://cat-bounce.com/",
                "https://heeeeeeeey.com/",
            ]
            for u in urls:
                webbrowser.open_new_tab(u)
                time.sleep(0.3)
            text = "🌐 3 вкладки с легендарными мемами открыты в браузере!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка браузера: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_browser_memes", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_glitch_cursor(self, payload: dict) -> None:
        """Курсор рисует 'восьмёрку' в течение 5 секунд."""
        def _glitch():
            user32 = ctypes.windll.user32
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y
            for i in range(50):
                angle = i * 0.25
                x = int(cx + 100 * math.sin(angle))
                y = int(cy + 60 * math.sin(angle * 2))
                user32.SetCursorPos(x, y)
                time.sleep(0.1)
        threading.Thread(target=_glitch, daemon=True).start()
        self._publish_response("output", {"type": "prank_glitch_cursor", "device_id": DEVICE_ID, "ok": True, "text": "🌀 Глючный курсор (траектория 'восьмёрка') кружится 5 секунд!"})

    def _do_prank_shake_window(self, payload: dict) -> None:
        """Тряска активного окна."""
        def _shake():
            user32 = ctypes.windll.user32
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x, y = rect.left, rect.top
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            for i in range(24):
                dx = 20 if (i % 2 == 0) else -20
                user32.MoveWindow(hwnd, x + dx, y, w, h, True)
                time.sleep(0.06)
            user32.MoveWindow(hwnd, x, y, w, h, True)
        threading.Thread(target=_shake, daemon=True).start()
        self._publish_response("output", {"type": "prank_shake_window", "device_id": DEVICE_ID, "ok": True, "text": "🫨 Активное окно трясётся из стороны в сторону!"})

    def _do_prank_meme_wallpaper(self, payload: dict) -> None:
        """Скачать и установить случайный смешной мем в качестве обоев."""
        payload["random_meme"] = True
        self._do_wallpaper_set(payload)

    def _do_prank_caps_disco(self, payload: dict) -> None:
        """Дискотека индикаторами CapsLock/NumLock на клавиатуре."""
        def _disco():
            user32 = ctypes.windll.user32
            for _ in range(16):
                user32.keybd_event(0x14, 0, 0, 0)
                user32.keybd_event(0x14, 0, 2, 0)
                user32.keybd_event(0x90, 0, 0, 0)
                user32.keybd_event(0x90, 0, 2, 0)
                time.sleep(0.18)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_caps_disco", "device_id": DEVICE_ID, "ok": True, "text": "✨ Дискотека светодиодов клавиатуры запущена!"})

    def _do_prank_cursor_circle(self, payload: dict) -> None:
        """Круговые гипнотические движения курсора мыши."""
        def _circle():
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            cx, cy = sw // 2, sh // 2
            r = min(sw, sh) // 3
            import math
            for step in range(80):
                angle = step * 0.15
                x = int(cx + r * math.cos(angle))
                y = int(cy + r * math.sin(angle))
                user32.SetCursorPos(x, y)
                time.sleep(0.05)
        threading.Thread(target=_circle, daemon=True).start()
        self._publish_response("output", {"type": "prank_cursor_circle", "device_id": DEVICE_ID, "ok": True, "text": "🌀 Курсор мыши закручен в гипнотический круг на 5 сек!"})

    def _do_prank_fake_error_spam(self, payload: dict) -> None:
        """Каскадный спам звуками критической ошибки Windows."""
        def _beep_spam():
            for _ in range(12):
                try:
                    import winsound
                    winsound.MessageBeep(0x00000010)
                except Exception:
                    pass
                time.sleep(0.18)
        threading.Thread(target=_beep_spam, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_error_spam", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Спам звуками критической ошибки Windows запущен!"})

    def _do_prank_fake_delete_sys32(self, payload: dict) -> None:
        """Имитация удаления System32 в терминале."""
        try:
            cmd = (
                'start "System Integrity Service" cmd /k "'
                'color 0A & echo [CRITICAL] Deleting Windows system files... & timeout /t 1 >nul & '
                'dir /s C:\\Windows\\System32 & cls & color 0C & '
                'echo [FATAL ERROR] 14,291 system files permanently purged! & '
                'echo System will halt in 5 seconds... & timeout /t 4 >nul & '
                'color 0A & echo ============================================= & '
                'echo    JUST A PRANK BY XGENT! EVERYTHING IS SAFE :) & '
                'echo ============================================= & '
                'timeout /t 6 >nul & exit"'
            )
            subprocess.Popen(cmd, shell=True)
            ok, text = True, "💻 Фейковое удаление System32 запущено в терминале!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_fake_delete_sys32", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_ghost_typer(self, payload: dict) -> None:
        """Печать призрачных фраз в активное окно."""
        def _ghost():
            time.sleep(1.0)
            phrases = [
                "   👻 Привет... Я живу в твоем кулере...   ",
                "   👀 Не оборачивайся... Сзади кто-то стоит...   ",
                "   👽 Твой ПК захвачен внеземным разумом...   ",
            ]
            text = random.choice(phrases)
            try:
                user32 = ctypes.windll.user32
                for ch in text:
                    code = ord(ch)
                    user32.keybd_event(0, code, 0x0004, 0)
                    user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
                    time.sleep(0.06)
            except Exception as e:
                log.error("Ghost typer error: %s", e)
        threading.Thread(target=_ghost, daemon=True).start()
        self._publish_response("output", {"type": "prank_ghost_typer", "device_id": DEVICE_ID, "ok": True, "text": "👻 Призрачный набор текста активирован в активном окне!"})

    def _do_prank_fbi_lock(self, payload: dict) -> None:
        """Окно предупреждения от спецслужб."""
        def _fbi():
            msg = (
                "ВНИМАНИЕ! Ваш IP-адрес зафиксирован Федеральной Службой Безопасности.\n\n"
                "Зафиксирована подозрительная активность.\n"
                "Веб-камера и микрофон переведены в режим протоколирования доказательств.\n"
                "Оставайтесь на месте до прибытия оперативной группы."
            )
            ctypes.windll.user32.MessageBoxW(None, msg, "⚠️ ФЕДЕРАЛЬНОЕ ПРЕДУПРЕЖДЕНИЕ", 0x10 | 0x0)
        threading.Thread(target=_fbi, daemon=True).start()
        self._publish_response("output", {"type": "prank_fbi_lock", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Предупреждение от спецслужб выведено на экран!"})

    def _do_prank_cat_invaders(self, payload: dict) -> None:
        """Открытие вкладок с котиками в браузере."""
        try:
            urls = [
                "https://cataas.com/cat/says/HELLO%20HUMAN",
                "https://cataas.com/cat/gif",
                "https://cataas.com/cat",
            ]
            for u in urls:
                webbrowser.open(u)
                time.sleep(0.3)
            ok, text = True, "🐱 Нашествие котиков открыто в браузере!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_cat_invaders", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_fake_ransom_cats(self, payload: dict) -> None:
        """Полноэкранный шуточный вымогатель с котиками."""
        def _cat_screen():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.title("RansomCat v1.0")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.configure(bg="#1A002C")
                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<Double-Button-1>", lambda e: root.destroy())

                frame = tk.Frame(root, bg="#1A002C")
                frame.place(relx=0.5, rely=0.5, anchor="center")

                tk.Label(frame, text="🐱 ВНИМАНИЕ! ВСЕ ВАШИ ФАЙЛЫ ЗАХВАЧЕНЫ КОТАМИ! 🐾", font=("Segoe UI", 26, "bold"), fg="#FF55FF", bg="#1A002C").pack(pady=15)
                msg = (
                    "Ваши файлы не зашифрованы, но котики очень голодны!\n"
                    "Для разблокировки положите 3 пакетика влажного корма перед монитором.\n\n"
                    "Осталось времени до того как кот скинет кружку со стола: 00:05:00\n"
                    "(Для закрытия нажмите клавишу ESC)"
                )
                tk.Label(frame, text=msg, font=("Segoe UI", 16), fg="white", bg="#1A002C", justify="center").pack(pady=20)
                root.after(18000, lambda: root.destroy())
                root.mainloop()
            except Exception as e:
                log.error("Cat ransom error: %s", e)
        threading.Thread(target=_cat_screen, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_ransom_cats", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Кошачий экран-вымогатель выведен на весь экран!"})

    def _do_prank_nyan_stream(self, payload: dict) -> None:
        """Запуск Nyan Cat с радугой."""
        try:
            webbrowser.open("https://www.youtube.com/watch?v=QH2-TGUlwu4")
            ok, text = True, "🌈 Nyan Cat с радугой и музыкой запущен в браузере!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_nyan_stream", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_random_beeps(self, payload: dict) -> None:
        """Случайные внезапные звуковые сигналы материнской платы."""
        def _beeps():
            for _ in range(8):
                try:
                    import winsound
                    freq = random.randint(500, 2200)
                    dur = random.randint(80, 250)
                    winsound.Beep(freq, dur)
                except Exception:
                    pass
                time.sleep(random.uniform(0.1, 0.4))
        threading.Thread(target=_beeps, daemon=True).start()
        self._publish_response("output", {"type": "prank_random_beeps", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Серия случайных звуковых сигналов ПК запущена!"})

    def _do_prank_confetti_winner(self, payload: dict) -> None:
        """Праздничный экран «Вы выиграли миллион долларов»."""
        try:
            html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>WINNER!</title>
<style>body{background:#FFD700;color:#8B0000;font-family:sans-serif;text-align:center;padding-top:100px;overflow:hidden;}
h1{font-size:55px;animation:blink 0.5s infinite alternate;}@keyframes blink{from{opacity:1;}to{opacity:0.3;}}
p{font-size:28px;font-weight:bold;}
</style></head><body><h1>🎉 ПОЗДРАВЛЯЕМ! ВЫ ВЫИГРАЛИ 1,000,000 $ ! 🎉</h1>
<p>Вы стали миллионным посетителем этого компьютера!<br>Деньги уже переводятся на ваш стол!</p>
<script>
let a=new AudioContext();
function b(){let o=a.createOscillator();o.connect(a.destination);o.frequency.value=800;o.start();setTimeout(()=>o.stop(),200);}
setInterval(b,600);
setTimeout(()=>window.close(),15000);
</script></body></html>"""
            tf = tempfile.NamedTemporaryFile(
                "w", prefix=_PRANK_TEMP_PREFIX, suffix=".html", delete=False, encoding="utf-8"
            )
            tf.write(html)
            tf.close()
            _schedule_prank_temp_cleanup(tf.name)
            subprocess.Popen(f'explorer "{tf.name}"', shell=True)
            ok, text = True, "🎰 Экран с выигрышем $1,000,000 открыт!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_confetti_winner", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_low_battery_fake(self, payload: dict) -> None:
        """Ложное системное оповещение о критическом заряде батареи 0%."""
        def _bat():
            msg = "Уровень заряда батареи: 0%!\nКомпьютер будет выключен через 15 секунд для предотвращения потери данных."
            ctypes.windll.user32.MessageBoxW(None, msg, "Критический разряд аккумулятора", 0x30 | 0x0)
        threading.Thread(target=_bat, daemon=True).start()
        self._publish_response("output", {"type": "prank_low_battery_fake", "device_id": DEVICE_ID, "ok": True, "text": "🪫 Оповещение о 0% батареи показано на экране!"})

    def _do_prank_earthquake(self, payload: dict) -> None:
        """Землетрясение активного окна (вибрация 8.0 баллов)."""
        def _quake():
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return
            w = r.right - r.left
            h = r.bottom - r.top
            orig_x, orig_y = r.left, r.top
            for _ in range(40):
                dx = random.randint(-25, 25)
                dy = random.randint(-25, 25)
                user32.MoveWindow(hwnd, orig_x + dx, orig_y + dy, w, h, True)
                time.sleep(0.04)
            user32.MoveWindow(hwnd, orig_x, orig_y, w, h, True)
        threading.Thread(target=_quake, daemon=True).start()
        self._publish_response("output", {"type": "prank_earthquake", "device_id": DEVICE_ID, "ok": True, "text": "🌋 Землетрясение активного окна запущено на 3 секунды!"})

    def _do_prank_laugh_track(self, payload: dict) -> None:
        """Зловещий закадровый смех через динамики."""
        def _laugh():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 140)
                engine.setProperty("volume", 1.0)
                engine.say("Ха ха ха ха! Мва ха ха ха ха! Я контролирую этот компьютер!")
                engine.runAndWait()
            except Exception as e:
                log.error("Laugh track error: %s", e)
        threading.Thread(target=_laugh, daemon=True).start()
        self._publish_response("output", {"type": "prank_laugh_track", "device_id": DEVICE_ID, "ok": True, "text": "😈 Зловещий смех запущен через динамики!"})

    def _register_prank_cancel(self, scene_id: str, callback) -> None:
        if not isinstance(scene_id, str) or not scene_id or not callable(callback):
            raise ValueError("prank cancellation registration is invalid")
        with self._active_prank_cancels_lock:
            self._active_prank_cancels[scene_id] = callback

    def _finish_prank_cancel(self, scene_id: str) -> None:
        with self._active_prank_cancels_lock:
            self._active_prank_cancels.pop(scene_id, None)

    def _cancel_active_pranks(self) -> list[str]:
        with self._active_prank_cancels_lock:
            entries = list(self._active_prank_cancels.items())
            self._active_prank_cancels.clear()
        cancelled = []
        for scene_id, callback in entries:
            try:
                callback()
                cancelled.append(scene_id)
            except Exception:
                log.exception("Не удалось отменить прикол %s", scene_id)
        return cancelled

    def _do_prank_stop_all(self, payload: dict) -> None:
        """Restore only prank state for which XIDER has an explicit saved original value."""
        log.info("Экстренная остановка всех приколов (prank_stop_all)...")
        cancel_requests = self._cancel_active_pranks()
        # Wallpaper is the only prank here with an explicit persisted original.
        # Never guess user defaults for mouse, display orientation, icons, or night light.
        _wallpaper_restored, wallpaper = self._restore_saved_prank_wallpaper()
        self._publish_response("output", {
            "type": "prank_stop_all",
            "device_id": DEVICE_ID,
            "ok": False,
            "state": "partial",
            "text": (
                f"🛑 Запрос отмены отправлен для сценок: {len(cancel_requests)}. {wallpaper} "
                "Результат частичный: не все старые эффекты поддерживают отмену. "
                "Остальные параметры системы не сбрасывал: "
                "короткие эффекты завершаются по своим таймерам, "
                "а для полной отмены каждой сцены нужен отдельный cancel-handler."
            ),
        })






    def _do_agent_update(self, payload: dict) -> None:
        """Install a signed source release transactionally; frozen bundles fail closed."""
        if payload.get("update") and _BUNDLED_RUNTIME:
            self._publish_response("output", {
                "type": "agent_update",
                "device_id": DEVICE_ID,
                "ok": False,
                "state": "unsupported_package",
                "operation_id": _frozen_update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                "update_state": "failed",
                "text": (
                    "⚠️ Эта сборка Windows EXE пока не поддерживает транзакционное "
                    "обновление. Ничего не менял; файлы пакета не тронуты."
                ),
            })
            return

        if payload.get("update"):
            operation_id = update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None))
            if not isinstance(payload.get("manifest_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", payload["manifest_digest"]):
                self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID,
                    "ok": False, "state": "manifest_pin_required", "update_state": "failed",
                    "operation_id": operation_id, "text": "Обновление не начато: требуется точный manifest digest."})
                return
            self._publish_update_progress(operation_id, "queued")
            script_dir = Path(__file__).resolve().parent
            files = (
                "xgent_wds.py",
                "xider_guardian_wds.py",
                "config.py",
                "crypto.py",
                "xgencrypto.py",
                "release_signature.py",
                "update_package.py",
                "command_module_loader.py", "command_signing.py", "command_replay.py", "shell_execution.py", "location_adapter.py", "camera_devices.py", "camera_series.py", "microphone_devices.py", "microphone_level.py", "audio_output_control.py", "media_temp.py", "media_history.py", "media_request_control.py", "display_contact_sheet.py", "window_capture.py", "dns_diagnostics.py", "dns_settings.py", "dns_comparison.py", "dnssec_check.py", "gateway_probe.py", "route_trace.py", "path_mtu.py", "captive_portal.py", "ipv6_connectivity.py", "tls_certificate.py", "service_probe.py", "latency_series.py", "packet_loss_history.py", "jitter_measurement.py", "network_throughput.py", "network_link_speed.py", "route_lookup.py", "endpoint_batch.py", "wifi_quality.py", "wifi_channel.py", "wifi_connection_history.py", "proxy_status.py", "vpn_route.py", "network_traffic.py", "network_traffic_schedule.py", "interface_status_history.py",
            )
            backup_dir = CONFIG_DIR / "agent-backups" / (
                f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
            )
            installed = False
            try:
                with tempfile.TemporaryDirectory(prefix="xgent-wds-update-") as tmp:
                    archive = Path(tmp) / "XIDER-source.zip"
                    manifest = Path(tmp) / "release-manifest.json"
                    self._publish_update_progress(operation_id, "downloading")
                    release_tag = download_verified_source_archive(
                        manifest,
                        archive,
                        release_tag=payload.get("release_tag"),
                        expected_manifest_digest=payload.get("manifest_digest"),
                    )
                    self._publish_update_progress(operation_id, "verifying")
                    source = extract_agent_files(
                        archive,
                        Path(tmp) / "agent-stage",
                        files,
                        component_dir="XGENT-WDS",
                    )
                    for name in files:
                        candidate = source / name
                        compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")
                    self._publish_update_progress(operation_id, "installing")
                    backup_dir = install_agent_files(
                        source,
                        script_dir,
                        backup_dir,
                        files,
                        transaction_path=CONFIG_DIR / UPDATE_STATE_NAME,
                        operation_id=operation_id, release_tag=release_tag,
                        manifest_digest=payload.get("manifest_digest"),
                    )
                    installed = True

                self._publish_response("output", {
                    "type": "agent_update",
                    "device_id": DEVICE_ID,
                    "ok": True,
                    "state": "restarting",
                    "release_tag": release_tag,
                    "health_check": "pending",
                    "operation_id": operation_id, "update_state": "reconnecting",
                    "text": (
                        f"📦 Подписанный релиз {release_tag} проверен; исходники Windows-агента "
                        f"установлены. Копия прежних файлов: {backup_dir.name}. Перезапускаюсь; "
                        "подтверждение MQTT-здоровья ещё ожидается."
                    ),
                })

                def _reboot_agent() -> None:
                    time.sleep(1.5)
                    try:
                        os.execv(
                            sys.executable,
                            [sys.executable, str(script_dir / "xgent_wds.py"), *sys.argv[1:]],
                        )
                    except Exception as restart_error:
                        log.exception("Не удалось перезапустить Windows-агент после обновления")
                        rollback_state = "rolled_back"
                        rollback_detail = ""
                        self._publish_update_progress(operation_id, "rolling_back")
                        try:
                            rollback_unhealthy_agent(
                                CONFIG_DIR / UPDATE_STATE_NAME,
                                script_dir,
                            )
                        except Exception as rollback_error:
                            rollback_state = "failed"
                            rollback_detail = f" Откат неполон: {rollback_error}"
                            log.exception("Не удалось вернуть прежние файлы после ошибки перезапуска")
                        self._publish_response("output", {
                            "type": "agent_update",
                            "device_id": DEVICE_ID,
                            "ok": False,
                            "state": rollback_state, "update_state": rollback_state,
                            "operation_id": operation_id,
                            **({"id": payload["id"]} if payload.get("id") else {}),
                            "text": f"❌ Перезапуск не удался: {restart_error}.{rollback_detail}",
                        })

                threading.Thread(target=_reboot_agent, name="xgent-wds-update-restart", daemon=True).start()
            except Exception as exc:
                log.exception("Windows agent self-update failed")
                outcome = "failed"
                rollback_detail = ""
                if installed:
                    self._publish_update_progress(operation_id, "rolling_back")
                    try:
                        rollback_unhealthy_agent(CONFIG_DIR / UPDATE_STATE_NAME, script_dir)
                        outcome = "rolled_back"
                    except Exception as rollback_error:
                        rollback_detail = f" Автоматический откат неполон: {rollback_error}"
                receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
                if receipt and receipt.get("operation_id") == operation_id and receipt.get("update_state") in {"rolled_back", "failed"}:
                    outcome = receipt["update_state"]
                self._publish_response("output", {
                    "type": "agent_update",
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "state": "failed",
                    "operation_id": operation_id, "update_state": outcome,
                    "text": (
                        f"❌ Обновление не подтверждено: {exc}.{rollback_detail} "
                        "Перед новой попыткой проверь журнал агента и резервную копию."
                    ),
                })
            return

        restart = payload.get("restart", False)
        pid = os.getpid()
        exe_path = sys.executable
        text = (
            f"🔄 <b>Агент XGENT v{VERSION}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Платформа: Windows ({platform.system()} {platform.release()})\n"
            f"• Процесс: <code>{html.escape(exe_path)}</code>\n"
            f"• PID процесса: <code>{pid}</code>\n"
            f"• Набор команд: <b>{len(SUPPORTED_COMMANDS)} функций</b> (полная синхронизация)\n"
            f"• Статус: 🟢 В сети, готов к приёму команд"
        )
        if restart:
            text += "\n\n⚠️ <i>Инициирован перезапуск процесса агента...</i>"
            def _reboot_agent():
                time.sleep(1.5)
                subprocess.Popen([sys.executable] + sys.argv)
                os._exit(0)
            threading.Thread(target=_reboot_agent, daemon=True).start()
        self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _publish_update_progress(self, operation_id: str, state: str) -> None:
        self._publish_response("update_progress", {
            "type": "agent_update_progress", "device_id": DEVICE_ID,
            "operation_id": operation_id, "update_state": state,
        })

    def _do_uninstall_agent(self, payload: dict) -> None:
        """Полное удаление агента с ПК: автозапуск, конфиги, логи и завершение процесса."""
        log.warning("Получена команда полного удаления агента с ПК!")
        try:
            set_guardian_desired_running(False)
        except Exception as exc:
            log.exception("Could not persist Guardian stop intent before uninstall")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление отменено: не удалось сохранить состояние Guardian: {exc}",
            })
            return

        # Unregister the worker task without ending its current instance: this
        # callback must stay alive long enough to publish the correlated ACK.
        worker_removed, worker_detail = _remove_scheduled_task(
            WINDOWS_TASK_NAME, stop_running=False,
        )
        if not worker_removed:
            try:
                set_guardian_desired_running(True)
            except Exception:
                log.exception("Could not restore Guardian intent after task removal failure")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление отменено: задача агента не удалена ({worker_detail}). Настройки сохранены.",
            })
            return

        # The Guardian is a different process, so stop it before unregistering
        # its task. If that cannot be confirmed, preserve the worker state and
        # report failure instead of pretending the uninstall completed.
        guardian_removed, guardian_detail = _remove_scheduled_task(
            WINDOWS_GUARDIAN_TASK_NAME, stop_running=True,
        )
        if not guardian_removed:
            try:
                set_guardian_desired_running(True)
            except Exception:
                log.exception("Could not restore Guardian intent after Guardian task removal failure")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление не завершено: Guardian не остановлен ({guardian_detail}). Состояние агента сохранено.",
            })
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "XGentAgent")
            winreg.CloseKey(key)
        except Exception:
            pass

        try:
            startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            for shortcut in startup_dir.glob("*XGENT*"):
                shortcut.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            import shutil
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        except Exception:
            pass

        text = (
            "🛑 <b>Агент XGENT полностью удалён с компьютера!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Автозагрузка Windows очищена\n"
            "• Локальные настройки и логи удалены\n"
            "• Процесс агента завершает работу."
        )
        self._publish_response("output", {"type": "uninstall_agent", "device_id": DEVICE_ID, "ok": True, "text": text})

        def _bye():
            time.sleep(1.5)
            os._exit(0)
        threading.Thread(target=_bye, daemon=True).start()


def _detect_features() -> dict:
    """Безопасное обнаружение возможностей без доступа к оборудованию."""
    features = {
        "notify": True,
        "lock_screen": True,
        "power_control": True,
        "screenshot": False,
        "tts": False,
        "audio_control": False,
        "camera_api": False,
        "battery": False,
        "clipboard": False,
        "mic": False,
        "shell": True,
        "open_app": True,
    }
    try:
        from PIL import ImageGrab  # noqa: F401
        features["screenshot"] = True
    except Exception:
        pass
    try:
        import pyttsx3  # noqa: F401
        features["tts"] = True
    except Exception:
        pass
    try:
        import pycaw  # noqa: F401
        import comtypes  # noqa: F401
        features["audio_control"] = True
    except Exception:
        pass
    try:
        import cv2  # noqa: F401
        features["camera_api"] = True
    except Exception:
        pass
    try:
        features["battery"] = psutil.sensors_battery() is not None
    except Exception:
        pass
    try:
        import pyperclip  # noqa: F401
        features["clipboard"] = True
    except Exception:
        pass
    try:
        import sounddevice  # noqa: F401
        import soundfile  # noqa: F401
        features["mic"] = True
    except Exception:
        pass
    return features


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _detect_feature_status() -> dict[str, str]:
    """Report detected prerequisites; hardware and privacy settings stay unverified."""
    features = _detect_features()
    return {
        "shell": "supported",
        "open_app": "supported",
        "external_ip_watch": "supported",
        "geolocation": "approximate",
        "screenshot": "device_unverified" if features["screenshot"] else "dependency_missing",
        "webcam": "permission_unverified" if _module_available("cv2") else "dependency_missing",
        "microphone": (
            "permission_unverified"
            if _module_available("sounddevice") and _module_available("soundfile")
            else "dependency_missing"
        ),
        "clipboard": "device_unverified" if features["clipboard"] else "dependency_missing",
        "battery": "supported" if features["battery"] else "unavailable",
    }


_COMMAND_MODULE_LOADER = None


def _same_absolute_path(left, right) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def _load_trusted_command_module_loader():
    """Bootstrap the one shared loader without importing it by a shadowable name."""
    global _COMMAND_MODULE_LOADER
    if _COMMAND_MODULE_LOADER is not None:
        return _COMMAND_MODULE_LOADER
    module_name = "command_module_loader"
    try:
        if _NUITKA_RUNTIME:
            import command_module_loader as module
            runtime_info = globals().get("__compiled__")
            loader_info = getattr(module, "__compiled__", None)
            runtime_dir = getattr(runtime_info, "containing_dir", None)
            loader_dir = getattr(loader_info, "containing_dir", None)
            if (
                loader_info is None
                or not isinstance(runtime_dir, (str, os.PathLike))
                or not isinstance(loader_dir, (str, os.PathLike))
                or not _same_absolute_path(runtime_dir, loader_dir)
            ):
                raise ImportError("Shared command loader is not compiled into the active Nuitka bundle")
            _COMMAND_MODULE_LOADER = module
            return module
        if _BUNDLED_RUNTIME:
            runtime = sys.modules.get("pyimod02_importers")
            finder_type = getattr(runtime, "PyiFrozenFinder", None)
            loader_type = getattr(runtime, "PyiFrozenLoader", None)
            archive = getattr(runtime, "pyz_archive", None)
            meipass = getattr(sys, "_MEIPASS", None)
            if (
                not isinstance(finder_type, type)
                or finder_type.__name__ != "PyiFrozenFinder"
                or finder_type.__module__ != "pyimod02_importers"
                or not isinstance(loader_type, type)
                or loader_type.__name__ != "PyiFrozenLoader"
                or loader_type.__module__ != "pyimod02_importers"
                or archive is None
                or not isinstance(getattr(archive, "toc", None), dict)
                or not isinstance(meipass, (str, os.PathLike))
                or not os.fspath(meipass)
            ):
                raise ImportError("Trusted PyInstaller loader is unavailable")
            finder = finder_type(os.fspath(meipass))
            spec = finder.find_spec(module_name)
            loader = getattr(spec, "loader", None)
            expected_origin = Path(meipass) / f"{module_name}.py"
            if (
                type(finder) is not finder_type
                or getattr(finder, "_pyz_archive", None) is not archive
                or not _same_absolute_path(getattr(finder, "_path", ""), meipass)
                or getattr(spec, "name", None) != module_name
                or type(loader) is not loader_type
                or getattr(loader, "name", None) != module_name
                or getattr(loader, "_pyz_archive", None) is not archive
                or getattr(loader, "_pyz_entry_name", None) != module_name
                or getattr(loader, "_is_package", None) is not False
                or module_name not in archive.toc
                or not getattr(loader, "path", None)
                or not getattr(spec, "origin", None)
                or not _same_absolute_path(loader.path, expected_origin)
                or not _same_absolute_path(spec.origin, expected_origin)
                or getattr(spec, "submodule_search_locations", None) is not None
            ):
                raise ImportError("Shared command loader is not bundled in the active PYZ")
        else:
            root = _command_approval_source_root().resolve(strict=True)
            expected = root / f"{module_name}.py"
            if expected.is_symlink() or not expected.is_file() or not _same_absolute_path(expected.resolve(strict=True), expected):
                raise ImportError("Shared command loader is missing or outside XGENT-MCS")
            spec = importlib.util.spec_from_file_location(module_name, expected)
            if (
                spec is None
                or spec.name != module_name
                or type(spec.loader) is not importlib.machinery.SourceFileLoader
                or not spec.origin
                or not _same_absolute_path(spec.origin, expected)
                or not _same_absolute_path(spec.loader.get_filename(module_name), expected)
            ):
                raise ImportError("Shared command loader has an unexpected source spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMMAND_MODULE_LOADER = module
        return module
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError("Shared command loader could not be trusted") from exc


def _load_command_approval_component(module_name: str):
    """Load only the canonical MCS verifier/replay helpers; no crypto copy here."""
    return _load_trusted_command_module_loader().load_component(
        module_name,
        source_root=_command_approval_source_root(),
        frozen=_BUNDLED_RUNTIME,
    )


def _frozen_update_operation_id(payload, context_id=None):
    """Keep rejection correlation available without importing source updater modules."""
    for candidate in (payload.get("id"), context_id, payload.get("operation_id")):
        if isinstance(candidate, str) and 1 <= len(candidate) <= 128:
            return candidate
    return str(uuid.uuid4())


def _command_approval_source_root():
    """Use the transaction-owned helper set, or the initial checkout's shared set."""
    windows = Path(__file__).resolve().parent
    candidate = windows / "command_module_loader.py"
    if candidate.exists() or candidate.is_symlink():
        return windows
    return windows.parent / "XGENT-MCS"


def _shell_approval_runtime(client):
    """Load the canonical MCS verifier/replay helper lazily; no crypto copy here."""
    try:
        signing = _load_command_approval_component("command_signing")
        replay = _load_command_approval_component("command_replay")
        _load_command_approval_component("shell_execution")
        ledger = getattr(client, "_command_replay_ledger", None)
        if ledger is None:
            ledger = replay.CommandReplayLedger()
            client._command_replay_ledger = ledger
        return signing, ledger
    except Exception:
        return None, None


def _command_approval_protocol_enabled(client) -> bool:
    """Advertise schema 1 only when its public keyset and replay store are usable."""
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        return signing.trusted_command_keys_ready() and ledger.ensure_available(now=int(time.time()))
    except Exception:
        return False


def _shell_runner_diagnostic(client) -> dict:
    """Report runner prerequisites from local metadata only; do not execute shell commands."""
    protocol_state = "ready" if _command_approval_protocol_enabled(client) else "unavailable"
    try:
        execution = _load_command_approval_component("shell_execution")
        executor_state = "available" if callable(getattr(execution, "execute_shell", None)) else "unavailable"
    except Exception:
        execution = None
        executor_state = "unavailable"
    if PLATFORM == "macOS":
        runner_path = "/bin/sh"
    else:
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        expected = str(Path(system_root) / "System32" / "cmd.exe")
        configured = os.environ.get("COMSPEC")
        runner_path = expected if not configured or os.path.normcase(os.path.abspath(configured)) == os.path.normcase(os.path.abspath(expected)) else ""
    if execution is None or not callable(getattr(execution, "inspect_shell_runner", None)):
        runner = {"runner_state": "unknown", "permission_state": "unknown"}
    else:
        runner = execution.inspect_shell_runner(runner_path)
    return {
        "schema": 1,
        "protocol_state": protocol_state,
        "executor_state": executor_state,
        "runner_state": runner["runner_state"],
        "permission_state": runner["permission_state"],
    }


def _reserve_shell_approval(client, payload: dict) -> bool:
    """Verify exact command bytes and persist the signed request ID before execution."""
    if type(payload) is not dict:
        return False
    command = payload.get("command")
    envelope = payload.get("approval")
    if not isinstance(command, str) or type(envelope) is not dict:
        return False
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        now = int(time.time())
        verified = signing.verify_approved_command(
            envelope,
            command=command,
            expected_device_id=DEVICE_ID,
            now=now,
        )
        if verified is None:
            return False
        return ledger.reserve(verified["request_id"], verified["expires_at"], now=now)
    except Exception:
        return False


def _capability_snapshot(feature_status: dict[str, str]) -> dict:
    """Passive diagnostics only: prerequisites are never an OS permission grant."""
    items = []
    for capability_id, legacy_id, settings_uri in (
        ("screen_recording", "screenshot", None),
        ("camera", "webcam", "ms-settings:privacy-webcam"),
        ("microphone", "microphone", "ms-settings:privacy-microphone"),
        ("location", "geolocation", "ms-settings:privacy-location"),
        ("accessibility", None, None),
        ("file_access", None, None),
    ):
        prerequisite = feature_status.get(legacy_id) if legacy_id else None
        state = "needs_live_test"
        detail = "Permission cannot be queried directly; only an owner-authorized trial action confirms access."
        if prerequisite in {"dependency_missing", "unavailable"}:
            state = "unavailable"
            detail = "Required local tool or library is unavailable; OS permission was not queried."
        elif legacy_id and prerequisite not in {"permission_unverified", "device_unverified", "approximate"}:
            state = "unknown"
            detail = "Diagnostic prerequisites could not be determined; no permission grant inferred."
        if capability_id == "location":
            from location_adapter import location_capability
            state, detail = location_capability()
        elif capability_id == "accessibility":
            detail = ("A macOS-style TCC Accessibility permission is unsupported on Windows; "
                      "interactive session, integrity level and UAC may limit UI control. No UAC auto-consent.")
        elif capability_id == "file_access":
            detail = ("A blanket TCC file permission is unsupported on Windows; access is path-specific "
                      "and depends on ACL/UAC. Only an authorized trial confirms access.")
        elif capability_id == "screen_recording" and state == "needs_live_test":
            detail = ("A TCC screen recording grant is unsupported on Windows; capture depends on "
                      "the interactive session and a permitted trial action.")
        items.append({"id": capability_id, "state": state, "detail": detail, "settings_uri": settings_uri})
    return {"schema": 1, "platform": PLATFORM,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "items": items}


def ctypes_windll_user32_message_box(text: str) -> None:
    """Обертка над MessageBoxW для тестируемости."""
    ctypes.windll.user32.MessageBoxW(None, text, "XGENT", 0x40)


def setup_logging() -> None:
    r"""Лог в %USERPROFILE%\.xgent\xgent.log (с ротацией) + консоль при наличии."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        RotatingFileHandler(
            CONFIG_DIR / "xgent.log",
            maxBytes=500_000,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if "--console" in sys.argv:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    setup_logging()
    if not acquire_instance_lock():
        log.info("X-LOCK: другой экземпляр Windows-агента уже запущен; второй не стартует")
        return
    client = XgentClient()
    if EARLY_UPDATE_STATE == "pending":
        def _rollback_if_unhealthy() -> None:
            if client._update_health.wait(120):
                return
            with client._subscription_lock:
                if client._update_health.is_set() or not client._running.is_set():
                    return
                state_path = CONFIG_DIR / UPDATE_STATE_NAME
                if not state_path.exists():
                    log.error("Нет журнала обновления и нет MQTT health подтверждения; откат невозможен")
                    return
                try:
                    rollback_unhealthy_agent(state_path, Path(__file__).resolve().parent)
                except Exception:
                    log.exception("Критическая ошибка: откат обновления Windows-агента не завершён")
                    return
                log.error("Новая версия не подключилась к MQTT за 120 секунд; возвращаю предыдущие файлы")
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

        threading.Thread(
            target=_rollback_if_unhealthy,
            name="xgent-wds-update-health",
            daemon=True,
        ).start()
    if "--console" in sys.argv or "--no-tray" in sys.argv:
        log.info("Запуск XGENT в консольном режиме")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            client.request_stop()
        finally:
            client.stop()
        return

    try:
        import pystray
        from pystray import Menu, MenuItem
    except ImportError:
        log.warning("pystray недоступен — запуск без значка в трее")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            client.request_stop()
        finally:
            client.stop()
        return

    icon = pystray.Icon(
        "xgent-wds",
        create_image(),
        "XGENT",
        menu=Menu(
            MenuItem("🟢 XGENT: онлайн", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Остановить XGENT", lambda *_: client.request_stop()),
        ),
    )
    client.on_stop_requested = icon.stop
    client.start()
    icon.run()
    client.stop()


if __name__ == "__main__":
    main()
