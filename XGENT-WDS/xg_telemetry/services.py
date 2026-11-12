"""Privacy-bounded Windows service inventory."""

from __future__ import annotations

import json
import logging


def read_service_inventory_snapshot(*, find_executable, run_without_console,
                                    subprocess_module, logger: logging.Logger) -> dict:
    """Read bounded Win32_Service status fields without binary paths or accounts."""
    unknown = {"state": "unknown", "services": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows service inventory unavailable", exc_info=True)
        return unknown
