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

$envLines = @([IO.File]::ReadAllLines($envPath))
$settings = @{}
foreach ($name in @(
    'SHARED_KEY', 'MQTT_BROKER', 'MQTT_PORT', 'MQTT_PREFIX',
    'MQTT_TLS', 'MQTT_USERNAME', 'MQTT_PASSWORD', 'ENCRYPT_PAYLOAD'
)) {
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($name) + '\s*=\s*(.*)$'
    $matchingLines = @($envLines | Where-Object { $_ -match $pattern })
    if ($matchingLines.Count -ne 1) {
        throw "Required setting $name is missing or duplicated; no Scheduled Task was changed."
    }
    $value = [regex]::Match([string]$matchingLines[0], $pattern).Groups[1].Value.Trim().Trim('"', "'").Trim()
    if (-not $value -or $value.StartsWith('#')) {
        throw "Required setting $name is empty; no Scheduled Task was changed."
    }
    $settings[$name] = $value
}
if ($settings['SHARED_KEY'] -eq 'XGENT-2026-shared-secret') {
    throw 'The public test SHARED_KEY is not allowed; no Scheduled Task was changed.'
}
if ($settings['MQTT_TLS'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
    throw 'MQTT_TLS=true is required; no Scheduled Task was changed.'
}
if ($settings['ENCRYPT_PAYLOAD'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
    throw 'ENCRYPT_PAYLOAD=true is required; no Scheduled Task was changed.'
}
if ($settings['MQTT_PREFIX'] -notmatch '^[A-Za-z0-9._/-]+$') {
    throw 'MQTT_PREFIX has an unsupported format; no Scheduled Task was changed.'
}
$mqttPort = 0
if (-not [int]::TryParse($settings['MQTT_PORT'], [ref]$mqttPort) -or $mqttPort -lt 1 -or $mqttPort -gt 65535) {
    throw 'MQTT_PORT must be between 1 and 65535; no Scheduled Task was changed.'
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
# Guardian is the sole worker-recovery owner. A second Task Scheduler restart
# policy races an intentional tray stop and can revive the worker after it was
# explicitly stopped.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER device agent (MQTT/TLS)' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] $TaskName installed and started in the background."
Write-Host "[OK] To stop it: schtasks /End /TN `"$TaskName`""

if (Test-Path -LiteralPath $guardianInstaller) {
    & $guardianInstaller -AgentDir $AgentDir -TaskName 'XIDER Guardian'
}
