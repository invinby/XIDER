"""Windows battery capacity and cycle-count telemetry."""

from __future__ import annotations

import json
import logging
import math


def read_battery_health_snapshot(*, source: str, find_executable, run_without_console,
                                 subprocess_module, logger: logging.Logger) -> dict:
    """Compare WMI design/full-charge capacities without current charge percent."""
    powershell = find_executable("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": source, "batteries": []}
    script = (
        "$design = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,DesignedCapacity); "
        "$full = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,FullChargedCapacity); "
        "ConvertTo-Json -InputObject @{design=$design;full=$full} -Depth 3 -Compress"
    )
    try:
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": source, "batteries": []}
        raw = json.loads(completed.stdout or "null")
        if not isinstance(raw, dict):
            return {"state": "unknown", "source": source, "batteries": []}
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
        return {"state": state, "source": source, "batteries": batteries}
    except Exception:
        logger.info("Windows battery capacity telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "batteries": []}


def read_battery_cycle_snapshot(*, source: str, find_executable, run_without_console,
                                subprocess_module, logger: logging.Logger) -> dict:
    """Read cycle counts only when the Windows WMI provider exposes a positive counter."""
    powershell = find_executable("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": source, "batteries": []}
    script = (
        "$rows = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount "
        "-ErrorAction SilentlyContinue | Select-Object -First 8 -Property InstanceName,CycleCount); "
        "ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": source, "batteries": []}
        raw = json.loads(completed.stdout or "null")
        if raw is None or raw == []:
            return {"state": "unsupported", "source": source, "batteries": []}
        source_rows = raw if isinstance(raw, list) else [raw]
        rows = [row for row in source_rows[:8] if isinstance(row, dict) and isinstance(row.get("InstanceName"), str)]
        if not rows:
            return {"state": "unknown", "source": source, "batteries": []}
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
        return {"state": state, "source": source, "batteries": batteries}
    except Exception:
        logger.info("Windows battery cycle telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "batteries": []}
