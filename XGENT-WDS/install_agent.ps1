[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$TaskName = 'XIDER Agent',
    [switch]$PreferPython,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$AgentDir = (Resolve-Path $AgentDir).Path
$envPath = Join-Path $AgentDir '.env'
$exePath = Join-Path $AgentDir 'XGENT-WDS.exe'
$pythonw = Join-Path $AgentDir 'venv\Scripts\pythonw.exe'
$scriptPath = Join-Path $AgentDir 'xgent_wds.py'

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing $envPath. Copy .env.example to .env and fill the MQTT credentials first."
}

if ($PreferPython) {
    if (-not ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $scriptPath))) {
        throw 'Python agent requested, but venv\Scripts\pythonw.exe or xgent_wds.py is missing.'
    }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $scriptPath) -WorkingDirectory $AgentDir
} elseif (Test-Path -LiteralPath $exePath) {
    $action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $AgentDir
} elseif ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $scriptPath)) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $scriptPath) -WorkingDirectory $AgentDir
} else {
    throw 'Neither XGENT-WDS.exe nor venv\Scripts\pythonw.exe + xgent_wds.py was found.'
}

$guardianInstaller = Join-Path $AgentDir 'install_guardian.ps1'
if (-not (Test-Path -LiteralPath $guardianInstaller -PathType Leaf)) {
    throw 'Windows Guardian installer is missing.'
}
if ($PreflightOnly) {
    # Validate both scheduled-task payloads before setup-all is allowed to
    # mutate the remote VPS. The installer in preflight mode must be read-only.
    & $guardianInstaller -AgentDir $AgentDir -PreflightOnly
    Write-Host '[OK] Agent and Guardian payloads are present. No task or ACL was changed.'
    return
}

# Keep the sidecar env readable only by the account that runs this agent.
icacls $envPath /inheritance:r | Out-Null
icacls $envPath /grant:r "$($env:USERNAME):R" | Out-Null

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER device agent (MQTT/TLS)' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] $TaskName installed and started in the background."
Write-Host "[OK] To stop it: schtasks /End /TN `"$TaskName`""

if (Test-Path -LiteralPath $guardianInstaller) {
    & $guardianInstaller -AgentDir $AgentDir -TaskName 'XIDER Guardian'
}
