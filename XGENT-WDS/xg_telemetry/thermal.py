"""Windows ACPI thermal-zone telemetry."""

from __future__ import annotations

import json
import logging
import math


def read_thermal_snapshot(*, source: str, find_executable, run_without_console,
                           subprocess_module, logger: logging.Logger) -> dict:
    """Read ACPI thermal-zone values when Windows exposes the WMI provider."""
    powershell = find_executable("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": source, "sensors": []}
    script = (
        "$rows = @(Get-CimInstance -Namespace root/wmi -ClassName "
        "MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
        "Select-Object -First 32 -Property CurrentTemperature); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    try:
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return {"state": "unknown", "source": source, "sensors": []}
        try:
            raw = json.loads(completed.stdout or "null")
        except (TypeError, json.JSONDecodeError):
            return {"state": "unknown", "source": source, "sensors": []}
        rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        if not rows:
            return {"state": "unsupported", "source": source, "sensors": []}
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
            return {"state": "unknown", "source": source, "sensors": []}
        return {"state": "supported", "source": source, "sensors": sensors}
    except Exception:
        logger.info("Windows ACPI thermal-zone telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "sensors": []}
