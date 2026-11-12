"""Windows storage reliability counters with identifiers removed."""

from __future__ import annotations

import json
import logging


def read_smart_trend_snapshot(*, find_executable, run_without_console,
                              subprocess_module, logger: logging.Logger) -> dict:
    """Return bounded reliability counters without exporting disk IDs."""
    unknown = {"state": "unknown", "source": "windows_storage_reliability_counters", "disks": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows SMART trend unavailable", exc_info=True)
        return unknown
