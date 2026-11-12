"""Windows volume topology with bounded, non-reversible local identifiers."""

from __future__ import annotations

import hashlib
import json
import logging


def read_volume_topology_snapshot(*, find_executable, run_without_console,
                                  subprocess_module, logger: logging.Logger) -> dict:
    """Map bounded Windows volume mount aliases to non-reversible local keys."""
    source = "windows_get_volume_partition_access_paths"
    unknown = {"state": "unknown", "source": source, "volumes": []}
    powershell = find_executable("powershell.exe")
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
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
        logger.info("Windows volume topology unavailable", exc_info=True)
        return unknown
