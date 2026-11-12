"""Installed application inventory projection for Windows."""

from __future__ import annotations

import json
import logging


def read_installed_app_inventory_snapshot(*, find_executable, run_without_console,
                                          subprocess_module, logger: logging.Logger) -> dict:
    """Read bounded uninstall registry labels; omit paths and commands."""
    unknown = {"state": "unknown", "apps": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows installed-app inventory unavailable", exc_info=True)
        return unknown
