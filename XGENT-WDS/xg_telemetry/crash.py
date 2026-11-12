"""Privacy-bounded Windows crash-event aggregates."""

from __future__ import annotations

import json
import logging


def read_crash_summary_snapshot(*, find_executable, run_without_console,
                                subprocess_module, logger: logging.Logger) -> dict:
    """Aggregate recent critical/error events locally; never serialize event text."""
    unknown = {"state": "unknown", "events": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows crash summary unavailable", exc_info=True)
        return unknown
