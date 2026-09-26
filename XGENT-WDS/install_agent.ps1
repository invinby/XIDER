[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$TaskName = 'XIDER Agent'
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

if (Test-Path -LiteralPath $exePath) {
    $action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $AgentDir
} elseif ((Test-Path -LiteralPath $pythonw) -and (Test-Path -LiteralPath $scriptPath)) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $scriptPath) -WorkingDirectory $AgentDir
} else {
    throw 'Neither XGENT-WDS.exe nor venv\Scripts\pythonw.exe + xgent_wds.py was found.'
}

# Keep the sidecar env readable only by the account that runs this agent.
icacls $envPath /inheritance:r | Out-Null
icacls $envPath /grant:r "$($env:USERNAME):R" | Out-Null

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER device agent (MQTT/TLS)' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] $TaskName installed and started in the background."
Write-Host "[OK] To stop it: schtasks /End /TN `"$TaskName`""

$guardianInstaller = Join-Path $AgentDir 'install_guardian.ps1'
if (Test-Path -LiteralPath $guardianInstaller) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $guardianInstaller -AgentDir $AgentDir
    if ($LASTEXITCODE -ne 0) { throw 'Windows Guardian installation failed.' }
}
