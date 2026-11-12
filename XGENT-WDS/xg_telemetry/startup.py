"""Privacy-bounded Windows startup entry inventory."""

from __future__ import annotations

import json
import logging


def read_startup_inventory_snapshot(*, find_executable, run_without_console,
                                    subprocess_module, logger: logging.Logger) -> dict:
    """Project startup entry names/scopes only; never export command values or paths."""
    unknown = {"state": "unknown", "entries": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows startup inventory unavailable", exc_info=True)
        return unknown
