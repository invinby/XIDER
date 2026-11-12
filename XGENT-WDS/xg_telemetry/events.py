"""Bounded Windows startup-duration and sleep/wake event snapshots."""

from __future__ import annotations

import logging


def read_startup_duration_snapshot(*, source: str, find_executable, run_without_console,
                                   subprocess_module, parse_output, logger: logging.Logger) -> dict:
    """Read only the measured BootTime field from the latest event 100."""
    powershell = find_executable("powershell.exe")
    unknown = {"state": "unknown", "source": source, "duration_ms": None, "completed_at": None}
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
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 32 * 1024:
            return unknown
        return parse_output(completed.stdout)
    except Exception:
        logger.info("Windows measured boot duration unavailable", exc_info=True)
        return unknown


def read_sleep_wake_history_snapshot(*, source: str, find_executable, run_without_console,
                                     subprocess_module, parse_output, logger: logging.Logger) -> dict:
    """Read only allowlisted sleep/resume event IDs from the System log."""
    powershell = find_executable("powershell.exe")
    if not powershell:
        return {"state": "unsupported", "source": source, "history_days": 7, "events": []}
    script = (
        "$start=(Get-Date).AddDays(-7); "
        "$kp=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=@(42,107);StartTime=$start} -MaxEvents 60 -ErrorAction SilentlyContinue | "
        "Select-Object -Property Id,ProviderName,@{Name='TimeCreated';Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}}); "
        "$pt=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Power-Troubleshooter';Id=1;StartTime=$start} -MaxEvents 60 -ErrorAction SilentlyContinue | "
        "Select-Object -Property Id,ProviderName,@{Name='TimeCreated';Expression={$_.TimeCreated.ToUniversalTime().ToString('o')}}); "
        "$rows=@($kp)+@($pt); ConvertTo-Json -InputObject $rows -Depth 2 -Compress"
    )
    try:
        completed = run_without_console(
            subprocess_module.run,
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str) or len(completed.stdout) > 1024 * 1024:
            return {"state": "unknown", "source": source, "history_days": 7, "events": []}
        return parse_output(completed.stdout)
    except Exception:
        logger.info("Windows sleep/wake telemetry unavailable", exc_info=True)
        return {"state": "unknown", "source": source, "history_days": 7, "events": []}
