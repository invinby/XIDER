"""Windows storage health command handlers."""

from __future__ import annotations

import json
import subprocess


def storage_smart(*, subprocess_module, run_without_console, publish_response, device_id) -> None:
    """Read disk health and available reliability counters through PowerShell."""
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
        out = run_without_console(subprocess_module.check_output,
            ["powershell", "-NoProfile", "-Command", ps],
            text=True, stderr=subprocess_module.DEVNULL, timeout=20,
        ).strip()
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        if not data:
            text, ok = "🩺 Накопители не найдены.", True
        else:
            lines = []
            for disk in data:
                temp = disk.get("Temp")
                wear = disk.get("Wear")
                lines.append(
                    f"• {disk.get('FriendlyName')} [{disk.get('MediaType')}] — "
                    f"{disk.get('Health')}"
                    + (f", {temp}°C" if temp is not None else "")
                    + (f", износ {wear}%" if wear is not None else "")
                )
            text, ok = "🩺 SMART и здоровье накопителей:\n" + "\n".join(lines), True
    except Exception as exc:
        text, ok = f"⚠️ SMART: {exc}", False
    publish_response("storage_smart", {
        "type": "storage_smart", "device_id": device_id, "ok": ok, "text": text,
    })
